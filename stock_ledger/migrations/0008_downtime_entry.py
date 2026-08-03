from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0007_production_run'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='stockentry',
            name='chk_stock_entry_type',
        ),
        migrations.RemoveConstraint(
            model_name='stockentry',
            name='chk_stock_entry_qty_nonzero',
        ),
        migrations.AddConstraint(
            model_name='stockentry',
            constraint=models.CheckConstraint(
                check=models.Q(
                    entry_type__in=[
                        'receipt',
                        'issue',
                        'transfer_out',
                        'transfer_in',
                        'production_output',
                        'production_consumption',
                        'count_adjustment',
                        'disposal',
                        'reversal',
                        'downtime',
                    ]
                ),
                name='chk_stock_entry_type',
            ),
        ),
        migrations.AddConstraint(
            model_name='stockentry',
            constraint=models.CheckConstraint(
                check=(
                    ~models.Q(quantity=0)
                    | models.Q(entry_type='downtime')
                ),
                name='chk_stock_entry_qty_nonzero',
            ),
        ),
    ]
