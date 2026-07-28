from django.db import migrations


def seed_unit_conversions(apps, schema_editor):
    Unit = apps.get_model('product', 'Unit')
    StockUnitConversion = apps.get_model('stock_ledger', 'StockUnitConversion')
    ProductPackaging = apps.get_model('product', 'ProductPackaging')

    global_rules = {
        'grams': '0.001000',
        'Kg': '1.000000',
    }
    for name, to_kg in global_rules.items():
        try:
            unit = Unit.objects.get(name=name)
        except Unit.DoesNotExist:
            continue
        StockUnitConversion.objects.update_or_create(
            unit_id=unit.id,
            product=None,
            defaults={'to_kg': to_kg, 'source': 'global'},
        )

    unit_names = ('unit', 'Box', 'Liter')
    units = {u.name: u for u in Unit.objects.filter(name__in=unit_names)}
    if not units:
        return

    for packaging in (
        ProductPackaging.objects
        .exclude(unitary_weight__isnull=True)
        .filter(unitary_weight__gt=0)
        .select_related('product')
    ):
        if packaging.product.is_downtime:
            continue
        for name, unit in units.items():
            StockUnitConversion.objects.update_or_create(
                unit_id=unit.id,
                product_id=packaging.product_id,
                defaults={
                    'to_kg': packaging.unitary_weight,
                    'source': 'product_packaging',
                },
            )


def unseed_unit_conversions(apps, schema_editor):
    StockUnitConversion = apps.get_model('stock_ledger', 'StockUnitConversion')
    StockUnitConversion.objects.filter(
        source__in=('global', 'product_packaging'),
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0005_triggers_chunk7'),
        ('product', '0002_productacceptance_productingredientlabel_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_unit_conversions, unseed_unit_conversions),
    ]
