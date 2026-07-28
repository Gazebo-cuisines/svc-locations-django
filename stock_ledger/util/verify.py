from __future__ import annotations

from decimal import Decimal

from django.db import connection
from django.db.models import Sum

from stock_ledger.models import StockBalance, StockEntry, StockReservation, StockReservationStatus
from stock_ledger.util.balance import find_balance_drift

_CHAIN_SQL = """
WITH RECURSIVE chain AS (
  SELECT id, entry_hash, prev_hash FROM stock_entry WHERE prev_hash IS NULL
  UNION ALL
  SELECT e.id, e.entry_hash, e.prev_hash
  FROM stock_entry e JOIN chain c ON e.prev_hash = c.entry_hash
)
SELECT
  (SELECT COUNT(*) FROM stock_entry) AS total,
  COUNT(*) AS linked
FROM chain
"""

_ORPHAN_PREV_SQL = """
SELECT e.id, e.prev_hash
FROM stock_entry e
LEFT JOIN stock_entry p ON p.entry_hash = e.prev_hash
WHERE e.prev_hash IS NOT NULL AND p.id IS NULL
"""

_TRANSFER_IMBALANCE_SQL = """
SELECT transfer_group_id, SUM(quantity_base) AS net_base, SUM(quantity) AS net_qty
FROM stock_entry
WHERE transfer_group_id IS NOT NULL
GROUP BY transfer_group_id
HAVING SUM(quantity_base) <> 0 OR SUM(quantity) <> 0
"""


def check_chain_continuity() -> dict:
    """total entries must equal rows reachable from prev_hash IS NULL roots."""
    with connection.cursor() as cursor:
        cursor.execute(_CHAIN_SQL)
        total, linked = cursor.fetchone()
        cursor.execute(_ORPHAN_PREV_SQL)
        orphans = [{'id': r[0], 'prev_hash': r[1]} for r in cursor.fetchall()]

    ok = (total == linked) and not orphans
    return {
        'check': 'chain_continuity',
        'ok': ok,
        'total': total,
        'linked': linked,
        'orphans': orphans,
    }


def check_balance_invariant() -> dict:
    drifts = find_balance_drift()
    return {
        'check': 'balance_invariant',
        'ok': not drifts,
        'drift_count': len(drifts),
        'drifts': drifts,
    }


def check_transfer_atomicity() -> dict:
    with connection.cursor() as cursor:
        cursor.execute(_TRANSFER_IMBALANCE_SQL)
        cols = [c[0] for c in cursor.description]
        bad = [dict(zip(cols, row)) for row in cursor.fetchall()]
    return {
        'check': 'transfer_atomicity',
        'ok': not bad,
        'imbalanced_groups': bad,
    }


def check_reservation_overbook() -> dict:
    """Open reservations must not exceed on-hand balance per lot/location."""
    reserved = {
        (r['lot_id'], r['location_id']): r['qty']
        for r in (
            StockReservation.objects
            .filter(status=StockReservationStatus.OPEN)
            .values('lot_id', 'location_id')
            .annotate(qty=Sum('quantity'))
        )
    }
    balances = {
        (b.lot_id, b.location_id): b.quantity
        for b in StockBalance.objects.all().only('lot_id', 'location_id', 'quantity')
    }
    over: list[dict] = []
    for key, open_qty in reserved.items():
        on_hand = balances.get(key, Decimal('0'))
        if open_qty > on_hand:
            over.append({
                'lot_id': key[0],
                'location_id': key[1],
                'open_reserved': str(open_qty),
                'on_hand': str(on_hand),
            })
    return {
        'check': 'reservation_overbook',
        'ok': not over,
        'overbooked': over,
    }


def run_all_verifications() -> dict:
    results = [
        check_chain_continuity(),
        check_balance_invariant(),
        check_transfer_atomicity(),
        check_reservation_overbook(),
    ]
    return {
        'ok': all(r['ok'] for r in results),
        'results': results,
    }
