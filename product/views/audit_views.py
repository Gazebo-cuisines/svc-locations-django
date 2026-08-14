from django.views.decorators.http import require_GET

from locations.utils.api_response import api_error, api_success
from product.query import active_products
from product.models import Product, ProductAudit
from stock_ledger.models import StockFifoOverride
from stock_ledger.util.entry_labels import entry_code
from users_rbac.models import RbacUser


@require_GET
def product_timeline_api(request, pk: int):
    if not Product.objects.filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    row = ProductAudit.objects.filter(product_id=pk).first()
    events = []
    if row and isinstance(row.timeline_events, list):
        events = list(reversed(row.timeline_events))
    return api_success(
        'Product timeline fetched successfully.',
        events,
    )


@require_GET
def product_stock_overrides_api(request, pk: int):
    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)
    rows = list(
        StockFifoOverride.objects
        .select_related('scanned_lot', 'recommended_lot')
        .filter(product_id=pk)
        .order_by('-created_at', '-id')[:200]
    )
    user_ids = {row.actor_user_id for row in rows if row.actor_user_id}
    names = {
        u.id: (u.display_name or u.username)
        for u in RbacUser.objects.filter(pk__in=user_ids).only(
            'id', 'display_name', 'username',
        )
    }
    items = [
        {
            'who': names.get(row.actor_user_id) or row.lan_username,
            'scanned_trace': row.scanned_lot.trace_number,
            'recommended_trace': row.recommended_lot.trace_number,
            'reason': row.reason,
            'when': row.created_at.isoformat() if row.created_at else None,
            'entry_code': entry_code(row.stock_entry_id),
        }
        for row in rows
    ]
    return api_success('Stock overrides fetched successfully.', {'items': items})
