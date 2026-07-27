"""
Seed a mock Peri Peri Burger product (core + all 1:1 satellites) for team demos.

Usage:
  python manage.py seed_demo_product
  python manage.py seed_demo_product --flush
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from locations.models import Location
from product.models import (
    Category,
    DeliveryState,
    PackagingType,
    PhysicalState,
    Product,
    ProductAudit,
    ProductClass,
    ProductCosting,
    ProductFlags,
    ProductPackaging,
    ProductProduction,
    ProductShelfLife,
    ProductStockPolicy,
    ProductTechnical,
    ProductYield,
    PurchaseShapeFormat,
    Range,
    SubRange,
    Unit,
)

DEMO_PRODUCT_ID = 900001
DEMO_LOCATION_SRC_ID = 900001
DEMO_LOCATION_DST_ID = 900002
DEMO_LOCATION_TRAY_ID = 900003
DEMO_LOCATION_BOX_ID = 900004


class Command(BaseCommand):
    help = 'Seed mock Peri Peri Burger product + satellites'

    def add_arguments(self, parser):
        parser.add_argument(
            '--flush',
            action='store_true',
            help='Delete existing demo product (id=900001) and related demo rows first',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['flush']:
            self._flush_demo()

        if Product.objects.filter(pk=DEMO_PRODUCT_ID).exists():
            self.stdout.write(
                self.style.WARNING(
                    f'Demo product id={DEMO_PRODUCT_ID} already exists. '
                    'Use --flush to recreate.',
                ),
            )
            return

        src, dst, tray, box = self._ensure_demo_locations()
        lookups = self._ensure_lookups()

        product = Product.objects.create(
            id=DEMO_PRODUCT_ID,
            name='Peri Peri Burger',
            alternate_name='PP Burger',
            recipe_code='PPB-001',
            alternate_recipe_code='PPB-ALT',
            gff_code='GFF-PPB',
            secondary_gff_recipe='GFF-PPB-2',
            external_barcode='5012345678901',
            is_active=True,
            is_downtime=False,
            purchasing_version=1,
            ingredient_count=8,
            remarks='Demo finished good — peri peri chicken burger for schema walkthrough.',
            product_class=lookups['product_class'],
            category=lookups['category'],
            range=lookups['range'],
            sub_range=lookups['sub_range'],
            unit=lookups['unit_each'],
            purchasing_unit=lookups['unit_case'],
            purchase_shape_format=lookups['shape'],
            source_container=src,
            destination_container=dst,
        )

        ProductCosting.objects.create(
            product=product,
            unit_cost=Decimal('1.850000'),
            unit_price=Decimal('4.990000'),
            nominal_code='4001',
            case_size_description='12 per case',
            lead_time_days=3,
        )
        ProductStockPolicy.objects.create(
            product=product,
            reorder_level=Decimal('120.000000'),
            min_stock=Decimal('48.000000'),
            max_stock=Decimal('480.000000'),
            clear_stock_level=Decimal('24.000000'),
        )
        ProductShelfLife.objects.create(
            product=product,
            shelf_life_days=5,
            shelf_life_intrinsic_days=7,
            shelf_life_depot_days=3,
            absolute_min_shelf_life_days=2,
            force_production_date=True,
            force_trace_number=True,
            force_use_by=True,
        )
        ProductPackaging.objects.create(
            product=product,
            pack_weight=Decimal('0.180000'),
            unitary_weight=Decimal('0.165000'),
            gross_unitary_weight=Decimal('0.190000'),
            align_unitary_weight=True,
            default_length=Decimal('12.000000'),
            items_per_unit=Decimal('1.000000'),
            units_per_tray=Decimal('6.000000'),
            units_per_batch=Decimal('120.000000'),
            is_gas_flush=False,
            container_vessel=None,
            tray=tray,
            box=box,
            packaging_type=lookups['packaging_type'],
            physical_state=lookups['physical_state'],
            delivery_state=lookups['delivery_state'],
        )
        ProductProduction.objects.create(
            product=product,
            avg_run_size=Decimal('240.000000'),
            avg_minutes=Decimal('45.000000'),
            avg_rate_product=Decimal('5.333300'),
            avg_staff_min_per_unit=Decimal('0.750000'),
            avg_staff_per_minute=Decimal('2.000000'),
            avg_rate_range=Decimal('5.000000'),
            average_rate=Decimal('5.200000'),
            unitary_gap_time=Decimal('0.050000'),
            unitary_dwell_time=Decimal('0.100000'),
            relative_plan_position=10,
            default_resource_id=101,
        )
        ProductYield.objects.create(
            product=product,
            yield_factor=Decimal('0.9200'),
            yield_factor_auto=Decimal('0.9150'),
            chilling_loss_factor=Decimal('0.0200'),
        )
        ProductFlags.objects.create(
            product=product,
            in_stock_list=True,
            auto_yield=True,
            has_plan=True,
            auto_rate=True,
            auto_clear_stock=False,
            consider_stock_in_plan=True,
            has_recipe=True,
            full_batches_only=False,
            auto_trends=True,
            is_implicit=False,
            include_in_projections=True,
            record_flag=True,
            has_chilling_loss=True,
            use_batch_quantity=True,
            freezer_no_deduct=False,
            is_purchase_item=False,
            is_sales_item=True,
            is_dispatch_support=False,
        )
        ProductTechnical.objects.create(
            product=product,
            is_gmo_free=True,
            spec_sign_off_date=date(2026, 1, 15),
            next_review_date=date(2026, 7, 15),
            requires_temperature_check=True,
            temp_check_lower_bound=Decimal('0.0000'),
            temp_check_upper_bound=Decimal('5.0000'),
        )
        ProductAudit.objects.create(
            product=product,
            created_by_user_id=1,
            lan_username='demo.user',
            source_workstation='DEMO-WS-01',
            source_workstation_ip='10.0.0.42',
        )

        self.stdout.write(self.style.SUCCESS(
            f'Seeded Peri Peri Burger id={product.id} '
            f'(recipe_code={product.recipe_code}) with all satellites.',
        ))

    def _flush_demo(self):
        Product.objects.filter(pk=DEMO_PRODUCT_ID).delete()
        Location.objects.filter(
            pk__in=[
                DEMO_LOCATION_SRC_ID,
                DEMO_LOCATION_DST_ID,
                DEMO_LOCATION_TRAY_ID,
                DEMO_LOCATION_BOX_ID,
            ],
        ).delete()
        self.stdout.write('Flushed demo product and demo locations.')

    def _ensure_demo_locations(self):
        src, _ = Location.objects.get_or_create(
            id=DEMO_LOCATION_SRC_ID,
            defaults={
                'name': 'Demo Kitchen Line A',
                'external_code': 'DEMO-SRC',
                'visible': True,
            },
        )
        dst, _ = Location.objects.get_or_create(
            id=DEMO_LOCATION_DST_ID,
            defaults={
                'name': 'Demo Finished Goods Chill',
                'external_code': 'DEMO-DST',
                'visible': True,
            },
        )
        tray, _ = Location.objects.get_or_create(
            id=DEMO_LOCATION_TRAY_ID,
            defaults={
                'name': 'Demo Burger Tray',
                'external_code': 'DEMO-TRAY',
                'visible': True,
            },
        )
        box, _ = Location.objects.get_or_create(
            id=DEMO_LOCATION_BOX_ID,
            defaults={
                'name': 'Demo Dispatch Box',
                'external_code': 'DEMO-BOX',
                'visible': True,
            },
        )
        return src, dst, tray, box

    def _ensure_lookups(self):
        product_class, _ = ProductClass.objects.get_or_create(
            id=9001, defaults={'name': 'Finished Goods'},
        )
        category, _ = Category.objects.get_or_create(
            id=9001, defaults={'name': 'Burgers'},
        )
        rng, _ = Range.objects.get_or_create(
            id=9001, defaults={'name': 'Peri Peri'},
        )
        sub_range, _ = SubRange.objects.get_or_create(
            id=9001,
            defaults={'name': 'Chicken Burgers', 'range': rng},
        )
        unit_each, _ = Unit.objects.get_or_create(
            id=9001, defaults={'name': 'Each'},
        )
        unit_case, _ = Unit.objects.get_or_create(
            id=9002, defaults={'name': 'Case'},
        )
        shape, _ = PurchaseShapeFormat.objects.get_or_create(
            id=9001, defaults={'name': 'Retail Unit'},
        )
        packaging_type, _ = PackagingType.objects.get_or_create(
            id=9001, defaults={'name': 'Clamshell'},
        )
        physical_state, _ = PhysicalState.objects.get_or_create(
            id=9001, defaults={'name': 'Chilled'},
        )
        delivery_state, _ = DeliveryState.objects.get_or_create(
            id=9001, defaults={'name': 'Ready to Eat'},
        )
        return {
            'product_class': product_class,
            'category': category,
            'range': rng,
            'sub_range': sub_range,
            'unit_each': unit_each,
            'unit_case': unit_case,
            'shape': shape,
            'packaging_type': packaging_type,
            'physical_state': physical_state,
            'delivery_state': delivery_state,
        }
