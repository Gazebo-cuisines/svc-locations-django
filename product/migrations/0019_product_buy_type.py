from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0018_category_direct_consume'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='buy_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('everyday', 'Everyday'),
                    ('normal', 'Normal'),
                    ('planner_orders', 'Planner orders'),
                ],
                max_length=16,
                null=True,
            ),
        ),
    ]
