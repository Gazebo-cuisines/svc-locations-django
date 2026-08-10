# Generated manually

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0011_product_category_nullable'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='range',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='products',
                to='product.range',
            ),
        ),
    ]
