"""Bedrock agent wiring: tool dispatch, schema, return-control loop."""

import json
from unittest import mock

from django.test import TestCase

from core.ai_tools import TOOLS, openapi_schema, run_tool
from core.bedrock import handle_chat, invoke_agent, params_from_question


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
        for path in (
            '/stock/investigate/',
            '/stock/scan/goods-out/',
            '/stock/entries/{pk}/',
            '/stock/products/{product_id}/history/goods-out/',
        ):
            self.assertIn(path, schema['paths'])


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


class HandleChatTests(TestCase):
    def test_params_from_bag_code(self):
        self.assertEqual(
            params_from_question('what happened to E280 baking powder')['code'],
            'E280',
        )

    def test_params_from_recipe_and_yesterday(self):
        params = params_from_question('yesterday SPICE0-16 no stock')
        self.assertEqual(params['recipe_code'], 'SPICE0-16')
        self.assertIn('date', params)

    def test_params_from_product_name(self):
        params = params_from_question('what happened with baking powder')
        self.assertEqual(params.get('q'), 'baking powder')

    @mock.patch('core.bedrock.reason_from_findings', return_value=None)
    @mock.patch('core.bedrock.run_tool')
    def test_chat_answers_from_investigate_briefing(self, run_tool, _reason):
        run_tool.return_value = {
            'status': 'success',
            'data': {
                'briefing': 'E280 is empty now. Warehouse BAKING POWDER is not missing.',
                'decision': {'kind': 'bag_empty'},
            },
        }
        result = handle_chat('what happened to E280', session_id='s1')
        self.assertEqual(
            result['answer'],
            'E280 is empty now. Warehouse BAKING POWDER is not missing.',
        )
        self.assertEqual(result['findings']['decision']['kind'], 'bag_empty')
        self.assertEqual(
            result['tool_calls'],
            [{'tool': '/stock/investigate/', 'params': {'code': 'E280'}}],
        )

    @mock.patch('core.bedrock.reason_from_findings')
    @mock.patch('core.bedrock.run_tool')
    def test_chat_uses_model_reason_over_findings(self, run_tool, reason):
        run_tool.return_value = {
            'status': 'success',
            'data': {
                'briefing': 'E280 is empty now.',
                'decision': {'kind': 'bag_empty'},
            },
        }
        reason.return_value = (
            'Finding: that bag is empty, not a warehouse hole.\n\n'
            'E280 is empty now.'
        )
        result = handle_chat('why no stock in bag E280')
        self.assertIn('Finding:', result['answer'])
        self.assertEqual(result['findings']['decision']['kind'], 'bag_empty')

    @mock.patch('core.bedrock.run_tool')
    def test_chat_miss_does_not_invent(self, run_tool):
        run_tool.return_value = {
            'status': 'error',
            'message': 'code=E1 not found',
            'data': None,
        }
        result = handle_chat('what happened to E1')
        self.assertIn('No ledger rows', result['answer'])
        self.assertIsNone(result['findings'])


class AiChatViewTests(TestCase):
    def test_chat_requires_auth(self):
        response = self.client.post(
            '/ai/chat/',
            data=json.dumps({'message': 'hi'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
