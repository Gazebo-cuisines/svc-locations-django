from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0016_productsupplier_moq'),
    ]

    operations = [
        migrations.AddField(
            model_name='productsupplier',
            name='sage_product_code',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
