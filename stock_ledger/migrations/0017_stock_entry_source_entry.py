from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0016_drop_stock_period'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockentry',
            name='source_entry',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='sticker_draws',
                to='stock_ledger.stockentry',
            ),
        ),
        migrations.AddIndex(
            model_name='stockentry',
            index=models.Index(
                fields=['source_entry'],
                name='idx_stock_entry_source_entry',
            ),
        ),
    ]
