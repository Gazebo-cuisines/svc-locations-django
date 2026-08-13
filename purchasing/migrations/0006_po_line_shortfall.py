from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0005_po_line_label'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorderline',
            name='qty_rejected',
            field=models.DecimalField(
                decimal_places=6, default=Decimal('0'), max_digits=16,
            ),
        ),
        migrations.AddField(
            model_name='purchaseorderline',
            name='shortfall_reason',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddConstraint(
            model_name='purchaseorderline',
            constraint=models.CheckConstraint(
                check=models.Q(qty_rejected__gte=0),
                name='chk_po_line_qty_rejected_nonneg',
            ),
        ),
    ]
