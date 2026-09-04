"""Bedrock agent wiring: tool dispatch, schema, return-control loop."""

import json
from unittest import mock

from django.test import TestCase

from core.ai_tools import TOOLS, openapi_schema, run_tool
from core.bedrock import invoke_agent


class AiToolsTests(TestCase):
    def test_unknown_tool_is_rejected(self):
        body = run_tool('/nope/', {})
        self.assertEqual(body['status'], 'error')

    def test_missing_path_parameter_is_rejected(self):
        body = run_tool('/purchasing/pos/{po_id}/', {})
        self.assertEqual(body['status'], 'error')

    def test_tool_calls_the_real_endpoint(self):
        body = run_tool('/purchasing/pos/', {'status': 'draft'})
        self.assertEqual(body['status'], 'success')
        self.assertEqual(body['data']['results'], [])

    def test_schema_covers_every_tool(self):
        schema = openapi_schema()
        self.assertEqual(set(schema['paths']), set(TOOLS))
        po_params = schema['paths']['/purchasing/pos/{po_id}/']['get']['parameters']
        self.assertTrue(po_params[0]['required'])
        self.assertEqual(po_params[0]['in'], 'path')


def _stream(events):
    return {'completion': events}


class InvokeAgentTests(TestCase):
    @mock.patch('core.bedrock._setting')
    @mock.patch('core.bedrock.agent_runtime_client')
    def test_return_control_result_is_sent_back(self, client, setting):
        setting.side_effect = lambda name, default='': {
            'BEDROCK_AGENT_ID': 'AGENT1',
            'BEDROCK_AGENT_ALIAS_ID': 'ALIAS1',
        }.get(name, default)
        control = _stream([{
            'returnControl': {
                'invocationId': 'inv-1',
                'invocationInputs': [{
                    'apiInvocationInput': {
                        'actionGroup': 'gazebo',
                        'apiPath': '/purchasing/pos/',
                        'httpMethod': 'GET',
                        'parameters': [{'name': 'status', 'value': 'draft'}],
                    },
                }],
            },
        }])
        answer = _stream([{'chunk': {'bytes': b'No draft POs.'}}])
        client.return_value.invoke_agent.side_effect = [control, answer]

        result = invoke_agent('any draft POs?', session_id='s1')

        self.assertEqual(result['answer'], 'No draft POs.')
        self.assertEqual(
            result['tool_calls'],
            [{'tool': '/purchasing/pos/', 'params': {'status': 'draft'}}],
        )
        second = client.return_value.invoke_agent.call_args_list[1].kwargs
        api_result = (
            second['sessionState']['returnControlInvocationResults'][0]['apiResult']
        )
        self.assertEqual(api_result['httpStatusCode'], 200)
        self.assertEqual(
            json.loads(api_result['responseBody']['application/json']['body'])['status'],
            'success',
        )

    @mock.patch('core.bedrock._setting', return_value='')
    def test_missing_agent_id_raises(self, _setting):
        with self.assertRaises(RuntimeError):
            invoke_agent('hello')


class AiChatViewTests(TestCase):
    def test_chat_requires_auth(self):
        response = self.client.post(
            '/ai/chat/',
            data=json.dumps({'message': 'hi'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
