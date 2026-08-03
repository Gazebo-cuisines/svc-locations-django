from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0008_downtime_entry'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockentry',
            name='po_number',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddIndex(
            model_name='stockentry',
            index=models.Index(fields=['po_number'], name='idx_stock_entry_po_number'),
        ),
    ]
