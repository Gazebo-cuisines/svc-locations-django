"""Backfill Liter→kg conversions for products with Liter-inner supplier shapes."""

from decimal import Decimal

from django.core.management.base import BaseCommand

from product.models import ProductPackaging, ProductSupplier, Unit
from stock_ledger.models import StockUnitConversion
from stock_ledger.util.conversions import sync_product_unit_conversion_for_product

WATER_LIKE = Decimal('1.000000')
OIL_DENSITY = Decimal('0.910000')


def _density_for(name: str) -> Decimal:
    if 'oil' in (name or '').lower():
        return OIL_DENSITY
    return WATER_LIKE


class Command(BaseCommand):
    help = (
        'Ensure packaging.unitary_weight + stock_unit_conversion exist for '
        'products whose active supplier inner unit is Liter.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Write packaging + conversion rows (default: dry-run).',
        )
        parser.add_argument(
            '--product-id',
            type=int,
            action='append',
            dest='product_ids',
            help='Limit to one or more product ids (repeatable).',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        product_ids = options.get('product_ids') or None

        try:
            liter = Unit.objects.get(name='Liter')
        except Unit.DoesNotExist:
            self.stderr.write('Unit Liter not found.')
            return

        qs = (
            ProductSupplier.objects
            .filter(is_active=True, inner_unit_id=liter.id)
            .select_related('product')
        )
        if product_ids:
            qs = qs.filter(product_id__in=product_ids)

        seen = set()
        planned = []
        for ps in qs:
            pid = ps.product_id
            if pid in seen:
                continue
            seen.add(pid)
            has = StockUnitConversion.objects.filter(
                unit_id=liter.id, product_id=pid,
            ).exists()
            if has:
                continue
            density = _density_for(ps.product.name)
            planned.append((pid, ps.product.name, density, ps.id))

        self.stdout.write(f'{len(planned)} product(s) need Liter conversion')
        for pid, name, density, ps_id in planned:
            self.stdout.write(
                f'  product_id={pid} {name!r} '
                f'density={density} (supplier {ps_id})'
            )

        if not apply:
            self.stdout.write('Dry-run only. Re-run with --apply to write.')
            return

        written = 0
        for pid, name, density, _ps_id in planned:
            ProductPackaging.objects.update_or_create(
                product_id=pid,
                defaults={'unitary_weight': density},
            )
            n = sync_product_unit_conversion_for_product(pid)
            written += 1
            self.stdout.write(
                f'  applied product_id={pid} unitary_weight={density} '
                f'sync_rows={n}'
            )
        self.stdout.write(self.style.SUCCESS(f'Updated {written} product(s).'))
