from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from stock_ledger.models import StockBalance, StockEntry


def find_balance_drift() -> list[dict]:
    """
    Compare stock_balance.quantity to SUM(stock_entry.quantity) per lot/location.
    Returns drift rows (empty list means OK). Does not repair — alarm only.
    """
    ledger = {
        (row['lot_id'], row['location_id']): row['ledger_qty']
        for row in (
            StockEntry.objects
            .values('lot_id', 'location_id')
            .annotate(ledger_qty=Sum('quantity'))
        )
    }

    drifts: list[dict] = []
    seen: set[tuple[int, int]] = set()

    for balance in StockBalance.objects.all().iterator():
        key = (balance.lot_id, balance.location_id)
        seen.add(key)
        ledger_qty = ledger.get(key, Decimal('0'))
        if balance.quantity != ledger_qty:
            drifts.append({
                'lot_id': balance.lot_id,
                'location_id': balance.location_id,
                'balance_quantity': str(balance.quantity),
                'ledger_quantity': str(ledger_qty),
                'kind': 'mismatch',
            })

    for key, ledger_qty in ledger.items():
        if key in seen:
            continue
        if ledger_qty != 0:
            drifts.append({
                'lot_id': key[0],
                'location_id': key[1],
                'balance_quantity': None,
                'ledger_quantity': str(ledger_qty),
                'kind': 'missing_balance',
            })

    return drifts
