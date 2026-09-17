from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0019_product_buy_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='buy_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('key_product', 'Key product'),
                    ('other_product', 'Other product'),
                    ('planner_product', 'Planner product'),
                ],
                max_length=16,
                null=True,
            ),
        ),
    ]
