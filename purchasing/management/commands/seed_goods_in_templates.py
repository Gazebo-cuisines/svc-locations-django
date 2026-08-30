from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from purchasing.models import (
    GoodsInCheckItem,
    GoodsInCheckScope,
    GoodsInCheckTemplate,
    GoodsInFailWhen,
    GoodsInInputType,
)


DOC = {
    'document_no': 'GFF001F',
    'issue_no': 15,
    'issue_date': date(1997, 1, 1),
    'review_date': date(2023, 10, 16),
    'previous_issue_date': date(2020, 12, 7),
    'reason_for_change': 'QC or Team leader check added',
}


def _upsert_template(*, name, goods_in_type, storage_regime, scope, items):
    template, created = GoodsInCheckTemplate.objects.update_or_create(
        goods_in_type=goods_in_type,
        storage_regime=storage_regime,
        scope=scope,
        version=1,
        defaults={
            'name': name,
            'is_active': True,
            **DOC,
        },
    )
    template.items.all().delete()
    GoodsInCheckItem.objects.bulk_create([
        GoodsInCheckItem(template=template, **item) for item in items
    ])
    return template, created


def seed_goods_in_templates():
    food_header = [
        {
            'code': 'vehicle_clean_fb_pest_odour',
            'label': 'Vehicle Clean, Free from FB, Pest and Odour',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.FALSE,
            'allows_comment': True,
            'sort_order': 10,
        },
        {
            'code': 'primary_outer_packaging_damaged',
            'label': 'Primary & Outer packaging damaged?',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.TRUE,
            'allows_comment': True,
            'sort_order': 20,
        },
        {
            'code': 'vehicle_temperature',
            'label': 'Vehicle Temp (°C)',
            'input_type': GoodsInInputType.DECIMAL,
            'required': False,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.OUT_OF_RANGE,
            'source': 'regime.vehicle_temp',
            'allows_comment': True,
            'sort_order': 30,
        },
        {
            'code': 'coa_coc_received',
            'label': 'COA/COC received',
            'input_type': GoodsInInputType.BOOL,
            'required': False,
            'is_critical': False,
            'fail_when': GoodsInFailWhen.FALSE,
            'allows_comment': False,
            'sort_order': 40,
        },
        {
            'code': 'reject_delivery',
            'label': 'Reject Delivery?',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.TRUE,
            'allows_comment': True,
            'sort_order': 50,
        },
        {
            'code': 'comment',
            'label': 'Comment',
            'input_type': GoodsInInputType.TEXT,
            'required': False,
            'is_critical': False,
            'fail_when': None,
            'allows_comment': False,
            'sort_order': 60,
        },
        {
            'code': 'random_qc_tl_check',
            'label': 'Random QC or TL Check',
            'input_type': GoodsInInputType.BOOL,
            'required': False,
            'is_critical': False,
            'fail_when': None,
            'allows_comment': True,
            'sort_order': 70,
        },
    ]

    food_line = [
        {
            'code': 'use_by',
            'label': 'UBD / BBE',
            'input_type': GoodsInInputType.DATE,
            'required': True,
            'is_critical': True,
            'fail_when': None,
            'source': 'product.min_shelf_life',
            'allows_comment': True,
            'sort_order': 10,
        },
        {
            'code': 'product_temperature',
            'label': 'Product Temp',
            'input_type': GoodsInInputType.DECIMAL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.OUT_OF_RANGE,
            'source': 'product.temp_bounds',
            'allows_comment': True,
            'sort_order': 20,
        },
        {
            'code': 'spec_check',
            'label': 'Spec Check',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.FALSE,
            'allows_comment': True,
            'sort_order': 30,
        },
    ]

    packaging_header = [
        {
            'code': 'vehicle_clean_fb_pest_odour',
            'label': 'Vehicle Clean, Free from FB, Pest and Odour',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.FALSE,
            'allows_comment': True,
            'sort_order': 10,
        },
        {
            'code': 'primary_packaging_damaged',
            'label': 'Primary Packaging Damaged',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.TRUE,
            'allows_comment': True,
            'sort_order': 20,
        },
        {
            'code': 'damaged_product',
            'label': 'Damaged Product?',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.TRUE,
            'allows_comment': True,
            'sort_order': 30,
        },
        {
            'code': 'reject_delivery',
            'label': 'Reject Delivery?',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.TRUE,
            'allows_comment': True,
            'sort_order': 40,
        },
        {
            'code': 'comment',
            'label': 'Comment',
            'input_type': GoodsInInputType.TEXT,
            'required': False,
            'is_critical': False,
            'fail_when': None,
            'allows_comment': False,
            'sort_order': 50,
        },
        {
            'code': 'random_qc_tl_check',
            'label': 'Random QC or TL Check',
            'input_type': GoodsInInputType.BOOL,
            'required': False,
            'is_critical': False,
            'fail_when': None,
            'allows_comment': True,
            'sort_order': 60,
        },
    ]

    packaging_line = [
        {
            'code': 'spec_check',
            'label': 'Spec Check',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.FALSE,
            'allows_comment': True,
            'sort_order': 10,
        },
    ]

    other_header = [
        {
            'code': 'damaged_product',
            'label': 'Damaged Product?',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.TRUE,
            'allows_comment': True,
            'sort_order': 10,
        },
        {
            'code': 'reject_delivery',
            'label': 'Reject Delivery?',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': True,
            'fail_when': GoodsInFailWhen.TRUE,
            'allows_comment': True,
            'sort_order': 20,
        },
        {
            'code': 'comment',
            'label': 'Comment',
            'input_type': GoodsInInputType.TEXT,
            'required': False,
            'is_critical': False,
            'fail_when': None,
            'allows_comment': False,
            'sort_order': 30,
        },
    ]

    other_line = [
        {
            'code': 'spec_check',
            'label': 'Spec Check',
            'input_type': GoodsInInputType.BOOL,
            'required': True,
            'is_critical': False,
            'fail_when': GoodsInFailWhen.FALSE,
            'allows_comment': True,
            'sort_order': 10,
        },
    ]

    specs = [
        ('Food goods inward — header', 'raw_material', None, GoodsInCheckScope.HEADER, food_header),
        ('Food goods inward — line', 'raw_material', None, GoodsInCheckScope.LINE, food_line),
        ('Packaging goods inward — header', 'packaging', None, GoodsInCheckScope.HEADER, packaging_header),
        ('Packaging goods inward — line', 'packaging', None, GoodsInCheckScope.LINE, packaging_line),
        ('Other goods inward — header', 'other', None, GoodsInCheckScope.HEADER, other_header),
        ('Other goods inward — line', 'other', None, GoodsInCheckScope.LINE, other_line),
    ]

    results = []
    with transaction.atomic():
        for name, gin_type, regime, scope, items in specs:
            template, created = _upsert_template(
                name=name,
                goods_in_type=gin_type,
                storage_regime=regime,
                scope=scope,
                items=items,
            )
            results.append((template, created, len(items)))
    return results


class Command(BaseCommand):
    help = 'Seed GFF001F food / packaging / other goods-in check templates.'

    def handle(self, *args, **options):
        for template, created, item_count in seed_goods_in_templates():
            action = 'created' if created else 'updated'
            self.stdout.write(
                f'{action}: {template} ({item_count} items)',
            )
        self.stdout.write(self.style.SUCCESS('Goods-in check templates seeded.'))
