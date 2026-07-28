from __future__ import annotations

from decimal import Decimal

from django.db import connection
from django.db.models import Sum

from stock_ledger.models import StockEntry, StockGenealogy
from stock_ledger.util.conversions import StockValidationError

MAX_TRACE_DEPTH = 32

_BACKWARD_SQL = """
WITH RECURSIVE back AS (
  SELECT g.input_entry_id, g.output_entry_id, g.quantity_base, 1 AS depth
  FROM stock_genealogy g
  JOIN stock_entry o ON o.id = g.output_entry_id
  WHERE o.lot_id = %s
  UNION ALL
  SELECT g.input_entry_id, g.output_entry_id, g.quantity_base, b.depth + 1
  FROM stock_genealogy g
  JOIN back b ON g.output_entry_id = b.input_entry_id
  WHERE b.depth < %s
)
SELECT DISTINCT
  l.id AS lot_id,
  l.product_id,
  l.trace_number,
  l.supplier_lot_code,
  b.depth,
  b.input_entry_id,
  b.output_entry_id,
  b.quantity_base
FROM back b
JOIN stock_entry e ON e.id = b.input_entry_id
JOIN stock_lot l ON l.id = e.lot_id
ORDER BY b.depth, l.id
"""

_FORWARD_SQL = """
WITH RECURSIVE fwd AS (
  SELECT g.input_entry_id, g.output_entry_id, g.quantity_base, 1 AS depth
  FROM stock_genealogy g
  JOIN stock_entry i ON i.id = g.input_entry_id
  WHERE i.lot_id = %s
  UNION ALL
  SELECT g.input_entry_id, g.output_entry_id, g.quantity_base, f.depth + 1
  FROM stock_genealogy g
  JOIN fwd f ON g.input_entry_id = f.output_entry_id
  WHERE f.depth < %s
)
SELECT DISTINCT
  l.id AS lot_id,
  l.product_id,
  l.trace_number,
  l.supplier_lot_code,
  f.depth,
  f.input_entry_id,
  f.output_entry_id,
  f.quantity_base
FROM fwd f
JOIN stock_entry e ON e.id = f.output_entry_id
JOIN stock_lot l ON l.id = e.lot_id
ORDER BY f.depth, l.id
"""


def _rows_as_dicts(cursor) -> list[dict]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def trace_backward(*, lot_id: int, max_depth: int = MAX_TRACE_DEPTH) -> list[dict]:
    """Lots/entries that fed into this lot (supplier → FG direction reversed)."""
    with connection.cursor() as cursor:
        cursor.execute(_BACKWARD_SQL, [lot_id, max_depth])
        return _rows_as_dicts(cursor)


def trace_forward(*, lot_id: int, max_depth: int = MAX_TRACE_DEPTH) -> list[dict]:
    """Lots/entries that this lot fed into."""
    with connection.cursor() as cursor:
        cursor.execute(_FORWARD_SQL, [lot_id, max_depth])
        return _rows_as_dicts(cursor)


def mass_balance_for_output(*, output_entry_id: int) -> dict:
    """
    Compare SUM(input genealogy quantity_base) to output quantity_base.
    yield_loss = inputs - output (None if output has no mass).
    """
    try:
        output = StockEntry.objects.only('id', 'quantity_base').get(pk=output_entry_id)
    except StockEntry.DoesNotExist as exc:
        raise StockValidationError(
            f'output_entry_id={output_entry_id} not found'
        ) from exc

    inputs_total = (
        StockGenealogy.objects
        .filter(output_entry_id=output_entry_id)
        .aggregate(total=Sum('quantity_base'))
        ['total']
    ) or Decimal('0')

    output_base = output.quantity_base
    yield_loss = None if output_base is None else (inputs_total - output_base)

    return {
        'output_entry_id': output_entry_id,
        'inputs_quantity_base': inputs_total,
        'output_quantity_base': output_base,
        'yield_loss': yield_loss,
    }
