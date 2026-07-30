from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def forwards_backfill(apps, schema_editor):
    ProductSupplier = apps.get_model('product', 'ProductSupplier')
    Unit = apps.get_model('product', 'Unit')

    def fmt(value: Decimal) -> str:
        text = format(value.normalize(), 'f')
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
        return text

    for row in ProductSupplier.objects.all().iterator():
        product = row.product
        outer_unit_id = row.pack_unit_id
        inner_unit_id = product.unit_id
        outer_qty = Decimal('1')
        inner_qty = row.conversion_to_base
        multiplier = (outer_qty * inner_qty).quantize(Decimal('0.000001'))
        outer_name = Unit.objects.filter(pk=outer_unit_id).values_list('name', flat=True).first() or ''
        inner_name = Unit.objects.filter(pk=inner_unit_id).values_list('name', flat=True).first() or ''
        label = (
            f'{fmt(outer_qty)}{outer_name.strip().upper()} x '
            f'{fmt(inner_qty)}{inner_name.strip().upper()} = '
            f'{fmt(multiplier)}{inner_name.strip().upper()}'
        )
        row.outer_qty = outer_qty
        row.outer_unit_id = outer_unit_id
        row.inner_qty = inner_qty
        row.inner_unit_id = inner_unit_id
        row.multiplier = multiplier
        row.shape_format_label = label
        if row.cost == Decimal('0'):
            row.cost = None
        row.save(
            update_fields=[
                'outer_qty',
                'outer_unit_id',
                'inner_qty',
                'inner_unit_id',
                'multiplier',
                'shape_format_label',
                'cost',
            ],
        )


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0005_productsupplier_and_more'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='productsupplier',
            name='chk_product_supplier_conversion_positive',
        ),
        migrations.AddField(
            model_name='productsupplier',
            name='outer_qty',
            field=models.DecimalField(decimal_places=6, max_digits=16, null=True),
        ),
        migrations.AddField(
            model_name='productsupplier',
            name='outer_unit',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='supplier_products_outer',
                to='product.unit',
            ),
        ),
        migrations.AddField(
            model_name='productsupplier',
            name='inner_qty',
            field=models.DecimalField(decimal_places=6, max_digits=16, null=True),
        ),
        migrations.AddField(
            model_name='productsupplier',
            name='inner_unit',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='supplier_products_inner',
                to='product.unit',
            ),
        ),
        migrations.AddField(
            model_name='productsupplier',
            name='multiplier',
            field=models.DecimalField(decimal_places=6, max_digits=16, null=True),
        ),
        migrations.AddField(
            model_name='productsupplier',
            name='shape_format_label',
            field=models.CharField(max_length=128, null=True),
        ),
        migrations.AddField(
            model_name='productsupplier',
            name='is_default',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='productsupplier',
            name='cost',
            field=models.DecimalField(
                blank=True, decimal_places=6, max_digits=16, null=True,
            ),
        ),
        migrations.RunPython(forwards_backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='productsupplier',
            name='outer_qty',
            field=models.DecimalField(decimal_places=6, max_digits=16),
        ),
        migrations.AlterField(
            model_name='productsupplier',
            name='outer_unit',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='supplier_products_outer',
                to='product.unit',
            ),
        ),
        migrations.AlterField(
            model_name='productsupplier',
            name='inner_qty',
            field=models.DecimalField(decimal_places=6, max_digits=16),
        ),
        migrations.AlterField(
            model_name='productsupplier',
            name='inner_unit',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='supplier_products_inner',
                to='product.unit',
            ),
        ),
        migrations.AlterField(
            model_name='productsupplier',
            name='multiplier',
            field=models.DecimalField(decimal_places=6, max_digits=16),
        ),
        migrations.AlterField(
            model_name='productsupplier',
            name='shape_format_label',
            field=models.CharField(max_length=128),
        ),
        migrations.RemoveField(
            model_name='productsupplier',
            name='pack_unit',
        ),
        migrations.RemoveField(
            model_name='productsupplier',
            name='conversion_to_base',
        ),
        migrations.AddConstraint(
            model_name='productsupplier',
            constraint=models.CheckConstraint(
                check=models.Q(('outer_qty__gt', 0)),
                name='chk_product_supplier_outer_qty_positive',
            ),
        ),
        migrations.AddConstraint(
            model_name='productsupplier',
            constraint=models.CheckConstraint(
                check=models.Q(('inner_qty__gt', 0)),
                name='chk_product_supplier_inner_qty_positive',
            ),
        ),
        migrations.AddConstraint(
            model_name='productsupplier',
            constraint=models.CheckConstraint(
                check=models.Q(('multiplier__gt', 0)),
                name='chk_product_supplier_multiplier_positive',
            ),
        ),
        migrations.AddConstraint(
            model_name='productsupplier',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_default', True)),
                fields=('product',),
                name='uniq_product_supplier_default',
            ),
        ),
    ]
