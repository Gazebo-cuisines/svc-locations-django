"""Scale stock_entry qty into product.unit when posted in a different unit."""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Sum

from stock_ledger.models import StockBalance, StockEntry
from stock_ledger.util.conversions import StockValidationError, to_product_unit


class Command(BaseCommand):
    help = (
        'Convert ledger qty into product.unit where entry.unit_id differs. '
        'MySQL stock_entry_bu blocks UPDATE; --apply is SQLite/tests only unless that trigger is dropped.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        apply = options['apply']
        if apply and connection.vendor == 'mysql':
            self.stderr.write(
                'MySQL stock_entry_bu blocks updates. Dry-run only.'
            )
            apply = False

        changed = []
        qs = (
            StockEntry.objects
            .select_related('lot__product')
            .exclude(lot__product__unit_id=None)
            .exclude(unit_id=None)
        )
        for entry in qs.iterator():
            product = entry.lot.product
            if entry.unit_id == product.unit_id:
                continue
            try:
                new_qty = to_product_unit(entry.quantity, entry.unit_id, product)
            except StockValidationError as exc:
                self.stderr.write(f'entry {entry.id}: {exc}')
                continue
            if new_qty == entry.quantity:
                continue
            changed.append((entry.id, entry.quantity, new_qty, product.unit_id))

        self.stdout.write(f'{len(changed)} entry(ies) need conversion')
        for entry_id, old, new, unit_id in changed[:20]:
            self.stdout.write(f'  E{entry_id} {old} -> {new} unit={unit_id}')
        if not apply or not changed:
            return

        with transaction.atomic():
            for entry_id, _old, new_qty, unit_id in changed:
                StockEntry.objects.filter(pk=entry_id).update(
                    quantity=new_qty, unit_id=unit_id,
                )
            keys = (
                StockBalance.objects
                .values_list('lot_id', 'location_id')
            )
            for lot_id, location_id in keys:
                total = (
                    StockEntry.objects
                    .filter(lot_id=lot_id, location_id=location_id)
                    .aggregate(s=Sum('quantity'))['s']
                ) or Decimal('0')
                bal = StockBalance.objects.get(
                    lot_id=lot_id, location_id=location_id,
                )
                if total == 0:
                    bal.delete()
                else:
                    bal.quantity = total
                    bal.save(update_fields=['quantity'])
        self.stdout.write('applied')
