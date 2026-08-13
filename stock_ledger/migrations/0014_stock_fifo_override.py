from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0013_product_goods_in_masters'),
        ('stock_ledger', '0013_stock_entry_posting'),
    ]

    operations = [
        migrations.CreateModel(
            name='StockFifoOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.TextField()),
                ('actor_user_id', models.IntegerField(blank=True, null=True)),
                ('lan_username', models.CharField(blank=True, max_length=64, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='fifo_overrides', to='product.product')),
                ('recommended_lot', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='fifo_overrides_recommended', to='stock_ledger.stocklot')),
                ('scanned_lot', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='fifo_overrides_scanned', to='stock_ledger.stocklot')),
                ('stock_entry', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='fifo_override', to='stock_ledger.stockentry')),
            ],
            options={
                'db_table': 'stock_fifo_override',
            },
        ),
        migrations.AddIndex(
            model_name='stockfifooverride',
            index=models.Index(fields=['product', '-created_at'], name='idx_fifo_override_product'),
        ),
    ]
