from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0010_stock_unit_chunk10'),
    ]

    operations = [
        migrations.CreateModel(
            name='StockEntryLabel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label_format', models.CharField(choices=[('pallet', 'Pallet'), ('box', 'Box')], max_length=16)),
                ('label_count', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('printed', 'Printed'), ('verified', 'Verified')], default='pending', max_length=16)),
                ('verified_count', models.PositiveIntegerField(default=0)),
                ('printed_at', models.DateTimeField(blank=True, null=True)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('actor_user_id', models.IntegerField(blank=True, null=True)),
                ('lan_username', models.CharField(blank=True, max_length=64, null=True)),
                ('source_workstation', models.CharField(blank=True, max_length=128, null=True)),
                ('stock_entry', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='label', to='stock_ledger.stockentry')),
            ],
            options={
                'db_table': 'stock_entry_label',
            },
        ),
        migrations.AddConstraint(
            model_name='stockentrylabel',
            constraint=models.CheckConstraint(check=models.Q(('label_format__in', ['pallet', 'box'])), name='chk_stock_entry_label_format'),
        ),
        migrations.AddConstraint(
            model_name='stockentrylabel',
            constraint=models.CheckConstraint(check=models.Q(('status__in', ['pending', 'printed', 'verified'])), name='chk_stock_entry_label_status'),
        ),
        migrations.AddConstraint(
            model_name='stockentrylabel',
            constraint=models.CheckConstraint(check=models.Q(('label_count__gte', 1)), name='chk_stock_entry_label_count'),
        ),
    ]
