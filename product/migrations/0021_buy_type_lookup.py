from django.db import migrations, models
import django.db.models.deletion


def _seed_buy_types(apps, schema_editor):
    BuyType = apps.get_model('product', 'BuyType')
    for name in ('Key product', 'Other product', 'Planner product'):
        BuyType.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0020_alter_product_buy_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='BuyType',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('name', models.CharField(max_length=64, unique=True)),
            ],
            options={
                'db_table': 'product_buy_type',
                'ordering': ['id'],
            },
        ),
        migrations.RemoveField(
            model_name='product',
            name='buy_type',
        ),
        migrations.AddField(
            model_name='product',
            name='buy_type',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='products',
                to='product.buytype',
            ),
        ),
        migrations.RunPython(_seed_buy_types, migrations.RunPython.noop),
    ]
