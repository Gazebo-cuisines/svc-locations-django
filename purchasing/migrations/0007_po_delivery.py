from decimal import Decimal

from django.db import migrations, models


def backfill_deliveries(apps, schema_editor):
    PO = apps.get_model('purchasing', 'PurchaseOrder')
    Line = apps.get_model('purchasing', 'PurchaseOrderLine')
    Delivery = apps.get_model('purchasing', 'PurchaseOrderDelivery')
    DeliveryLine = apps.get_model('purchasing', 'PurchaseOrderDeliveryLine')

    qs = PO.objects.exclude(checked_at=None) | PO.objects.filter(reject_delivery=True)
    for po in qs.distinct().iterator():
        if Delivery.objects.filter(purchase_order_id=po.id).exists():
            continue
        has_qty = Line.objects.filter(purchase_order_id=po.id).filter(
            models.Q(qty_received__gt=0) | models.Q(qty_rejected__gt=0),
        ).exists()
        if po.reject_delivery:
            status = 'rejected'
        elif has_qty:
            status = 'received'
        else:
            status = 'open'
        delivery = Delivery.objects.create(
            purchase_order_id=po.id,
            status=status,
            delivery_at=po.delivery_at,
            delivery_trace_number=po.delivery_trace_number,
            vehicle_temperature=po.vehicle_temperature,
            reject_delivery=po.reject_delivery,
            header_checks=po.header_checks or {},
            header_template_id=po.header_template_id,
            header_template_version=po.header_template_version,
            checked_by_user_id=po.checked_by_user_id,
            checked_at=po.checked_at,
            qc_tl_checked_by_user_id=po.qc_tl_checked_by_user_id,
            qc_tl_checked_at=po.qc_tl_checked_at,
            qc_tl_comment=po.qc_tl_comment,
        )
        for line in Line.objects.filter(purchase_order_id=po.id):
            DeliveryLine.objects.create(
                delivery=delivery,
                po_line=line,
                qty_received=line.qty_received or Decimal('0'),
                qty_rejected=line.qty_rejected or Decimal('0'),
                production_date=line.production_date,
                use_by=line.use_by,
                trace_number=line.trace_number,
                product_temperature=line.product_temperature,
                line_checks=line.line_checks or {},
                line_template_id=line.line_template_id,
                line_template_version=line.line_template_version,
                line_check_ok=line.line_check_ok,
                last_receipt_entry_id=line.last_receipt_entry_id,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0006_po_line_shortfall'),
    ]

    operations = [
        migrations.CreateModel(
            name='PurchaseOrderDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('open', 'Open'), ('rejected', 'Rejected'), ('received', 'Received')], default='open', max_length=16)),
                ('delivery_at', models.DateField(blank=True, null=True)),
                ('delivery_trace_number', models.CharField(blank=True, max_length=64, null=True)),
                ('vehicle_temperature', models.DecimalField(blank=True, decimal_places=1, max_digits=5, null=True)),
                ('reject_delivery', models.BooleanField(default=False)),
                ('header_checks', models.JSONField(blank=True, default=dict)),
                ('header_template_id', models.BigIntegerField(blank=True, null=True)),
                ('header_template_version', models.IntegerField(blank=True, null=True)),
                ('checked_by_user_id', models.IntegerField(blank=True, null=True)),
                ('checked_at', models.DateTimeField(blank=True, null=True)),
                ('qc_tl_checked_by_user_id', models.IntegerField(blank=True, null=True)),
                ('qc_tl_checked_at', models.DateTimeField(blank=True, null=True)),
                ('qc_tl_comment', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('purchase_order', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='deliveries', to='purchasing.purchaseorder')),
            ],
            options={
                'db_table': 'po_purchase_order_delivery',
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='PurchaseOrderDeliveryLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('qty_received', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=16)),
                ('qty_rejected', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=16)),
                ('production_date', models.DateField(blank=True, null=True)),
                ('use_by', models.DateField(blank=True, null=True)),
                ('trace_number', models.CharField(blank=True, max_length=64, null=True)),
                ('product_temperature', models.DecimalField(blank=True, decimal_places=1, max_digits=5, null=True)),
                ('line_checks', models.JSONField(blank=True, default=dict)),
                ('line_template_id', models.BigIntegerField(blank=True, null=True)),
                ('line_template_version', models.IntegerField(blank=True, null=True)),
                ('line_check_ok', models.BooleanField(default=False)),
                ('last_receipt_entry_id', models.BigIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('delivery', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='lines', to='purchasing.purchaseorderdelivery')),
                ('po_line', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='delivery_lines', to='purchasing.purchaseorderline')),
            ],
            options={
                'db_table': 'po_purchase_order_delivery_line',
                'ordering': ['delivery_id', 'id'],
            },
        ),
        migrations.AddField(
            model_name='purchaseorderhistory',
            name='delivery',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='history', to='purchasing.purchaseorderdelivery'),
        ),
        migrations.AddField(
            model_name='goodsinattachment',
            name='delivery',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='attachments', to='purchasing.purchaseorderdelivery'),
        ),
        migrations.AddConstraint(
            model_name='purchaseorderdelivery',
            constraint=models.CheckConstraint(
                check=models.Q(status__in=['open', 'rejected', 'received']),
                name='chk_po_delivery_status',
            ),
        ),
        migrations.AddConstraint(
            model_name='purchaseorderdelivery',
            constraint=models.UniqueConstraint(
                condition=models.Q(status='open'),
                fields=('purchase_order',),
                name='uniq_po_one_open_delivery',
            ),
        ),
        migrations.AddConstraint(
            model_name='purchaseorderdeliveryline',
            constraint=models.UniqueConstraint(
                fields=('delivery', 'po_line'),
                name='uniq_po_delivery_line',
            ),
        ),
        migrations.AddIndex(
            model_name='purchaseorderdelivery',
            index=models.Index(fields=['purchase_order', 'status'], name='idx_po_delivery_po_status'),
        ),
        migrations.RunPython(backfill_deliveries, migrations.RunPython.noop),
    ]
