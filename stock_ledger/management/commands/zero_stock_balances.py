"""Zero on-hand stock via count_adjustment to counted_quantity=0."""

from uuid import uuid4

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from stock_ledger.models import StockBalance
from stock_ledger.util import services as stock_services
from stock_ledger.util.conversions import StockValidationError


class Command(BaseCommand):
    help = (
        'Set every stock_balance to zero with a count_adjustment '
        '(counted_quantity=0). Ledger history is kept; balance rows are removed.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Write adjustments (default: dry-run).',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        qs = (
            StockBalance.objects
            .exclude(quantity=0)
            .select_related('lot__product')
            .order_by('id')
        )
        rows = list(qs)
        total = qs.aggregate(s=Sum('quantity'))['s']
        self.stdout.write(
            f'{len(rows)} balance(s) with qty, sum={total}'
        )
        for bal in rows:
            self.stdout.write(
                f'  bal={bal.id} product={bal.lot.product_id} '
                f'loc={bal.location_id} qty={bal.quantity}'
            )

        if not apply:
            self.stdout.write('Dry-run only. Re-run with --apply to zero.')
            return

        ok = 0
        for bal in rows:
            product = bal.lot.product
            if product.unit_id is None:
                self.stderr.write(
                    f'  skip bal={bal.id}: product {product.id} has no unit'
                )
                continue
            key = f'zero-stock:{bal.id}:{uuid4().hex[:12]}'
            try:
                with transaction.atomic():
                    stock_services.count_adjustment(
                        idempotency_key=key,
                        lot=bal.lot,
                        location_id=bal.location_id,
                        counted_quantity=0,
                        unit_id=product.unit_id,
                        remarks='Test wipe: set counted stock to zero',
                        source_document_type='test_wipe',
                    )
            except StockValidationError as exc:
                self.stderr.write(f'  fail bal={bal.id}: {exc}')
                continue
            ok += 1
            self.stdout.write(f'  zeroed bal={bal.id}')

        left = StockBalance.objects.exclude(quantity=0).count()
        self.stdout.write(
            self.style.SUCCESS(
                f'Zeroed {ok} balance(s). Remaining nonzero={left}.'
            )
        )
