from decimal import Decimal

from django.db import models


class PurchaseOrderStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    ORDERED = 'ordered', 'Ordered'
    PARTIAL = 'partial', 'Partial'
    RECEIVED = 'received', 'Received'
    CANCELLED = 'cancelled', 'Cancelled'


class PurchaseOrderSource(models.TextChoices):
    MANUAL = 'manual', 'Manual'
    LEGACY_CSV = 'legacy_csv', 'Legacy CSV'
    SAGE = 'sage', 'Sage'


class PurchaseOrderHistoryEvent(models.TextChoices):
    ACCEPT = 'accept', 'Accept'
    REJECT = 'reject', 'Reject'
    NON_CONFORMANCE = 'non_conformance', 'Non-conformance'
    NOTE = 'note', 'Note'


class PurchaseOrder(models.Model):
    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    supplier = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='purchase_orders',
    )
    ship_to_location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inbound_purchase_orders',
    )
    status = models.CharField(
        max_length=16,
        choices=PurchaseOrderStatus.choices,
        default=PurchaseOrderStatus.DRAFT,
    )
    ordered_at = models.DateField(null=True, blank=True)
    expected_at = models.DateField(null=True, blank=True)
    remarks = models.CharField(max_length=256, null=True, blank=True)

    source = models.CharField(
        max_length=16,
        choices=PurchaseOrderSource.choices,
        default=PurchaseOrderSource.MANUAL,
    )
    external_number = models.CharField(max_length=64, null=True, blank=True)

    # Header goods-in (filled in later chunks)
    delivery_trace_number = models.CharField(max_length=64, null=True, blank=True)
    vehicle_temperature = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True,
    )
    reject_delivery = models.BooleanField(default=False)
    header_checks = models.JSONField(default=dict, blank=True)
    header_template_id = models.BigIntegerField(null=True, blank=True)
    header_template_version = models.IntegerField(null=True, blank=True)

    checked_by_user_id = models.IntegerField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    qc_tl_checked_by_user_id = models.IntegerField(null=True, blank=True)
    qc_tl_checked_at = models.DateTimeField(null=True, blank=True)
    qc_tl_comment = models.TextField(null=True, blank=True)

    total_net = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    created_by_user_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'po_purchase_order'
        ordering = ['-id']
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    status__in=[
                        PurchaseOrderStatus.DRAFT,
                        PurchaseOrderStatus.ORDERED,
                        PurchaseOrderStatus.PARTIAL,
                        PurchaseOrderStatus.RECEIVED,
                        PurchaseOrderStatus.CANCELLED,
                    ],
                ),
                name='chk_po_status',
            ),
            models.CheckConstraint(
                check=models.Q(
                    source__in=[
                        PurchaseOrderSource.MANUAL,
                        PurchaseOrderSource.LEGACY_CSV,
                        PurchaseOrderSource.SAGE,
                    ],
                ),
                name='chk_po_source',
            ),
            models.UniqueConstraint(
                fields=['source', 'external_number'],
                condition=models.Q(external_number__isnull=False),
                name='uniq_po_source_external_number',
            ),
        ]
        indexes = [
            models.Index(fields=['status'], name='idx_po_status'),
            models.Index(fields=['supplier'], name='idx_po_supplier'),
            models.Index(fields=['expected_at'], name='idx_po_expected_at'),
        ]

    def __str__(self):
        return f'po:{self.id}:{self.number or "draft"}'


class PurchaseOrderLine(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    line_no = models.PositiveIntegerField()
    product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='purchase_order_lines',
    )
    product_supplier = models.ForeignKey(
        'product.ProductSupplier',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='purchase_order_lines',
    )
    qty_ordered = models.DecimalField(max_digits=16, decimal_places=6)
    qty_received = models.DecimalField(
        max_digits=16, decimal_places=6, default=Decimal('0'),
    )
    qty_balance = models.DecimalField(max_digits=16, decimal_places=6)
    unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='purchase_order_lines',
    )
    unit_cost = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    multiplier = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    shape_format_label = models.CharField(max_length=128, null=True, blank=True)

    production_date = models.DateField(null=True, blank=True)
    use_by = models.DateField(null=True, blank=True)
    trace_number = models.CharField(max_length=64, null=True, blank=True)
    product_temperature = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True,
    )

    line_checks = models.JSONField(default=dict, blank=True)
    line_template_id = models.BigIntegerField(null=True, blank=True)
    line_template_version = models.IntegerField(null=True, blank=True)
    line_check_ok = models.BooleanField(default=False)
    line_closed = models.BooleanField(default=False)
    stock_in_done = models.BooleanField(default=False)
    last_receipt_entry_id = models.BigIntegerField(null=True, blank=True)

    remarks = models.CharField(max_length=256, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'po_purchase_order_line'
        ordering = ['purchase_order_id', 'line_no']
        constraints = [
            models.UniqueConstraint(
                fields=['purchase_order', 'line_no'],
                name='uniq_po_line_no',
            ),
            models.CheckConstraint(
                check=models.Q(qty_ordered__gt=0),
                name='chk_po_line_qty_ordered_positive',
            ),
            models.CheckConstraint(
                check=models.Q(qty_received__gte=0),
                name='chk_po_line_qty_received_nonneg',
            ),
            models.CheckConstraint(
                check=models.Q(qty_balance__gte=0),
                name='chk_po_line_qty_balance_nonneg',
            ),
        ]
        indexes = [
            models.Index(fields=['product'], name='idx_po_line_product'),
            models.Index(
                fields=['purchase_order', 'line_closed'],
                name='idx_po_line_open',
            ),
        ]

    def __str__(self):
        return f'po_line:{self.purchase_order_id}:{self.line_no}'


class PurchaseOrderHistory(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name='history',
    )
    event_type = models.CharField(
        max_length=32,
        choices=PurchaseOrderHistoryEvent.choices,
    )
    remarks = models.TextField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    actor_user_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'po_purchase_order_history'
        ordering = ['-id']
        indexes = [
            models.Index(
                fields=['purchase_order', 'created_at'],
                name='idx_po_history_po_created',
            ),
        ]

    def __str__(self):
        return f'po_history:{self.purchase_order_id}:{self.event_type}'
