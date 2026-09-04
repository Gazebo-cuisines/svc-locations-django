"""
Bedrock agent runtime.

The action group runs in RETURN_CONTROL mode: Bedrock hands the tool call back
to us instead of a Lambda, we run it in-process against our own endpoints and
send the result on the next InvokeAgent call. No Lambda, no public callback URL.
"""

import json
import os
import uuid
from functools import lru_cache

import boto3
from django.conf import settings

from core.ai_tools import run_tool

MAX_TOOL_HOPS = 6


def _setting(name: str, default: str = '') -> str:
    return os.getenv(name) or getattr(settings, name, '') or default


@lru_cache(maxsize=1)
def agent_runtime_client():
    profile = _setting('AWS_PROFILE')
    region = _setting('BEDROCK_REGION') or _setting('AWS_DEFAULT_REGION', 'eu-west-2')
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    except Exception:
        session = boto3.Session()
    return session.client('bedrock-agent-runtime', region_name=region)


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
