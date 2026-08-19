"""
Seed mock stock ledger movements and validate invariants.

Leaves data in DB for manual testing.

Usage:
  python manage.py seed_stock_ledger_demo
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from locations.models import Location
from product.models import Product, Unit
from stock_ledger.models import (
    StockLot,
    StockLotOrigin,
)
from stock_ledger.util import reservations, services
from stock_ledger.util.verify import run_all_verifications


class Command(BaseCommand):
    help = 'Seed mock stock movements, run verifiers, leave data for testing.'

    def handle(self, *args, **options):
        unit_kg = Unit.objects.filter(name='Kg').first()
        if unit_kg is None:
            raise CommandError('product_unit Kg is required')

        locations = list(Location.objects.order_by('id')[:2])
        if len(locations) < 2:
            raise CommandError('need at least 2 locations')
        loc_a, loc_b = locations[0], locations[1]

        product = Product.objects.filter(is_downtime=False).order_by('id').first()
        if product is None:
            raise CommandError('need at least one non-downtime product')

        tag = uuid4().hex[:8]
        now = timezone.now()

        with transaction.atomic():
            lot = StockLot.objects.create(
                product=product,
                trace_number=f'DEMO-{tag}',
                origin=StockLotOrigin.PURCHASE,
                production_date=date(2026, 7, 1),
                use_by=date(2026, 12, 31),
                supplier_lot_code=f'SUP-{tag}',
            )
            ingredient = Product.objects.filter(
                is_downtime=False,
            ).exclude(pk=product.id).order_by('id').first()
            in_lot = None
            if ingredient is not None:
                in_lot = StockLot.objects.create(
                    product=ingredient,
                    trace_number=f'DEMO-IN-{tag}',
                    origin=StockLotOrigin.PURCHASE,
                    production_date=date(2026, 7, 1),
                    use_by=date(2026, 12, 31),
                )

            receipt = services.receipt(
                idempotency_key=f'demo-receipt-{tag}',
                lot=lot,
                location_id=loc_a.id,
                quantity=Decimal('100'),
                unit_id=unit_kg.id,
                effective_at=now,
            )
            out_leg, in_leg = services.transfer(
                idempotency_key=f'demo-xfer-{tag}',
                lot=lot,
                from_location_id=loc_a.id,
                to_location_id=loc_b.id,
                quantity=Decimal('25'),
                unit_id=unit_kg.id,
                effective_at=now,
            )
            issue = services.issue(
                idempotency_key=f'demo-issue-{tag}',
                lot=lot,
                location_id=loc_b.id,
                quantity=Decimal('5'),
                unit_id=unit_kg.id,
                effective_at=now,
            )
            adj = services.count_adjustment(
                idempotency_key=f'demo-adj-{tag}',
                lot=lot,
                location_id=loc_a.id,
                quantity_delta=Decimal('-2'),
                unit_id=unit_kg.id,
                effective_at=now,
            )
            reservation = reservations.reserve(
                lot=lot,
                location_id=loc_a.id,
                quantity=Decimal('10'),
                unit_id=unit_kg.id,
                source_document_type='demo',
                source_document_id=1,
            )

            production = None
            if in_lot is not None:
                services.receipt(
                    idempotency_key=f'demo-in-receipt-{tag}',
                    lot=in_lot,
                    location_id=loc_a.id,
                    quantity=Decimal('20'),
                    unit_id=unit_kg.id,
                    effective_at=now,
                )
                production, consumptions = services.production(
                    idempotency_key=f'demo-prod-{tag}',
                    output_lot=lot,
                    output_location_id=loc_a.id,
                    output_quantity=Decimal('8'),
                    output_unit_id=unit_kg.id,
                    inputs=[{
                        'lot': in_lot,
                        'location_id': loc_a.id,
                        'quantity': Decimal('10'),
                        'unit_id': unit_kg.id,
                    }],
                    effective_at=now,
                )

        report = run_all_verifications()
        self.stdout.write(
            f'lot={lot.id} trace={lot.trace_number} '
            f'product={product.id} loc_a={loc_a.id} loc_b={loc_b.id}'
        )
        self.stdout.write(
            f'receipt={receipt.id} transfer={out_leg.id}/{in_leg.id} '
            f'issue={issue.id} adj={adj.id} reservation={reservation.id}'
        )
        if production is not None:
            self.stdout.write(f'production_output={production.id}')

        for row in report['results']:
            status = 'OK' if row['ok'] else 'FAIL'
            self.stdout.write(f"{row['check']}: {status}")

        if not report['ok']:
            raise CommandError('verification failed after demo seed')

        atp = reservations.available_to_promise(
            lot_id=lot.id, location_id=loc_a.id,
        )
        self.stdout.write(f'ATP loc_a={atp}')
        self.stdout.write(self.style.SUCCESS(
            'demo seed OK - data left in DB for testing'
        ))
