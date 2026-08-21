from decimal import Decimal

from django.db import models


class StockChainHead(models.Model):
    """Singleton row (id=1); head_entry_id FK to stock_entry added in a later migration."""

    id = models.PositiveSmallIntegerField(primary_key=True)
    head_entry_id = models.BigIntegerField(null=True, blank=True)
    head_hash = models.CharField(max_length=64, null=True, blank=True)
    entry_count = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField()

    class Meta:
        db_table = 'stock_chain_head'
        constraints = [
            models.CheckConstraint(
                check=models.Q(id=1),
                name='chk_stock_chain_head_singleton',
            ),
        ]

    def __str__(self):
        return f'stock_chain_head:{self.head_hash or "empty"}'


class StockUnitConversion(models.Model):
    unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        related_name='stock_unit_conversions',
    )
    product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_unit_conversions',
    )
    to_kg = models.DecimalField(max_digits=16, decimal_places=6)
    source = models.CharField(max_length=24)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'stock_unit_conversion'
        constraints = [
            models.UniqueConstraint(
                fields=['unit', 'product'],
                name='uq_stock_unit_conversion',
            ),
            models.CheckConstraint(
                check=models.Q(to_kg__gt=Decimal('0')),
                name='chk_stock_unit_conversion_pos',
            ),
        ]

    def __str__(self):
        scope = f'product:{self.product_id}' if self.product_id else 'global'
        return f'stock_unit_conversion:{self.unit_id}:{scope}'


class StockLotOrigin(models.TextChoices):
    PRODUCTION = 'production', 'Production'
    PURCHASE = 'purchase', 'Purchase'
    OPENING = 'opening', 'Opening'


class StockLotAmendmentField(models.TextChoices):
    USE_BY = 'use_by', 'Use by'
    PRODUCTION_DATE = 'production_date', 'Production date'
    SUPPLIER_LOT_CODE = 'supplier_lot_code', 'Supplier lot code'


class StockLot(models.Model):
    """Intrinsic lot identity. No updated_at — amendments are events."""

    product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='stock_lots',
    )
    recipe_version = models.ForeignKey(
        'recipe.RecipeVersion',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_lots',
    )
    shape_format = models.ForeignKey(
        'product.PurchaseShapeFormat',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_lots',
    )
    product_supplier = models.ForeignKey(
        'product.ProductSupplier',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_lots',
    )
    trace_number = models.CharField(max_length=32)
    supplier_lot_code = models.CharField(max_length=64, null=True, blank=True)
    origin = models.CharField(max_length=16, choices=StockLotOrigin.choices)
    production_date = models.DateField(null=True, blank=True)
    use_by = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Recipe version is identity for MADE stock — do not change after entries exist.
        if self.pk is not None:
            prev = (
                StockLot.objects
                .filter(pk=self.pk)
                .values_list('recipe_version_id', flat=True)
                .first()
            )
            if (
                prev is not None
                and prev != self.recipe_version_id
                and self.entries.exists()
            ):
                raise ValueError(
                    f'Cannot change recipe_version on lot_id={self.pk} '
                    f'after stock entries exist (was {prev}, tried {self.recipe_version_id})'
                )
        super().save(*args, **kwargs)

    class Meta:
        db_table = 'stock_lot'
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'product',
                    'trace_number',
                    'production_date',
                    'use_by',
                    'recipe_version',
                    'shape_format',
                    'product_supplier',
                ],
                name='uq_stock_lot_identity',
            ),
            models.CheckConstraint(
                check=models.Q(
                    origin__in=[
                        StockLotOrigin.PRODUCTION,
                        StockLotOrigin.PURCHASE,
                        StockLotOrigin.OPENING,
                    ]
                ),
                name='chk_stock_lot_origin',
            ),
        ]
        indexes = [
            models.Index(
                fields=['product', 'use_by'],
                name='idx_stock_lot_product_useby',
            ),
            models.Index(
                fields=['trace_number'],
                name='idx_stock_lot_trace',
            ),
        ]

    def __str__(self):
        return f'stock_lot:{self.id}:{self.trace_number}'


class StockLotAmendment(models.Model):
    """Append-only lot attribute change. Immutable via triggers in chunk 7."""

    lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='amendments',
    )
    field_name = models.CharField(
        max_length=32,
        choices=StockLotAmendmentField.choices,
    )
    old_value = models.CharField(max_length=64, null=True, blank=True)
    new_value = models.CharField(max_length=64, null=True, blank=True)
    reason = models.CharField(max_length=255)
    authorised_by_user_id = models.IntegerField()
    effective_at = models.DateTimeField()
    recorded_at = models.DateTimeField()

    class Meta:
        db_table = 'stock_lot_amendment'
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    field_name__in=[
                        StockLotAmendmentField.USE_BY,
                        StockLotAmendmentField.PRODUCTION_DATE,
                        StockLotAmendmentField.SUPPLIER_LOT_CODE,
                    ]
                ),
                name='chk_stock_lot_amendment_field',
            ),
        ]
        indexes = [
            models.Index(
                fields=['lot', 'recorded_at'],
                name='idx_stock_lot_amendment_lot',
            ),
        ]

    def __str__(self):
        return f'stock_lot_amendment:{self.id}:lot:{self.lot_id}:{self.field_name}'


class StockEntryType(models.TextChoices):
    RECEIPT = 'receipt', 'Receipt'
    ISSUE = 'issue', 'Issue'
    TRANSFER_OUT = 'transfer_out', 'Transfer out'
    TRANSFER_IN = 'transfer_in', 'Transfer in'
    PRODUCTION_OUTPUT = 'production_output', 'Production output'
    PRODUCTION_CONSUMPTION = 'production_consumption', 'Production consumption'
    COUNT_ADJUSTMENT = 'count_adjustment', 'Count adjustment'
    DISPOSAL = 'disposal', 'Disposal'
    REVERSAL = 'reversal', 'Reversal'
    DOWNTIME = 'downtime', 'Downtime'


class StockEntry(models.Model):
    """Immutable hash-chained ledger row. Triggers land in chunk 7."""

    idempotency_key = models.CharField(max_length=64)
    entry_type = models.CharField(max_length=24, choices=StockEntryType.choices)
    lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='entries',
    )
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='stock_entries',
    )
    counterparty_location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_entry_counterparties',
    )
    transfer_group_id = models.CharField(max_length=36, null=True, blank=True)
    quantity = models.DecimalField(max_digits=16, decimal_places=6)
    unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        related_name='stock_entries',
    )
    base_unit_factor = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    quantity_base = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    effective_at = models.DateTimeField()
    recorded_at = models.DateTimeField()
    reverses_entry = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reversed_by',
    )
    # Goods-in sticker (E{id}) this outbound row draws stock from.
    source_entry = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='sticker_draws',
    )
    override_reason = models.CharField(max_length=255, null=True, blank=True)
    authorised_by_user_id = models.IntegerField(null=True, blank=True)
    source_document_type = models.CharField(max_length=24, null=True, blank=True)
    source_document_id = models.BigIntegerField(null=True, blank=True)
    source_document_line = models.IntegerField(null=True, blank=True)
    po_number = models.CharField(max_length=64, null=True, blank=True)
    unit_cost = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    line_cost = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    actor_user_id = models.IntegerField(null=True, blank=True)
    lan_username = models.CharField(max_length=64, null=True, blank=True)
    source_workstation = models.CharField(max_length=64, null=True, blank=True)
    source_workstation_ip = models.CharField(max_length=45, null=True, blank=True)
    device_serial = models.CharField(max_length=32, null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    prev_hash = models.CharField(max_length=64, null=True, blank=True)
    entry_hash = models.CharField(max_length=64)

    class Meta:
        db_table = 'stock_entry'
        constraints = [
            models.UniqueConstraint(
                fields=['idempotency_key'],
                name='uq_stock_entry_idempotency',
            ),
            models.UniqueConstraint(
                fields=['entry_hash'],
                name='uq_stock_entry_hash',
            ),
            models.CheckConstraint(
                check=models.Q(
                    entry_type__in=[
                        StockEntryType.RECEIPT,
                        StockEntryType.ISSUE,
                        StockEntryType.TRANSFER_OUT,
                        StockEntryType.TRANSFER_IN,
                        StockEntryType.PRODUCTION_OUTPUT,
                        StockEntryType.PRODUCTION_CONSUMPTION,
                        StockEntryType.COUNT_ADJUSTMENT,
                        StockEntryType.DISPOSAL,
                        StockEntryType.REVERSAL,
                        StockEntryType.DOWNTIME,
                    ]
                ),
                name='chk_stock_entry_type',
            ),
            models.CheckConstraint(
                check=(
                    ~models.Q(quantity=0)
                    | models.Q(entry_type=StockEntryType.DOWNTIME)
                ),
                name='chk_stock_entry_qty_nonzero',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(base_unit_factor__isnull=True)
                    | models.Q(base_unit_factor__gt=Decimal('0'))
                ),
                name='chk_stock_entry_factor_pos',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(quantity_base__isnull=True, base_unit_factor__isnull=True)
                    | models.Q(
                        quantity_base__isnull=False,
                        base_unit_factor__isnull=False,
                    )
                ),
                name='chk_stock_entry_mass_pair',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        override_reason__isnull=True,
                        authorised_by_user_id__isnull=True,
                    )
                    | models.Q(
                        override_reason__isnull=False,
                        authorised_by_user_id__isnull=False,
                    )
                ),
                name='chk_stock_entry_override',
            ),
        ]
        indexes = [
            models.Index(
                fields=['lot', 'location', 'id'],
                name='idx_stock_entry_lot_loc',
            ),
            models.Index(
                fields=['effective_at'],
                name='idx_stock_entry_effective',
            ),
            models.Index(
                fields=['location', 'effective_at'],
                name='idx_stock_entry_location',
            ),
            models.Index(
                fields=['source_document_type', 'source_document_id'],
                name='idx_stock_entry_document',
            ),
            models.Index(
                fields=['po_number'],
                name='idx_stock_entry_po_number',
            ),
            models.Index(
                fields=['transfer_group_id'],
                name='idx_stock_entry_transfer',
            ),
            models.Index(
                fields=['source_entry'],
                name='idx_stock_entry_source_entry',
            ),
            models.Index(
                fields=['device_serial'],
                name='idx_stock_entry_device',
            ),
        ]

    def __str__(self):
        return f'stock_entry:{self.id}:{self.entry_type}'


class StockGenealogy(models.Model):
    """Append-only production input→output edge. Immutable via triggers in chunk 7."""

    output_entry = models.ForeignKey(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='genealogy_as_output',
    )
    input_entry = models.ForeignKey(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='genealogy_as_input',
    )
    quantity_base = models.DecimalField(max_digits=16, decimal_places=6)

    class Meta:
        db_table = 'stock_genealogy'
        constraints = [
            models.UniqueConstraint(
                fields=['output_entry', 'input_entry'],
                name='uq_stock_genealogy_edge',
            ),
            models.CheckConstraint(
                check=models.Q(quantity_base__gt=Decimal('0')),
                name='chk_stock_genealogy_qty',
            ),
        ]
        indexes = [
            models.Index(
                fields=['input_entry'],
                name='idx_stock_genealogy_input',
            ),
        ]

    def __str__(self):
        return (
            f'stock_genealogy:{self.id}:'
            f'out:{self.output_entry_id}:in:{self.input_entry_id}'
        )


class StockBalance(models.Model):
    """Synchronous on-hand read model. PK identity is (lot, location)."""

    lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='balances',
    )
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='stock_balances',
    )
    quantity = models.DecimalField(max_digits=16, decimal_places=6)
    quantity_base = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    last_entry = models.ForeignKey(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='+',
    )
    last_count_entry = models.ForeignKey(
        StockEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='+',
    )
    negative_authorised_by_entry = models.ForeignKey(
        StockEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='+',
    )
    updated_at = models.DateTimeField()

    class Meta:
        db_table = 'stock_balance'
        constraints = [
            models.UniqueConstraint(
                fields=['lot', 'location'],
                name='uq_stock_balance_lot_location',
            ),
        ]
        indexes = [
            models.Index(
                fields=['location'],
                name='idx_stock_balance_location',
            ),
            models.Index(
                fields=['last_entry'],
                name='idx_stock_balance_watermark',
            ),
        ]

    def __str__(self):
        return f'stock_balance:lot:{self.lot_id}:loc:{self.location_id}'


class StockReservationStatus(models.TextChoices):
    OPEN = 'open', 'Open'
    CONSUMED = 'consumed', 'Consumed'
    RELEASED = 'released', 'Released'
    EXPIRED = 'expired', 'Expired'


class StockReservation(models.Model):
    """Mutable hold against lot/location. Not part of the hash chain."""

    lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='reservations',
    )
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='stock_reservations',
    )
    quantity = models.DecimalField(max_digits=16, decimal_places=6)
    unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        related_name='stock_reservations',
    )
    status = models.CharField(
        max_length=12,
        choices=StockReservationStatus.choices,
        default=StockReservationStatus.OPEN,
    )
    source_document_type = models.CharField(max_length=24, null=True, blank=True)
    source_document_id = models.BigIntegerField(null=True, blank=True)
    source_document_line = models.IntegerField(null=True, blank=True)
    consumed_by_entry = models.ForeignKey(
        StockEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='consumed_reservations',
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stock_reservation'
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    status__in=[
                        StockReservationStatus.OPEN,
                        StockReservationStatus.CONSUMED,
                        StockReservationStatus.RELEASED,
                        StockReservationStatus.EXPIRED,
                    ]
                ),
                name='chk_stock_reservation_status',
            ),
            models.CheckConstraint(
                check=models.Q(quantity__gt=Decimal('0')),
                name='chk_stock_reservation_qty',
            ),
        ]
        indexes = [
            models.Index(
                fields=['lot', 'location', 'status'],
                name='idx_stock_reservation_open',
            ),
            models.Index(
                fields=['source_document_type', 'source_document_id'],
                name='idx_stock_reservation_doc',
            ),
        ]

    def __str__(self):
        return f'stock_reservation:{self.id}:{self.status}'


class StockChainAnchor(models.Model):
    """Local index of head hashes published to S3 Object Lock (chunk 14)."""

    head_entry = models.ForeignKey(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='chain_anchors',
    )
    head_hash = models.CharField(max_length=64)
    entry_count = models.BigIntegerField()
    s3_object_key = models.CharField(max_length=255)
    s3_version_id = models.CharField(max_length=128, null=True, blank=True)
    anchored_at = models.DateTimeField()

    class Meta:
        db_table = 'stock_chain_anchor'
        constraints = [
            models.UniqueConstraint(
                fields=['head_entry'],
                name='uq_stock_chain_anchor_entry',
            ),
        ]
        indexes = [
            models.Index(
                fields=['anchored_at'],
                name='idx_stock_chain_anchor_time',
            ),
        ]

    def __str__(self):
        return f'stock_chain_anchor:{self.id}:entry:{self.head_entry_id}'


class ProductionRun(models.Model):
    """Floor production sidecar (resource / shift / timing / headcount)."""

    stock_entry = models.OneToOneField(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='production_run',
    )
    resource = models.ForeignKey(
        'planning.Resource',
        on_delete=models.PROTECT,
        related_name='production_runs',
    )
    shift_code = models.CharField(max_length=32, null=True, blank=True)
    staff_count = models.PositiveIntegerField(null=True, blank=True)
    base_date = models.DateField()
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'production_run'
        indexes = [
            models.Index(
                fields=['resource', 'base_date'],
                name='idx_prod_run_resource_day',
            ),
            models.Index(
                fields=['base_date'],
                name='idx_prod_run_base_date',
            ),
        ]

    def __str__(self):
        return f'production_run:{self.id}:entry:{self.stock_entry_id}'


class StockUnitStatus(models.TextChoices):
    ACTIVE = 'active', 'Active'
    PARTIALLY_CONSUMED = 'partially_consumed', 'Partially consumed'
    CONSUMED = 'consumed', 'Consumed'
    VOID = 'void', 'Void'
    IN_TRANSIT = 'in_transit', 'In transit'


class StockUnitPrintReason(models.TextChoices):
    INITIAL = 'initial', 'Initial'
    REPRINT = 'reprint', 'Reprint'
    RELABEL = 'relabel', 'Relabel'


class StockUnit(models.Model):
    """One physical labeled unit (bag / pallet / box group)."""

    unit_serial = models.CharField(max_length=32, unique=True)
    lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='units',
    )
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='stock_units',
    )
    unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        related_name='stock_units',
    )
    quantity_initial = models.DecimalField(max_digits=16, decimal_places=6)
    quantity_remaining = models.DecimalField(max_digits=16, decimal_places=6)
    status = models.CharField(
        max_length=20,
        choices=StockUnitStatus.choices,
        default=StockUnitStatus.ACTIVE,
    )
    created_by_entry = models.ForeignKey(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='created_units',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = 'stock_unit'
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(quantity_remaining__gte=Decimal('0'))
                    & models.Q(quantity_remaining__lte=models.F('quantity_initial'))
                ),
                name='chk_stock_unit_qty_bounds',
            ),
            models.CheckConstraint(
                check=models.Q(quantity_initial__gt=Decimal('0')),
                name='chk_stock_unit_qty_initial',
            ),
            models.CheckConstraint(
                check=models.Q(
                    status__in=[
                        StockUnitStatus.ACTIVE,
                        StockUnitStatus.PARTIALLY_CONSUMED,
                        StockUnitStatus.CONSUMED,
                        StockUnitStatus.VOID,
                        StockUnitStatus.IN_TRANSIT,
                    ]
                ),
                name='chk_stock_unit_status',
            ),
        ]
        indexes = [
            models.Index(
                fields=['lot', 'location', 'status'],
                name='idx_stock_unit_lot_loc',
            ),
        ]

    def __str__(self):
        return f'stock_unit:{self.unit_serial}:{self.status}'


class StockUnitConsumption(models.Model):
    """Append-only link: physical unit drawn down by a ledger entry."""

    stock_unit = models.ForeignKey(
        StockUnit,
        on_delete=models.PROTECT,
        related_name='consumptions',
    )
    stock_entry = models.ForeignKey(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='unit_consumptions',
    )
    quantity_base = models.DecimalField(max_digits=16, decimal_places=6)

    class Meta:
        db_table = 'stock_unit_consumption'
        constraints = [
            models.UniqueConstraint(
                fields=['stock_unit', 'stock_entry'],
                name='uq_stock_unit_consumption',
            ),
            models.CheckConstraint(
                check=models.Q(quantity_base__gt=Decimal('0')),
                name='chk_stock_unit_consumption_qty',
            ),
        ]

    def __str__(self):
        return (
            f'stock_unit_consumption:{self.id}:'
            f'unit:{self.stock_unit_id}:entry:{self.stock_entry_id}'
        )


class StockUnitPrintEvent(models.Model):
    """Append-only print / reprint audit."""

    stock_unit = models.ForeignKey(
        StockUnit,
        on_delete=models.PROTECT,
        related_name='print_events',
    )
    printed_at = models.DateTimeField()
    actor_user_id = models.IntegerField(null=True, blank=True)
    lan_username = models.CharField(max_length=64, null=True, blank=True)
    source_workstation = models.CharField(max_length=128, null=True, blank=True)
    reason = models.CharField(
        max_length=16,
        choices=StockUnitPrintReason.choices,
    )

    class Meta:
        db_table = 'stock_unit_print_event'
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    reason__in=[
                        StockUnitPrintReason.INITIAL,
                        StockUnitPrintReason.REPRINT,
                        StockUnitPrintReason.RELABEL,
                    ]
                ),
                name='chk_stock_unit_print_reason',
            ),
        ]
        indexes = [
            models.Index(
                fields=['stock_unit', 'printed_at'],
                name='idx_stock_unit_print_unit',
            ),
        ]

    def __str__(self):
        return f'stock_unit_print_event:{self.id}:{self.reason}'


class StockEntryLabelFormat(models.TextChoices):
    PALLET = 'pallet', 'Pallet'
    BOX = 'box', 'Box'


class StockEntryLabelStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    PRINTED = 'printed', 'Printed'
    VERIFIED = 'verified', 'Verified'


class StockEntryLabel(models.Model):
    """Mutable label state for an immutable stock_entry (Goods IN barcode = E{id})."""

    stock_entry = models.OneToOneField(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='label',
    )
    label_format = models.CharField(
        max_length=16,
        choices=StockEntryLabelFormat.choices,
    )
    label_count = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16,
        choices=StockEntryLabelStatus.choices,
        default=StockEntryLabelStatus.PENDING,
    )
    verified_count = models.PositiveIntegerField(default=0)
    printed_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    actor_user_id = models.IntegerField(null=True, blank=True)
    lan_username = models.CharField(max_length=64, null=True, blank=True)
    source_workstation = models.CharField(max_length=128, null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'stock_entry_label'
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    label_format__in=[
                        StockEntryLabelFormat.PALLET,
                        StockEntryLabelFormat.BOX,
                    ]
                ),
                name='chk_stock_entry_label_format',
            ),
            models.CheckConstraint(
                check=models.Q(
                    status__in=[
                        StockEntryLabelStatus.PENDING,
                        StockEntryLabelStatus.PRINTED,
                        StockEntryLabelStatus.VERIFIED,
                    ]
                ),
                name='chk_stock_entry_label_status',
            ),
            models.CheckConstraint(
                check=models.Q(label_count__gte=1),
                name='chk_stock_entry_label_count',
            ),
        ]

    def __str__(self):
        return (
            f'stock_entry_label:{self.stock_entry_id}:'
            f'{self.label_format}:{self.status}'
        )


class StockEntryLabelScanResult(models.TextChoices):
    OK = 'ok', 'Ok'
    MISMATCH = 'mismatch', 'Mismatch'


class StockEntryLabelScan(models.Model):
    """Append-only scan activity for a Goods IN entry label."""

    label = models.ForeignKey(
        StockEntryLabel,
        on_delete=models.PROTECT,
        related_name='scans',
    )
    stock_entry = models.ForeignKey(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='label_scans',
    )
    scanned_at = models.DateTimeField()
    code = models.CharField(max_length=64)
    result = models.CharField(
        max_length=16,
        choices=StockEntryLabelScanResult.choices,
    )
    actor_user_id = models.IntegerField(null=True, blank=True)
    lan_username = models.CharField(max_length=64, null=True, blank=True)
    source_workstation = models.CharField(max_length=128, null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'stock_entry_label_scan'
        ordering = ['-scanned_at', '-id']
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    result__in=[
                        StockEntryLabelScanResult.OK,
                        StockEntryLabelScanResult.MISMATCH,
                    ]
                ),
                name='chk_stock_entry_label_scan_result',
            ),
        ]
        indexes = [
            models.Index(
                fields=['stock_entry', 'scanned_at'],
                name='idx_entry_label_scan_entry',
            ),
            models.Index(
                fields=['label', 'scanned_at'],
                name='idx_entry_label_scan_label',
            ),
        ]

    def __str__(self):
        return (
            f'stock_entry_label_scan:{self.id}:'
            f'entry:{self.stock_entry_id}:{self.result}'
        )


class StockEntryPostingStatus(models.TextChoices):
    QUEUED = 'queued', 'Queued'
    POSTED = 'posted', 'Posted'
    CANCELLED = 'cancelled', 'Cancelled'


class StockEntryPosting(models.Model):
    """
    Mutable posting gate for an immutable receipt.
    queued = entry exists for barcode, stock_balance not updated yet.
    """

    stock_entry = models.OneToOneField(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='posting',
    )
    status = models.CharField(
        max_length=16,
        choices=StockEntryPostingStatus.choices,
        default=StockEntryPostingStatus.QUEUED,
    )
    queued_at = models.DateTimeField()
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    actor_user_id = models.IntegerField(null=True, blank=True)
    lan_username = models.CharField(max_length=64, null=True, blank=True)
    source_workstation = models.CharField(max_length=128, null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'stock_entry_posting'
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    status__in=[
                        StockEntryPostingStatus.QUEUED,
                        StockEntryPostingStatus.POSTED,
                        StockEntryPostingStatus.CANCELLED,
                    ]
                ),
                name='chk_stock_entry_posting_status',
            ),
        ]
        indexes = [
            models.Index(
                fields=['status', 'queued_at'],
                name='idx_stock_entry_posting_status',
            ),
        ]

    def __str__(self):
        return (
            f'stock_entry_posting:{self.stock_entry_id}:{self.status}'
        )


class StockFifoOverride(models.Model):
    """Who skipped FIFO on a queued goods-out transfer, and why."""

    product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='fifo_overrides',
    )
    scanned_lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='fifo_overrides_scanned',
    )
    recommended_lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name='fifo_overrides_recommended',
    )
    stock_entry = models.OneToOneField(
        StockEntry,
        on_delete=models.PROTECT,
        related_name='fifo_override',
    )
    reason = models.TextField()
    actor_user_id = models.IntegerField(null=True, blank=True)
    lan_username = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'stock_fifo_override'
        indexes = [
            models.Index(fields=['product', '-created_at'], name='idx_fifo_override_product'),
        ]

    def __str__(self):
        return f'stock_fifo_override:{self.id}:product:{self.product_id}'
