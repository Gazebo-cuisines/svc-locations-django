from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0011_stock_entry_label'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockentrylabel',
            name='meta',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='StockEntryLabelScan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scanned_at', models.DateTimeField()),
                ('code', models.CharField(max_length=64)),
                ('result', models.CharField(choices=[('ok', 'Ok'), ('mismatch', 'Mismatch')], max_length=16)),
                ('actor_user_id', models.IntegerField(blank=True, null=True)),
                ('lan_username', models.CharField(blank=True, max_length=64, null=True)),
                ('source_workstation', models.CharField(blank=True, max_length=128, null=True)),
                ('meta', models.JSONField(blank=True, default=dict)),
                ('label', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='scans', to='stock_ledger.stockentrylabel')),
                ('stock_entry', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='label_scans', to='stock_ledger.stockentry')),
            ],
            options={
                'db_table': 'stock_entry_label_scan',
                'ordering': ['-scanned_at', '-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='stockentrylabelscan',
            constraint=models.CheckConstraint(check=models.Q(('result__in', ['ok', 'mismatch'])), name='chk_stock_entry_label_scan_result'),
        ),
        migrations.AddIndex(
            model_name='stockentrylabelscan',
            index=models.Index(fields=['stock_entry', 'scanned_at'], name='idx_entry_label_scan_entry'),
        ),
        migrations.AddIndex(
            model_name='stockentrylabelscan',
            index=models.Index(fields=['label', 'scanned_at'], name='idx_entry_label_scan_label'),
        ),
    ]
