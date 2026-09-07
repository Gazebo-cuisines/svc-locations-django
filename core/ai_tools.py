"""
Read-only tools a Bedrock agent may call.

The agent never gets a second implementation of the domain: each tool is an
existing endpoint, dispatched in-process through the URL resolver, so filters,
permissions and serialisation stay in one place.
"""

import json

from django.test import RequestFactory
from django.urls import resolve

# apiPath -> tool. Keys match the OpenAPI paths given to the action group.
TOOLS: dict[str, dict] = {
    '/stock/balances/': {
        'summary': 'Stock on hand per lot and location.',
        'params': {
            'product_id': 'Filter to one product.',
            'location_id': 'Filter to one location.',
            'trace_number': 'Filter to one lot trace number.',
            'order': 'Set to fifo for oldest-first.',
            'include_zero': 'Set to 1 to include empty balances.',
        },
    },
    '/stock/warehouse/remaining/': {
        'summary': 'Remaining storage stock per product per unit.',
        'params': {'location_id': 'Filter to one storage unit.'},
    },
    '/stock/scan/': {
        'summary': 'Resolve a product, entry or unit code to its stock detail.',
        'params': {
            'code': 'Barcode or code to resolve.',
            'location_id': 'Restrict batches to one location.',
        },
    },
    '/stock/atp/': {
        'summary': 'Available to promise for a product.',
        'params': {'product_id': 'Product to check.'},
    },
    '/stock/recall/': {
        'summary': 'Trace where a lot or trace number ended up.',
        'params': {'trace_number': 'Trace number to recall.'},
    },
    '/purchasing/pos/': {
        'summary': 'List purchase orders.',
        'params': {
            'status': 'Comma separated statuses.',
            'supplier_id': 'Filter to one supplier.',
            'sage_po_number': 'Exact Sage PO number.',
        },
    },
    '/purchasing/pos/{po_id}/': {
        'summary': 'One purchase order with its lines.',
        'params': {'po_id': 'Purchase order id.'},
    },
    '/purchasing/deliveries/rejected/': {
        'summary': 'Deliveries rejected at goods in.',
        'params': {},
    },
    '/container/suppliers/': {
        'summary': 'List suppliers.',
        'params': {},
    },
    '/product/': {
        'summary': 'List products in the catalogue.',
        'params': {'search': 'Match on name or code.'},
    },
    '/search/': {
        'summary': 'Global search over products, POs, parties and scans.',
        'params': {'q': 'Search text.'},
    },
}

_factory = RequestFactory()


def run_tool(api_path: str, params: dict, *, auth_header: str = '') -> dict:
    """Call the endpoint behind api_path and return its parsed JSON body."""
    tool = TOOLS.get(api_path)
    if tool is None:
        return {'status': 'error', 'message': f'Unknown tool {api_path}.', 'data': None}

    params = {k: v for k, v in (params or {}).items() if v not in (None, '')}
    path = api_path
    query = {}
    for name, value in params.items():
        placeholder = '{%s}' % name
        if placeholder in path:
            path = path.replace(placeholder, str(value))
        else:
            query[name] = value
    if '{' in path:
        return {
            'status': 'error',
            'message': f'Missing path parameter for {api_path}.',
            'data': None,
        }

    headers = {'HTTP_AUTHORIZATION': auth_header} if auth_header else {}
    request = _factory.get(path, data=query, **headers)
    match = resolve(path)
    response = match.func(request, *match.args, **match.kwargs)
    if hasattr(response, 'render'):
        response.render()
    try:
        return json.loads(response.content or b'{}')
    except ValueError:
        return {
            'status': 'error',
            'message': 'Tool returned a non-JSON response.',
            'data': None,
        }


def openapi_schema(*, title: str = 'Gazebo stock and purchasing') -> dict:
    """OpenAPI 3 document to paste into the Bedrock agent action group."""
    paths = {}
    for api_path, tool in TOOLS.items():
        paths[api_path] = {
            'get': {
                'operationId': api_path.strip('/').replace('/', '_').replace(
                    '{', '',
                ).replace('}', ''),
                'summary': tool['summary'],
                'description': tool['summary'],
                'parameters': [
                    {
                        'name': name,
                        'in': 'path' if ('{%s}' % name) in api_path else 'query',
                        'required': ('{%s}' % name) in api_path,
                        'description': description,
                        'schema': {'type': 'string'},
                    }
                    for name, description in tool['params'].items()
                ],
                'responses': {
                    '200': {
                        'description': tool['summary'],
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'properties': {
                                        'status': {'type': 'string'},
                                        'message': {'type': 'string'},
                                        'data': {'type': 'object'},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
    return {
        'openapi': '3.0.0',
        'info': {
            'title': title,
            'version': '1.0.0',
            'description': (
                'Read-only stock, purchasing and product data for the '
                'Gazebo locations service.'
            ),
        },
        'paths': paths,
    }
