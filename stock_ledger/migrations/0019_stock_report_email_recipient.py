from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0018_stock_entry_device_serial'),
    ]

    operations = [
        migrations.CreateModel(
            name='StockReportEmailRecipient',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True,
                    primary_key=True,
                    serialize=False,
                    verbose_name='ID',
                )),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'stock_report_email_recipient',
                'ordering': ['email'],
            },
        ),
    ]
