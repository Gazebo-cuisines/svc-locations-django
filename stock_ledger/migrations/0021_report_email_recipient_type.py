from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0020_stock_entry_label_printed_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockreportemailrecipient',
            name='report_type',
            field=models.CharField(default='closing_stock', max_length=32),
        ),
        migrations.AlterField(
            model_name='stockreportemailrecipient',
            name='email',
            field=models.EmailField(max_length=254),
        ),
        migrations.AddConstraint(
            model_name='stockreportemailrecipient',
            constraint=models.UniqueConstraint(
                fields=('email', 'report_type'),
                name='uniq_stock_report_email_type',
            ),
        ),
    ]
