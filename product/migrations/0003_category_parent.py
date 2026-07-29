# Generated manually — parent_id already exists in DB from legacy import.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0002_productacceptance_productingredientlabel_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='category',
                    name='parent',
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='children',
                        to='product.category',
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
