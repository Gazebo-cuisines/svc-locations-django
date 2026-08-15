"""Navbar / global search across products, POs, parties, and lots."""

from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from locations.models import Location, LocationRole
from locations.utils.api_response import api_error, api_success
from product.query import active_products
from users_rbac.auth import require_auth
from purchasing.models import PurchaseOrder
from stock_ledger.models import StockLot
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.scan import resolve_scan

HIT_LIMIT = 10
MIN_LEN = 2


def _scan_hit(q: str) -> dict | None:
    if ' ' in q:
        return None
    try:
        match = resolve_scan(q)
    except StockValidationError:
        return None
    product = match['product']
    lot = match.get('lot')
    return {
        'type': 'scan',
        'id': product.id,
        'label': product.name,
        'match_type': match['match_type'],
        'lot_id': lot.id if lot is not None else None,
        'code': q,
    }


@csrf_exempt
@require_GET
@require_auth
def global_search_api(request):
    q = (request.GET.get('q') or '').strip()
    if len(q) < MIN_LEN:
        return api_error('Type at least 2 characters to search.', status_code=400)

    results = []
    scan_hit = _scan_hit(q)
    if scan_hit is not None:
        results.append(scan_hit)

    for product in active_products(q=q)[:HIT_LIMIT]:
        results.append({
            'type': 'product',
            'id': product.id,
            'label': product.name,
            'goods_in_type': product.goods_in_type,
            'product_class': product.product_class.name,
        })

    po_filter = (
        Q(number__icontains=q)
        | Q(external_number__icontains=q)
        | Q(supplier__name__icontains=q)
        | Q(lines__product__name__icontains=q)
    )
    for po in (
        PurchaseOrder.objects.select_related('supplier')
        .filter(po_filter)
        .distinct()
        .order_by('-id')[:HIT_LIMIT]
    ):
        results.append({
            'type': 'purchase_order',
            'id': po.id,
            'label': po.number or f'PO{po.id}',
            'sage_po_number': po.external_number,
            'subtitle': po.supplier.name if po.supplier_id else None,
        })

    for role, type_name in (
        (LocationRole.SUPPLIER, 'supplier'),
        (LocationRole.CUSTOMER, 'customer'),
    ):
        for loc in (
            Location.objects.filter(
                visible=True,
                roles__role=role,
                name__icontains=q,
            ).distinct().order_by('name')[:HIT_LIMIT]
        ):
            results.append({'type': type_name, 'id': loc.id, 'label': loc.name})

    lot_filter = (
        Q(trace_number__icontains=q)
        | Q(supplier_lot_code__icontains=q)
        | Q(product__name__icontains=q)
    )
    for lot in (
        StockLot.objects.select_related('product')
        .filter(lot_filter)
        .order_by('-id')[:HIT_LIMIT]
    ):
        results.append({
            'type': 'stock',
            'id': lot.id,
            'label': lot.trace_number,
            'product_id': lot.product_id,
            'product_name': lot.product.name,
        })

    return api_success(
        'Search results fetched successfully.',
        {'q': q, 'results': results},
    )
