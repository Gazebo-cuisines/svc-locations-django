from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from typing import Optional


def split_batches(
    gross: Decimal,
    *,
    full_batches_only: bool,
    standard_batch_kg: Optional[Decimal],
    align_unitary_weight: bool,
) -> list[Decimal]:
    """Return per-batch gross quantities. Single batch if not full_batches_only."""
    if gross <= 0:
        return []
    if (
        not full_batches_only
        or standard_batch_kg is None
        or standard_batch_kg <= 0
    ):
        return [gross]

    batch_size = standard_batch_kg
    n = int((gross / batch_size).to_integral_value(rounding=ROUND_CEILING))
    if n <= 0:
        return [gross]

    batches: list[Decimal] = []
    remaining = gross
    for i in range(n):
        is_last = i == n - 1
        if is_last:
            if align_unitary_weight:
                batches.append(batch_size)
            else:
                batches.append(remaining)
        else:
            batches.append(batch_size)
            remaining -= batch_size
    return batches
