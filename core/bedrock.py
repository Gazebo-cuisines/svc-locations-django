"""
Bedrock agent runtime.

The action group runs in RETURN_CONTROL mode: Bedrock hands the tool call back
to us instead of a Lambda, we run it in-process against our own endpoints and
send the result on the next InvokeAgent call. No Lambda, no public callback URL.

Ledger questions: load GET /stock/investigate/ first, then reason only over
that JSON (Converse). Numbers come from the ledger, not the model.
"""

import json
import os
import re
import uuid
from datetime import timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import boto3
from django.conf import settings
from django.utils import timezone

from core.ai_tools import run_tool

MAX_TOOL_HOPS = 6
UK = ZoneInfo('Europe/London')
_LEDGER_HINT = re.compile(
    r'\b(E\d+|stock|bag|scan|ledger|goods.?out|spice|trace|fifo|'
    r'sticker|barcode|powder|ingredient|queue|plan)\b',
    re.I,
)
_REASON_SYSTEM = (
    'You are Gazebo warehouse support. Answer ONLY from FINDINGS JSON. '
    'Use briefing as the body. You may add one "Finding:" line that restates '
    'decision.kind in plain English for the user question. '
    'Never invent entry codes, quantities, times, or bags. '
    'If a field is missing, say the ledger does not have it.'
)


def _setting(name: str, default: str = '') -> str:
    return os.getenv(name) or getattr(settings, name, '') or default


def _aws_session():
    profile = _setting('AWS_PROFILE')
    try:
        return boto3.Session(profile_name=profile) if profile else boto3.Session()
    except Exception:
        return boto3.Session()


def _region() -> str:
    return _setting('BEDROCK_REGION') or _setting('AWS_DEFAULT_REGION', 'eu-west-2')


@lru_cache(maxsize=1)
def agent_runtime_client():
    return _aws_session().client('bedrock-agent-runtime', region_name=_region())


@lru_cache(maxsize=1)
def bedrock_runtime_client():
    return _aws_session().client('bedrock-runtime', region_name=_region())


def _read_stream(response) -> tuple[str, dict | None]:
    """Drain the event stream into (answer text, return-control payload)."""
    answer = []
    control = None
    for event in response.get('completion', []):
        chunk = event.get('chunk')
        if chunk and chunk.get('bytes'):
            answer.append(chunk['bytes'].decode('utf-8', 'replace'))
        if event.get('returnControl'):
            control = event['returnControl']
    return ''.join(answer), control


def _tool_results(control: dict, auth_header: str, calls: list) -> list[dict]:
    results = []
    for item in control.get('invocationInputs', []):
        api_input = item.get('apiInvocationInput') or {}
        api_path = api_input.get('apiPath', '')
        params = {
            p.get('name'): p.get('value')
            for p in api_input.get('parameters', [])
        }
        body = run_tool(api_path, params, auth_header=auth_header)
        calls.append({'tool': api_path, 'params': params})
        results.append({
            'apiResult': {
                'actionGroup': api_input.get('actionGroup', ''),
                'apiPath': api_path,
                'httpMethod': api_input.get('httpMethod', 'GET'),
                'httpStatusCode': 200 if body.get('status') == 'success' else 400,
                'responseBody': {
                    'application/json': {'body': json.dumps(body, default=str)},
                },
            },
        })
    return results


def params_from_question(message: str) -> dict:
    """Pull investigate query params from a floor question."""
    text = (message or '').strip()
    params = {}
    code = re.search(r'\bE\d+\b', text, re.I)
    if code:
        params['code'] = code.group(0)
    else:
        product = re.search(r'\bP\d+\b', text, re.I)
        if product:
            params['code'] = product.group(0)
        else:
            recipe = re.search(r'\b[A-Z]{3,}\d+[A-Z0-9-]*\b', text)
            if recipe:
                params['recipe_code'] = recipe.group(0)
            elif _LEDGER_HINT.search(text):
                q = re.sub(
                    r'\b(what|happened|with|the|item|why|scan|scanned|'
                    r'yesterday|today|please|check|bag|barcode)\b',
                    ' ',
                    text,
                    flags=re.I,
                )
                q = ' '.join(q.split())
                if q:
                    params['q'] = q[:120]
    if re.search(r'\byesterday\b', text, re.I) and params:
        params['date'] = (
            timezone.now().astimezone(UK).date() - timedelta(days=1)
        ).isoformat()
    return params


def load_findings(message: str, *, auth_header: str = '') -> tuple[dict | None, dict]:
    params = params_from_question(message)
    if not params:
        return None, params
    body = run_tool('/stock/investigate/', params, auth_header=auth_header)
    if body.get('status') != 'success' or not body.get('data'):
        return None, params
    return body['data'], params


def reason_from_findings(question: str, findings: dict) -> str | None:
    model = _setting('BEDROCK_MODEL_ID')
    if not model:
        return None
    payload = json.dumps(
        {
            'briefing': findings.get('briefing'),
            'decision': findings.get('decision'),
            'bag': findings.get('bag'),
            'events': findings.get('events'),
            'balances': findings.get('balances'),
        },
        default=str,
    )
    try:
        response = bedrock_runtime_client().converse(
            modelId=model,
            system=[{'text': _REASON_SYSTEM}],
            messages=[{
                'role': 'user',
                'content': [{
                    'text': f'QUESTION:\n{question}\n\nFINDINGS:\n{payload}',
                }],
            }],
            inferenceConfig={'maxTokens': 800, 'temperature': 0},
        )
    except Exception:
        return None
    blocks = (response.get('output') or {}).get('message', {}).get('content') or []
    texts = [b.get('text') for b in blocks if b.get('text')]
    return '\n'.join(texts).strip() or None


def handle_chat(
    message: str,
    *,
    session_id: str = '',
    auth_header: str = '',
) -> dict:
    """Ledger findings first; model may only rephrase those rows."""
    session_id = session_id or uuid.uuid4().hex
    findings, params = load_findings(message, auth_header=auth_header)
    if findings:
        reasoned = reason_from_findings(message, findings)
        return {
            'session_id': session_id,
            'answer': reasoned or findings.get('briefing') or '',
            'findings': findings,
            'tool_calls': [{'tool': '/stock/investigate/', 'params': params}],
        }
    if params:
        return {
            'session_id': session_id,
            'answer': (
                'No ledger rows matched that bag or product. '
                'Scan the E-code on the bag and ask again.'
            ),
            'findings': None,
            'tool_calls': [{'tool': '/stock/investigate/', 'params': params}],
        }
    return invoke_agent(
        message, session_id=session_id, auth_header=auth_header,
    )


def invoke_agent(message: str, *, session_id: str = '', auth_header: str = '') -> dict:
    """Ask the Bedrock agent, running any tool calls it asks for."""
    agent_id = _setting('BEDROCK_AGENT_ID')
    alias_id = _setting('BEDROCK_AGENT_ALIAS_ID', 'TSTALIASID')
    if not agent_id:
        raise RuntimeError('BEDROCK_AGENT_ID is not configured.')

    session_id = session_id or uuid.uuid4().hex
    calls: list[dict] = []
    kwargs = {
        'agentId': agent_id,
        'agentAliasId': alias_id,
        'sessionId': session_id,
        'inputText': message,
        'sessionState': {
            'promptSessionAttributes': {
                'ledger': (
                    'Stock or bag questions: call GET /stock/investigate/. '
                    'Answer from data.briefing. Never invent codes or quantities.'
                ),
            },
        },
    }
    answer = ''
    for _ in range(MAX_TOOL_HOPS):
        text, control = _read_stream(agent_runtime_client().invoke_agent(**kwargs))
        answer += text
        if not control:
            break
        kwargs = {
            'agentId': agent_id,
            'agentAliasId': alias_id,
            'sessionId': session_id,
            'sessionState': {
                'invocationId': control.get('invocationId'),
                'returnControlInvocationResults': _tool_results(
                    control, auth_header, calls,
                ),
            },
        }
    return {'session_id': session_id, 'answer': answer.strip(), 'tool_calls': calls}
