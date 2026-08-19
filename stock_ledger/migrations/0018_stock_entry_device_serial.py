from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0017_stock_entry_source_entry'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockentry',
            name='device_serial',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddIndex(
            model_name='stockentry',
            index=models.Index(fields=['device_serial'], name='idx_stock_entry_device'),
        ),
    ]
