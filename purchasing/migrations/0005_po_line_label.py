from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0004_goods_in_attachments'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorderline',
            name='label_format',
            field=models.CharField(blank=True, max_length=16, null=True),
        ),
        migrations.AddField(
            model_name='purchaseorderline',
            name='label_count',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='purchaseorderline',
            constraint=models.CheckConstraint(
                check=(
                    models.Q(('label_format__isnull', True))
                    | models.Q(('label_format__in', ['pallet', 'box']))
                ),
                name='chk_po_line_label_format',
            ),
        ),
    ]
