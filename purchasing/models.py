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
    CREATE = 'create', 'Create'
    UPDATE = 'update', 'Update'
    ACCEPT = 'accept', 'Accept'
    REJECT = 'reject', 'Reject'
    NON_CONFORMANCE = 'non_conformance', 'Non-conformance'
    NOTE = 'note', 'Note'


class LineShortfallReason(models.TextChoices):
    SHORT_DELIVERY = 'short_delivery', 'Rest coming later'
    WRONG_PRODUCT = 'wrong_product', 'Wrong product'
    SHORT_USEBY = 'short_useby', 'Use-by too short'
    TECHNICAL = 'technical_issue', 'Technical issue'
    DAMAGED = 'damaged', 'Damaged'
    QUALITY = 'quality', 'Quality fail'
    OTHER = 'other', 'Other'


SHORTFALL_AWAIT_REASONS = frozenset({LineShortfallReason.SHORT_DELIVERY})


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
    delivery_at = models.DateField(null=True, blank=True)
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
    qty_rejected = models.DecimalField(
        max_digits=16, decimal_places=6, default=Decimal('0'),
    )
    qty_balance = models.DecimalField(max_digits=16, decimal_places=6)
    shortfall_reason = models.CharField(max_length=32, null=True, blank=True)
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

    # Admin-chosen physical labels for goods-in (warehouse does not pick these).
    label_format = models.CharField(max_length=16, null=True, blank=True)
    label_count = models.PositiveIntegerField(null=True, blank=True)

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
                check=models.Q(qty_rejected__gte=0),
                name='chk_po_line_qty_rejected_nonneg',
            ),
            models.CheckConstraint(
                check=models.Q(qty_balance__gte=0),
                name='chk_po_line_qty_balance_nonneg',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(label_format__isnull=True)
                    | models.Q(label_format__in=['pallet', 'box'])
                ),
                name='chk_po_line_label_format',
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


class PurchaseOrderDeliveryStatus(models.TextChoices):
    OPEN = 'open', 'Open'
    REJECTED = 'rejected', 'Rejected'
    RECEIVED = 'received', 'Received'


class PurchaseOrderDelivery(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name='deliveries',
    )
    status = models.CharField(
        max_length=16,
        choices=PurchaseOrderDeliveryStatus.choices,
        default=PurchaseOrderDeliveryStatus.OPEN,
    )
    delivery_at = models.DateField(null=True, blank=True)
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'po_purchase_order_delivery'
        ordering = ['id']
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    status__in=[
                        PurchaseOrderDeliveryStatus.OPEN,
                        PurchaseOrderDeliveryStatus.REJECTED,
                        PurchaseOrderDeliveryStatus.RECEIVED,
                    ],
                ),
                name='chk_po_delivery_status',
            ),
            models.UniqueConstraint(
                fields=['purchase_order'],
                condition=models.Q(status=PurchaseOrderDeliveryStatus.OPEN),
                name='uniq_po_one_open_delivery',
            ),
        ]
        indexes = [
            models.Index(
                fields=['purchase_order', 'status'],
                name='idx_po_delivery_po_status',
            ),
        ]

    def __str__(self):
        return f'po_delivery:{self.purchase_order_id}:{self.id}:{self.status}'


class PurchaseOrderDeliveryLine(models.Model):
    delivery = models.ForeignKey(
        PurchaseOrderDelivery,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    po_line = models.ForeignKey(
        PurchaseOrderLine,
        on_delete=models.CASCADE,
        related_name='delivery_lines',
    )
    qty_received = models.DecimalField(
        max_digits=16, decimal_places=6, default=Decimal('0'),
    )
    qty_rejected = models.DecimalField(
        max_digits=16, decimal_places=6, default=Decimal('0'),
    )
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
    last_receipt_entry_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'po_purchase_order_delivery_line'
        ordering = ['delivery_id', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['delivery', 'po_line'],
                name='uniq_po_delivery_line',
            ),
        ]

    def __str__(self):
        return f'po_dline:{self.delivery_id}:{self.po_line_id}'


class PurchaseOrderHistory(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name='history',
    )
    delivery = models.ForeignKey(
        'PurchaseOrderDelivery',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
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


class GoodsInAttachmentKind(models.TextChoices):
    PHOTO = 'photo', 'Photo'
    COA = 'coa', 'COA/COC'
    DELIVERY_NOTE = 'delivery_note', 'Delivery note'
    OTHER = 'other', 'Other'


class GoodsInAttachment(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    line = models.ForeignKey(
        PurchaseOrderLine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attachments',
    )
    history = models.ForeignKey(
        PurchaseOrderHistory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attachments',
    )
    delivery = models.ForeignKey(
        PurchaseOrderDelivery,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attachments',
    )
    kind = models.CharField(
        max_length=32,
        choices=GoodsInAttachmentKind.choices,
        default=GoodsInAttachmentKind.PHOTO,
    )
    s3_key = models.CharField(max_length=512)
    content_type = models.CharField(max_length=64, null=True, blank=True)
    original_filename = models.CharField(max_length=255, null=True, blank=True)
    uploaded_by_user_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'po_goods_in_attachment'
        ordering = ['-id']
        indexes = [
            models.Index(
                fields=['purchase_order', 'kind'],
                name='idx_po_attachment_po_kind',
            ),
        ]

    def __str__(self):
        return f'po_attach:{self.purchase_order_id}:{self.kind}:{self.id}'


class GoodsInCheckScope(models.TextChoices):
    HEADER = 'header', 'Header'
    LINE = 'line', 'Line'


class GoodsInInputType(models.TextChoices):
    BOOL = 'bool', 'Yes/No'
    DECIMAL = 'decimal', 'Decimal'
    TEXT = 'text', 'Text'
    DATE = 'date', 'Date'


class GoodsInFailWhen(models.TextChoices):
    FALSE = 'false', 'Fail when false'
    TRUE = 'true', 'Fail when true'
    OUT_OF_RANGE = 'out_of_range', 'Fail when out of range'


class GoodsInCheckTemplate(models.Model):
    """Versioned goods-in checklist (GFF001F document control lives here)."""

    name = models.CharField(max_length=128)
    goods_in_type = models.CharField(max_length=16)  # ProductGoodsInType values
    # Null storage_regime = fallback for this goods_in_type.
    storage_regime = models.CharField(max_length=16, null=True, blank=True)
    scope = models.CharField(max_length=16, choices=GoodsInCheckScope.choices)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    document_no = models.CharField(max_length=32, default='GFF001F')
    issue_no = models.PositiveIntegerField(default=15)
    issue_date = models.DateField(null=True, blank=True)
    review_date = models.DateField(null=True, blank=True)
    previous_issue_date = models.DateField(null=True, blank=True)
    reason_for_change = models.CharField(max_length=256, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'po_goods_in_check_template'
        ordering = ['goods_in_type', 'scope', '-version']
        constraints = [
            models.UniqueConstraint(
                fields=['goods_in_type', 'storage_regime', 'scope', 'version'],
                name='uniq_goods_in_template_key_version',
            ),
            models.CheckConstraint(
                check=models.Q(scope__in=['header', 'line']),
                name='chk_goods_in_template_scope',
            ),
        ]
        indexes = [
            models.Index(
                fields=['goods_in_type', 'storage_regime', 'scope', 'is_active'],
                name='idx_goods_in_template_lookup',
            ),
        ]

    def __str__(self):
        regime = self.storage_regime or '*'
        return (
            f'gin_tpl:{self.id}:{self.goods_in_type}/{regime}/'
            f'{self.scope}:v{self.version}'
        )


class GoodsInCheckItem(models.Model):
    template = models.ForeignKey(
        GoodsInCheckTemplate,
        on_delete=models.CASCADE,
        related_name='items',
    )
    code = models.CharField(max_length=64)
    label = models.CharField(max_length=256)
    input_type = models.CharField(max_length=16, choices=GoodsInInputType.choices)
    required = models.BooleanField(default=True)
    is_critical = models.BooleanField(default=False)
    fail_when = models.CharField(
        max_length=16,
        choices=GoodsInFailWhen.choices,
        null=True,
        blank=True,
    )
    min_value = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
    )
    max_value = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
    )
    # e.g. product.temp_bounds, product.min_shelf_life
    source = models.CharField(max_length=64, null=True, blank=True)
    allows_comment = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'po_goods_in_check_item'
        ordering = ['template_id', 'sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['template', 'code'],
                name='uniq_goods_in_item_code',
            ),
            models.CheckConstraint(
                check=models.Q(input_type__in=['bool', 'decimal', 'text', 'date']),
                name='chk_goods_in_item_input_type',
            ),
        ]

    def __str__(self):
        return f'gin_item:{self.template_id}:{self.code}'


class GoodsInAnswer(models.Model):
    """One answered goods-in question. Unanswered = no row."""

    delivery = models.ForeignKey(
        PurchaseOrderDelivery,
        on_delete=models.CASCADE,
        related_name='answers',
        null=True,
        blank=True,
    )
    delivery_line = models.ForeignKey(
        PurchaseOrderDeliveryLine,
        on_delete=models.CASCADE,
        related_name='answers',
        null=True,
        blank=True,
    )
    adhoc_session = models.ForeignKey(
        'AdhocGoodsInSession',
        on_delete=models.CASCADE,
        related_name='answers',
        null=True,
        blank=True,
    )
    adhoc_line = models.ForeignKey(
        'AdhocGoodsInLine',
        on_delete=models.CASCADE,
        related_name='answers',
        null=True,
        blank=True,
    )
    scope = models.CharField(max_length=16, choices=GoodsInCheckScope.choices)
    check_code = models.CharField(max_length=64)
    input_type = models.CharField(max_length=16, choices=GoodsInInputType.choices)
    value_bool = models.BooleanField(null=True, blank=True)
    value_decimal = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    value_text = models.TextField(null=True, blank=True)
    value_date = models.DateField(null=True, blank=True)
    comment = models.TextField(null=True, blank=True)
    answered_by_user_id = models.IntegerField(null=True, blank=True)
    answered_at = models.DateTimeField()

    class Meta:
        db_table = 'po_goods_in_answer'
        ordering = ['id']
        constraints = [
            models.CheckConstraint(
                check=models.Q(scope__in=['header', 'line']),
                name='chk_gin_answer_scope',
            ),
            models.CheckConstraint(
                check=models.Q(input_type__in=['bool', 'decimal', 'text', 'date']),
                name='chk_gin_answer_input_type',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        delivery_id__isnull=False,
                        adhoc_session_id__isnull=True,
                        adhoc_line_id__isnull=True,
                    )
                    & (
                        models.Q(
                            scope='header',
                            delivery_line_id__isnull=True,
                        )
                        | models.Q(
                            scope='line',
                            delivery_line_id__isnull=False,
                        )
                    )
                )
                | (
                    models.Q(
                        adhoc_session_id__isnull=False,
                        delivery_id__isnull=True,
                        delivery_line_id__isnull=True,
                    )
                    & (
                        models.Q(scope='header', adhoc_line_id__isnull=True)
                        | models.Q(scope='line', adhoc_line_id__isnull=False)
                    )
                ),
                name='chk_gin_answer_parent',
            ),
            models.UniqueConstraint(
                fields=['delivery', 'check_code'],
                condition=models.Q(scope='header', delivery_id__isnull=False),
                name='uniq_gin_answer_po_header',
            ),
            models.UniqueConstraint(
                fields=['delivery_line', 'check_code'],
                condition=models.Q(delivery_line_id__isnull=False),
                name='uniq_gin_answer_po_line',
            ),
            models.UniqueConstraint(
                fields=['adhoc_session', 'check_code'],
                condition=models.Q(scope='header', adhoc_session_id__isnull=False),
                name='uniq_gin_answer_adhoc_header',
            ),
            models.UniqueConstraint(
                fields=['adhoc_line', 'check_code'],
                condition=models.Q(adhoc_line_id__isnull=False),
                name='uniq_gin_answer_adhoc_line',
            ),
        ]
        indexes = [
            models.Index(
                fields=['delivery', 'scope'],
                name='idx_gin_answer_delivery',
            ),
            models.Index(
                fields=['adhoc_session', 'scope'],
                name='idx_gin_answer_adhoc',
            ),
        ]

    def __str__(self):
        return f'gin_answer:{self.scope}:{self.check_code}:{self.id}'


class AdhocGoodsInStatus(models.TextChoices):
    OPEN = 'open', 'Open'
    REJECTED = 'rejected', 'Rejected'
    QC_COMPLETE = 'qc_complete', 'QC complete'
    RECEIVED = 'received', 'Received'


class AdhocGoodsInSession(models.Model):
    """Without-PO goods-in QC visit. Receive posts stock without a PurchaseOrder."""

    status = models.CharField(
        max_length=16,
        choices=AdhocGoodsInStatus.choices,
        default=AdhocGoodsInStatus.OPEN,
    )
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='adhoc_goods_in_sessions',
    )
    product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='adhoc_goods_in_sessions',
    )
    delivery_at = models.DateField(null=True, blank=True)
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
    # Chunk 3 — receive (never linked to PurchaseOrder)
    item_po_ref = models.CharField(max_length=64, null=True, blank=True)
    label_format = models.CharField(max_length=16, null=True, blank=True)
    label_count = models.PositiveIntegerField(null=True, blank=True)
    receive_idempotency_key = models.CharField(max_length=128, null=True, blank=True)
    created_by_user_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'po_adhoc_goods_in_session'
        ordering = ['-id']
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    status__in=[
                        AdhocGoodsInStatus.OPEN,
                        AdhocGoodsInStatus.REJECTED,
                        AdhocGoodsInStatus.QC_COMPLETE,
                        AdhocGoodsInStatus.RECEIVED,
                    ],
                ),
                name='chk_adhoc_gin_status',
            ),
        ]
        indexes = [
            models.Index(
                fields=['status', 'created_at'],
                name='idx_adhoc_gin_status_created',
            ),
            models.Index(
                fields=['product', 'status'],
                name='idx_adhoc_gin_product_status',
            ),
        ]

    def __str__(self):
        return f'adhoc_gin:{self.id}:{self.status}'


class AdhocGoodsInLine(models.Model):
    session = models.OneToOneField(
        AdhocGoodsInSession,
        on_delete=models.CASCADE,
        related_name='line',
    )
    product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='adhoc_goods_in_lines',
    )
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
    product_supplier = models.ForeignKey(
        'product.ProductSupplier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='adhoc_goods_in_lines',
    )
    qty_entered = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    stock_qty = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    shape_format_label = models.CharField(max_length=128, null=True, blank=True)
    shape_other = models.JSONField(null=True, blank=True)
    last_receipt_entry_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'po_adhoc_goods_in_line'

    def __str__(self):
        return f'adhoc_gin_line:{self.session_id}:{self.product_id}'
