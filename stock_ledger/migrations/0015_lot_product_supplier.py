from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0013_product_goods_in_masters'),
        ('stock_ledger', '0014_stock_fifo_override'),
    ]

    operations = [
        migrations.AddField(
            model_name='stocklot',
            name='product_supplier',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='stock_lots',
                to='product.productsupplier',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='stocklot',
            name='uq_stock_lot_identity',
        ),
        migrations.AddConstraint(
            model_name='stocklot',
            constraint=models.UniqueConstraint(
                fields=[
                    'product',
                    'trace_number',
                    'production_date',
                    'use_by',
                    'recipe_version',
                    'shape_format',
                    'product_supplier',
                ],
                name='uq_stock_lot_identity',
            ),
        ),
    ]
