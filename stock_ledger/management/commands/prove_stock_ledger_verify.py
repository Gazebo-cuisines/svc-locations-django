"""
Prove each verifier fails on deliberately corrupted state, then restore.
Requires at least one location/unit; creates ephemeral product/lot/entries.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from locations.models import Location
from product.models import (
    Category,
    Product,
    ProductClass,
    Range,
    Unit,
)
from stock_ledger.models import (
    StockBalance,
    StockChainHead,
    StockEntry,
    StockEntryType,
    StockLot,
    StockLotOrigin,
    StockReservation,
    StockReservationStatus,
)
from stock_ledger.util import services
from stock_ledger.util.verify import (
    check_balance_invariant,
    check_chain_continuity,
    check_reservation_overbook,
    check_transfer_atomicity,
)


class Command(BaseCommand):
    help = 'Corrupt rows on purpose, assert verifiers FAIL, then restore.'

    def handle(self, *args, **options):
        unit = Unit.objects.filter(name='Kg').first() or Unit.objects.first()
        location = Location.objects.order_by('id').first()
        if unit is None or location is None:
            raise CommandError('Need product_unit and loc_location rows')

        product = self._ensure_product(unit)
        lot = StockLot.objects.create(
            product=product,
            trace_number=f'PROVE-{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 1, 1),
            use_by=date(2026, 12, 31),
        )

        proofs: list[tuple[str, bool]] = []

        # --- balance drift ---
        receipt = services.receipt(
            idempotency_key=f'prove-bal-{uuid4().hex}',
            lot=lot,
            location_id=location.id,
            quantity=Decimal('10'),
            unit_id=unit.id,
        )
        bal = StockBalance.objects.get(lot_id=lot.id, location_id=location.id)
        original_qty = bal.quantity
        StockBalance.objects.filter(pk=bal.pk).update(quantity=original_qty + 1)
        bal_check = check_balance_invariant()
        StockBalance.objects.filter(pk=bal.pk).update(quantity=original_qty)
        proofs.append(('balance_invariant', not bal_check['ok']))

        # --- transfer imbalance ---
        group = str(uuid4())
        services._insert_entry(
            idempotency_key=f'prove-t-out-{uuid4().hex}',
            entry_type=StockEntryType.TRANSFER_OUT,
            lot=lot,
            location_id=location.id,
            quantity=Decimal('-5'),
            unit_id=unit.id,
            effective_at=timezone.now(),
            transfer_group_id=group,
            counterparty_location_id=location.id,
        )
        other = Location.objects.exclude(pk=location.id).order_by('id').first() or location
        services._insert_entry(
            idempotency_key=f'prove-t-in-{uuid4().hex}',
            entry_type=StockEntryType.TRANSFER_IN,
            lot=lot,
            location_id=other.id,
            quantity=Decimal('3'),
            unit_id=unit.id,
            effective_at=timezone.now(),
            transfer_group_id=group,
            counterparty_location_id=location.id,
        )
        xfer_check = check_transfer_atomicity()
        proofs.append(('transfer_atomicity', not xfer_check['ok']))
        self._delete_entries([
            e.id for e in StockEntry.objects.filter(transfer_group_id=group)
        ])
        self._rebuild_balances(lot.id)

        # --- reservation overbook ---
        StockReservation.objects.create(
            lot=lot,
            location_id=location.id,
            quantity=Decimal('999999'),
            unit_id=unit.id,
            status=StockReservationStatus.OPEN,
        )
        res_check = check_reservation_overbook()
        StockReservation.objects.filter(
            lot=lot, location_id=location.id, quantity=Decimal('999999'),
        ).delete()
        proofs.append(('reservation_overbook', not res_check['ok']))

        # --- chain continuity (need >=2 linked entries; corrupt middle/root hash) ---
        second = services.receipt(
            idempotency_key=f'prove-chain-{uuid4().hex}',
            lot=lot,
            location_id=location.id,
            quantity=Decimal('1'),
            unit_id=unit.id,
        )
        entry = StockEntry.objects.filter(lot=lot).order_by('id').first()
        if entry is None or second.id == entry.id:
            raise CommandError('expected two stock_entry rows for chain prove')
        good_hash = entry.entry_hash
        with connection.cursor() as cursor:
            cursor.execute('DROP TRIGGER IF EXISTS stock_entry_bu')
            cursor.execute(
                'UPDATE stock_entry SET entry_hash=%s WHERE id=%s',
                ['0' * 64, entry.id],
            )
        chain_check = check_chain_continuity()
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE stock_entry SET entry_hash=%s WHERE id=%s',
                [good_hash, entry.id],
            )
            cursor.execute(
                """
CREATE TRIGGER stock_entry_bu BEFORE UPDATE ON stock_entry FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_entry: updates forbidden';
END
"""
            )
        proofs.append(('chain_continuity', not chain_check['ok']))

        # Remove prove rows
        self._delete_entries(
            list(StockEntry.objects.filter(lot=lot).values_list('id', flat=True))
        )
        StockBalance.objects.filter(lot=lot).delete()
        StockReservation.objects.filter(lot=lot).delete()
        lot.delete()

        failed = [name for name, detected in proofs if not detected]
        for name, detected in proofs:
            self.stdout.write(
                f'{name}: {"DETECTS_CORRUPTION" if detected else "MISS"}'
            )
        if failed:
            raise CommandError(f'prove failed for: {", ".join(failed)}')
        self.stdout.write(self.style.SUCCESS('all verifiers detect corruption'))

    def _ensure_product(self, unit: Unit) -> Product:
        existing = Product.objects.order_by('id').first()
        if existing is not None:
            return existing
        pc, _ = ProductClass.objects.get_or_create(id=9001, defaults={'name': 'prove-class'})
        cat, _ = Category.objects.get_or_create(id=9001, defaults={'name': 'prove-cat'})
        rng, _ = Range.objects.get_or_create(id=9001, defaults={'name': 'prove-range'})
        return Product.objects.create(
            id=900001,
            name='Prove Stock Product',
            product_class=pc,
            category=cat,
            range=rng,
            unit=unit,
        )

    def _delete_entries(self, entry_ids: list[int]) -> None:
        if not entry_ids:
            return
        StockBalance.objects.filter(last_entry_id__in=entry_ids).delete()
        StockBalance.objects.filter(last_count_entry_id__in=entry_ids).delete()
        StockBalance.objects.filter(
            negative_authorised_by_entry_id__in=entry_ids,
        ).delete()
        StockReservation.objects.filter(consumed_by_entry_id__in=entry_ids).update(
            consumed_by_entry=None,
        )
        head = StockChainHead.objects.filter(pk=1).first()
        if head and head.head_entry_id in entry_ids:
            head.head_entry_id = None
            head.head_hash = None
            head.entry_count = 0
            head.updated_at = timezone.now()
            head.save(update_fields=[
                'head_entry_id', 'head_hash', 'entry_count', 'updated_at',
            ])
        with connection.cursor() as cursor:
            cursor.execute('DROP TRIGGER IF EXISTS stock_entry_bd')
            cursor.execute('DROP TRIGGER IF EXISTS stock_genealogy_bd')
            format_ids = ','.join(['%s'] * len(entry_ids))
            cursor.execute(
                f'DELETE FROM stock_genealogy WHERE output_entry_id IN ({format_ids}) '
                f'OR input_entry_id IN ({format_ids})',
                entry_ids + entry_ids,
            )
            cursor.execute(
                f'UPDATE stock_entry SET reverses_entry_id=NULL '
                f'WHERE reverses_entry_id IN ({format_ids})',
                entry_ids,
            )
            cursor.execute(
                f'DELETE FROM stock_entry WHERE id IN ({format_ids})',
                entry_ids,
            )
            cursor.execute(
                """
CREATE TRIGGER stock_entry_bd BEFORE DELETE ON stock_entry FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_entry: deletes forbidden';
END
"""
            )
            cursor.execute(
                """
CREATE TRIGGER stock_genealogy_bd BEFORE DELETE ON stock_genealogy FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_genealogy: deletes forbidden';
END
"""
            )
        remaining = StockEntry.objects.order_by('-id').first()
        head = StockChainHead.objects.get(pk=1)
        if remaining is None:
            head.head_entry_id = None
            head.head_hash = None
            head.entry_count = 0
        else:
            head.head_entry_id = remaining.id
            head.head_hash = remaining.entry_hash
            head.entry_count = StockEntry.objects.count()
        head.updated_at = timezone.now()
        head.save()

    def _rebuild_balances(self, lot_id: int) -> None:
        from django.db.models import Sum

        StockBalance.objects.filter(lot_id=lot_id).delete()
        rows = (
            StockEntry.objects
            .filter(lot_id=lot_id)
            .values('lot_id', 'location_id')
            .annotate(
                quantity=Sum('quantity'),
                quantity_base=Sum('quantity_base'),
            )
        )
        for row in rows:
            last = (
                StockEntry.objects
                .filter(lot_id=row['lot_id'], location_id=row['location_id'])
                .order_by('-id')
                .first()
            )
            if last is None:
                continue
            StockBalance.objects.create(
                lot_id=row['lot_id'],
                location_id=row['location_id'],
                quantity=row['quantity'],
                quantity_base=row['quantity_base'],
                last_entry=last,
                updated_at=timezone.now(),
            )
