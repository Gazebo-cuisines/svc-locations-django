from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0012_stock_entry_label_scan'),
    ]

    operations = [
        migrations.CreateModel(
            name='StockEntryPosting',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('posted', 'Posted'), ('cancelled', 'Cancelled')], default='queued', max_length=16)),
                ('queued_at', models.DateTimeField()),
                ('posted_at', models.DateTimeField(blank=True, null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('actor_user_id', models.IntegerField(blank=True, null=True)),
                ('lan_username', models.CharField(blank=True, max_length=64, null=True)),
                ('source_workstation', models.CharField(blank=True, max_length=128, null=True)),
                ('meta', models.JSONField(blank=True, default=dict)),
                ('stock_entry', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='posting', to='stock_ledger.stockentry')),
            ],
            options={
                'db_table': 'stock_entry_posting',
            },
        ),
        migrations.AddConstraint(
            model_name='stockentryposting',
            constraint=models.CheckConstraint(check=models.Q(('status__in', ['queued', 'posted', 'cancelled'])), name='chk_stock_entry_posting_status'),
        ),
        migrations.AddIndex(
            model_name='stockentryposting',
            index=models.Index(fields=['status', 'queued_at'], name='idx_stock_entry_posting_status'),
        ),
    ]
