"""
Seed GAZEBO supplier-product mappings + warehouse stock for production tests.

  python manage.py seed_unit2_raw_stock
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from product.models import Product, ProductSupplier, Unit
from stock_ledger.models import StockLotOrigin
from stock_ledger.util import services

GAZEBO_SUPPLIER_ID = 11
UNIT_2_ID = 8
UNIT_11_ID = 2
SPICE_ROOM_ID = 15
HIGH_RISK_ID = 4
SLEEVING_ID = 5

# qty = stock-unit amount (enough for several MADE / allocate runs).
# warehouse = receive location; also_at = extra on-hand for floor allocate.
RAW_LINES: list[dict] = [
    # --- Unit 2 / Spice BOM ---
    {
        'product_id': 1957, 'name': 'BALTI PASTE', 'code': 'GAZ-BALTI',
        'qty': '25000', 'shape': 'grams', 'warehouse': UNIT_2_ID,
        'also_at': [SPICE_ROOM_ID],
    },
    {
        'product_id': 910122, 'name': 'SALT', 'code': 'GAZ-SALT',
        'qty': '50000', 'shape': 'grams', 'warehouse': UNIT_2_ID,
        'also_at': [SPICE_ROOM_ID],
    },
    {
        'product_id': 910125, 'name': 'SUGAR-', 'code': 'GAZ-SUGAR',
        'qty': '500', 'shape': 'box', 'warehouse': UNIT_2_ID,
        'also_at': [SPICE_ROOM_ID],
    },
    {
        'product_id': 910127, 'name': 'CUMIN SEED-', 'code': 'GAZ-CUMIN',
        'qty': '500', 'shape': 'box', 'warehouse': UNIT_2_ID,
        'also_at': [SPICE_ROOM_ID],
    },
    {
        'product_id': 910130, 'name': 'CHILLI POWDER-', 'code': 'GAZ-CHILLI',
        'qty': '500', 'shape': 'box', 'warehouse': UNIT_2_ID,
        'also_at': [SPICE_ROOM_ID],
    },
    {
        'product_id': 1976, 'name': 'TAMARIND CONCENTRATE', 'code': 'GAZ-TAMARIND',
        'qty': '25000', 'shape': 'grams', 'warehouse': UNIT_2_ID,
        'also_at': [SPICE_ROOM_ID],
    },
    {
        'product_id': 910128, 'name': 'GARAM MASALA', 'code': 'GAZ-GARAM',
        'qty': '500', 'shape': 'box', 'warehouse': UNIT_2_ID,
        'also_at': [SPICE_ROOM_ID],
    },
    {
        'product_id': 1955, 'name': 'TOMATO PASTE', 'code': 'GAZ01',
        'qty': '50000', 'shape': 'bag10kg', 'warehouse': UNIT_2_ID,
        'also_at': [],
    },
    {
        'product_id': 1954, 'name': 'LEMON JUICE', 'code': 'GAZ-LEMON',
        'qty': '25000', 'shape': 'grams', 'warehouse': UNIT_2_ID,
        'also_at': [],
    },
    {
        'product_id': 1996, 'name': 'PEAS ( FROZEN )', 'code': 'GAZ-PEAS',
        'qty': '50000', 'shape': 'grams', 'warehouse': UNIT_2_ID,
        'also_at': [],
    },
    {
        'product_id': 1997, 'name': 'ONION WHITE DICED 10MM ( FROZEN )', 'code': 'GAZ-ONION',
        'qty': '50000', 'shape': 'grams', 'warehouse': UNIT_2_ID,
        'also_at': [],
    },
    {
        'product_id': 2005, 'name': 'CORIANDER CHOPPED ( FROZEN )', 'code': 'GAZ-CORI',
        'qty': '25000', 'shape': 'grams', 'warehouse': UNIT_2_ID,
        'also_at': [],
    },
    # --- Steaming BOM ---
    {
        'product_id': 1999, 'name': 'CARROT DICED 10MM ( FROZEN )', 'code': 'GAZ-CARROT',
        'qty': '100000', 'shape': 'grams', 'warehouse': UNIT_2_ID,
        'also_at': [223],  # Steaming
    },
    {
        'product_id': 2059, 'name': 'POTATO DICED 10MM ( FRESH )', 'code': 'GAZ-POTATO',
        'qty': '100000', 'shape': 'grams', 'warehouse': UNIT_2_ID,
        'also_at': [223],
    },
    # --- Unit 11 packaging (Plan picking list) ---
    {
        'product_id': 2135,
        'name': 'Film - K peel 7G - 25 mu -  f-240mm - 240mm x 1000',
        'code': 'GAZ-FILM-KPEEL',
        'qty': '5000',
        'shape': 'meters',
        'warehouse': UNIT_11_ID,
        'also_at': [HIGH_RISK_ID],
    },
    {
        'product_id': 2134,
        'name': 'Tray - Grab & Go - Square Tray',
        'code': 'GAZ-TRAY-GG',
        'qty': '5000',
        'shape': 'unit',
        'warehouse': UNIT_11_ID,
        'also_at': [HIGH_RISK_ID],
    },
    {
        'product_id': 910120,
        'name': 'Box - GRAB AND GO x 6 TUBS - 280 x 148 x 134',
        'code': 'GAZ-BOX-GG6',
        'qty': '500',
        'shape': 'each',
        'warehouse': UNIT_11_ID,
        'also_at': [SLEEVING_ID],
    },
    {
        'product_id': 2289,
        'name': 'G&G Veg Samosa ( 95mm x 263mm)',
        'code': 'GAZ-LABEL-GGVS',
        'qty': '5000',
        'shape': 'unit',
        'warehouse': UNIT_11_ID,
        'also_at': [SLEEVING_ID],
    },
]

LOC_LABEL = {
    UNIT_2_ID: 'Unit 2',
    UNIT_11_ID: 'Unit 11',
    SPICE_ROOM_ID: 'Spice Room',
    HIGH_RISK_ID: 'High Risk',
    SLEEVING_ID: 'Sleeving',
    223: 'Steaming',
}


class Command(BaseCommand):
    help = 'Seed GAZEBO supplier mappings + Unit 2 / Unit 11 / floor raw+pack stock'

    def add_arguments(self, parser):
        parser.add_argument(
            '--warehouse-only',
            action='store_true',
            help='Skip also_at floor copies (Spice Room / High Risk / Sleeving)',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        grams = Unit.objects.get(pk=2)
        box = Unit.objects.get(pk=6)
        kg = Unit.objects.get(pk=5)
        meters = Unit.objects.get(pk=3)
        unit = Unit.objects.get(pk=1)
        each = Unit.objects.filter(pk=9001).first() or unit
        bag = Unit.objects.filter(name__iexact='Bag').first() or box

        created_maps = 0
        receipts = 0

        for line in RAW_LINES:
            product = Product.objects.filter(pk=line['product_id'], is_active=True).first()
            if product is None:
                self.stdout.write(
                    self.style.WARNING(f'skip missing product {line["product_id"]}')
                )
                continue

            outer_qty, outer_unit, inner_qty, inner_unit = self._shape(
                line['shape'],
                grams=grams,
                box=box,
                kg=kg,
                bag=bag,
                meters=meters,
                unit=unit,
                each=each,
            )
            multiplier = ProductSupplier.build_multiplier(outer_qty, inner_qty)
            label = ProductSupplier.build_shape_label(
                outer_qty,
                outer_unit.name,
                inner_qty,
                inner_unit.name,
                multiplier,
            )

            _, was_created = ProductSupplier.objects.get_or_create(
                product_id=product.id,
                supplier_id=GAZEBO_SUPPLIER_ID,
                supplier_code=line['code'],
                defaults={
                    'supplier_product_name': line['name'][:128],
                    'outer_qty': outer_qty,
                    'outer_unit': outer_unit,
                    'inner_qty': inner_qty,
                    'inner_unit': inner_unit,
                    'multiplier': multiplier,
                    'shape_format_label': label,
                    'is_default': not ProductSupplier.objects.filter(
                        product_id=product.id,
                        is_default=True,
                    ).exists(),
                    'is_active': True,
                    'cost': Decimal('1.000000'),
                },
            )
            if was_created:
                created_maps += 1
                self.stdout.write(
                    f'  + supplier map {line["code"]} -> {product.name} ({label})'
                )

            use_by = timezone.localdate().replace(year=timezone.localdate().year + 1)
            lot = services.resolve_lot(
                product_id=product.id,
                trace_number=f'DUMMY-{product.id}',
                production_date=timezone.localdate(),
                use_by=use_by,
                origin=StockLotOrigin.PURCHASE,
                supplier_lot_code=line['code'],
            )
            qty = Decimal(line['qty'])
            warehouse_id = line['warehouse']
            services.receipt(
                idempotency_key=f'dummy-wh-{warehouse_id}-{product.id}-{uuid4().hex[:8]}',
                lot=lot,
                location_id=warehouse_id,
                quantity=qty,
                unit_id=product.unit_id,
                remarks=f'dummy seed {LOC_LABEL.get(warehouse_id, warehouse_id)}',
            )
            receipts += 1
            self.stdout.write(
                f'  + {LOC_LABEL.get(warehouse_id, warehouse_id)} +{qty} {product.name}'
            )

            if options['warehouse_only']:
                continue
            for loc_id in line.get('also_at') or []:
                services.receipt(
                    idempotency_key=(
                        f'dummy-floor-{loc_id}-{product.id}-{uuid4().hex[:8]}'
                    ),
                    lot=lot,
                    location_id=loc_id,
                    quantity=qty,
                    unit_id=product.unit_id,
                    remarks=f'dummy seed {LOC_LABEL.get(loc_id, loc_id)} for allocate',
                )
                self.stdout.write(
                    f'  + {LOC_LABEL.get(loc_id, loc_id)} +{qty} {product.name}'
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. supplier maps created={created_maps}, receipts={receipts}'
            )
        )

    @staticmethod
    def _shape(kind: str, *, grams, box, kg, bag, meters, unit, each):
        if kind == 'bag10kg':
            return Decimal('1'), bag, Decimal('10'), kg
        if kind == 'box':
            return Decimal('1'), box, Decimal('1'), box
        if kind == 'meters':
            return Decimal('1'), meters, Decimal('1000'), meters
        if kind == 'unit':
            return Decimal('1'), unit, Decimal('1'), unit
        if kind == 'each':
            return Decimal('1'), each, Decimal('1'), each
        return Decimal('1'), bag, Decimal('1000'), grams
