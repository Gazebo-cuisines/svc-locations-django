from django.db import migrations, models


def _flag_veg_fresh(apps, schema_editor):
    Category = apps.get_model('product', 'Category')
    Category.objects.filter(name__iexact='VEG - FRESH').update(direct_consume=True)


def _unflag_veg_fresh(apps, schema_editor):
    Category = apps.get_model('product', 'Category')
    Category.objects.filter(name__iexact='VEG - FRESH').update(direct_consume=False)


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0017_productsupplier_sage_product_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='direct_consume',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(_flag_veg_fresh, _unflag_veg_fresh),
    ]
