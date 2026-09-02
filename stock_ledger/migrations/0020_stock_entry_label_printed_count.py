from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0019_stock_report_email_recipient'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockentrylabel',
            name='printed_count',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
