from decimal import Decimal

from django.db import models


class PlanStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    LOCKED = 'locked', 'Locked'
    CLOSED = 'closed', 'Closed'


class PlanLineSource(models.TextChoices):
    MANUAL = 'manual', 'Manual'
    ORDER = 'order', 'Order'
    FORECAST = 'forecast', 'Forecast'


class PlanRunStatus(models.TextChoices):
    RUNNING = 'running', 'Running'
    COMPLETE = 'complete', 'Complete'
    FAILED = 'failed', 'Failed'


class PlanSupplyKind(models.TextChoices):
    PURCHASE_ORDER = 'purchase_order', 'Purchase order'
    PRODUCTION_OUTPUT = 'production_output', 'Production output'
    OPENING = 'opening', 'Opening'
    MANUAL = 'manual', 'Manual'


class ResourceGroup(models.Model):
    """Legacy tblresources.group — no master table; id matches legacy group int."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)

    class Meta:
        db_table = 'resource_group'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class Resource(models.Model):
    """Production resource (line, oven, mixer). PK matches legacy tblresources.id."""

    id = models.IntegerField(primary_key=True)
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='resources',
    )
    group = models.ForeignKey(
        ResourceGroup,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='resources',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'resource'
        ordering = ['code']

    def __str__(self):
        return f'resource:{self.id}:{self.code}'


class Plan(models.Model):
    plan_date = models.DateField()
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='plans',
    )
    status = models.CharField(
        max_length=16,
        choices=PlanStatus.choices,
        default=PlanStatus.DRAFT,
    )
    remarks = models.TextField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'plan'
        constraints = [
            models.UniqueConstraint(
                fields=['plan_date', 'location'],
                name='uq_plan_date_location',
            ),
            models.CheckConstraint(
                check=models.Q(status__in=PlanStatus.values),
                name='chk_plan_status',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'plan_date'], name='idx_plan_status_date'),
            models.Index(
                fields=['location', 'plan_date'],
                name='idx_plan_location_date',
            ),
        ]

    def __str__(self):
        return f'plan:{self.id}:{self.plan_date}:{self.status}'


class PlanLine(models.Model):
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='plan_lines',
    )
    quantity = models.DecimalField(max_digits=16, decimal_places=6)
    unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        related_name='plan_lines',
    )
    source = models.CharField(
        max_length=16,
        choices=PlanLineSource.choices,
        default=PlanLineSource.MANUAL,
    )
    override_consider_stock = models.BooleanField(null=True, blank=True)
    override_full_batches = models.BooleanField(null=True, blank=True)
    override_align_last_batch = models.BooleanField(null=True, blank=True)
    recipe_version = models.ForeignKey(
        'recipe.RecipeVersion',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='plan_lines',
    )
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'plan_line'
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=Decimal('0')),
                name='chk_plan_line_qty',
            ),
            models.CheckConstraint(
                check=models.Q(source__in=PlanLineSource.values),
                name='chk_plan_line_source',
            ),
        ]
        indexes = [
            models.Index(fields=['plan'], name='idx_plan_line_plan'),
            models.Index(fields=['product'], name='idx_plan_line_product'),
        ]

    def __str__(self):
        return f'plan_line:{self.id}:plan:{self.plan_id}'


class PlanRun(models.Model):
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name='runs',
    )
    run_number = models.IntegerField()
    status = models.CharField(
        max_length=16,
        choices=PlanRunStatus.choices,
        default=PlanRunStatus.RUNNING,
    )
    driver_version = models.CharField(max_length=32)
    error_message = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'plan_run'
        constraints = [
            models.UniqueConstraint(
                fields=['plan', 'run_number'],
                name='uq_plan_run_number',
            ),
            models.CheckConstraint(
                check=models.Q(status__in=PlanRunStatus.values),
                name='chk_plan_run_status',
            ),
        ]
        indexes = [
            models.Index(fields=['plan', 'status'], name='idx_plan_run_plan_status'),
        ]

    def __str__(self):
        return f'plan_run:{self.id}:plan:{self.plan_id}:#{self.run_number}'


class PlanRequirement(models.Model):
    run = models.ForeignKey(
        PlanRun,
        on_delete=models.CASCADE,
        related_name='requirements',
    )
    plan_line = models.ForeignKey(
        PlanLine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requirements',
    )
    parent_requirement = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
    )
    level = models.IntegerField()
    batch_number = models.IntegerField(default=1)
    position = models.IntegerField(null=True, blank=True)
    product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='plan_requirements',
    )
    recipe_version = models.ForeignKey(
        'recipe.RecipeVersion',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='plan_requirements',
    )
    net_required = models.DecimalField(max_digits=16, decimal_places=6)
    gross_required = models.DecimalField(max_digits=16, decimal_places=6)
    yield_factor = models.DecimalField(max_digits=16, decimal_places=6)
    process_loss = models.DecimalField(max_digits=16, decimal_places=6)
    min_shelf_life_days = models.IntegerField(default=0)
    source_location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='plan_requirement_sources',
    )
    destination_location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='plan_requirement_destinations',
    )
    default_resource = models.ForeignKey(
        Resource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='default_requirements',
    )
    stock_on_hand = models.DecimalField(
        max_digits=16,
        decimal_places=6,
        default=Decimal('0'),
    )
    balance = models.DecimalField(
        max_digits=16,
        decimal_places=6,
        default=Decimal('0'),
    )
    closed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'plan_requirement'
        constraints = [
            models.CheckConstraint(
                check=models.Q(level__gte=1),
                name='chk_plan_req_level',
            ),
            models.CheckConstraint(
                check=models.Q(batch_number__gte=1),
                name='chk_plan_req_batch',
            ),
            models.CheckConstraint(
                check=models.Q(net_required__gte=0) & models.Q(gross_required__gte=0),
                name='chk_plan_req_qty',
            ),
            models.CheckConstraint(
                check=models.Q(process_loss__gt=0) & models.Q(yield_factor__gt=0),
                name='chk_plan_req_factors',
            ),
        ]
        indexes = [
            models.Index(fields=['run', 'level'], name='idx_plan_req_run_level'),
            models.Index(fields=['run', 'product'], name='idx_plan_req_run_product'),
            models.Index(fields=['run', 'closed'], name='idx_plan_req_run_closed'),
            models.Index(
                fields=['parent_requirement'],
                name='idx_plan_req_parent',
            ),
        ]

    def __str__(self):
        return f'plan_requirement:{self.id}:run:{self.run_id}:lvl:{self.level}'


class PlanAllocation(models.Model):
    requirement = models.ForeignKey(
        PlanRequirement,
        on_delete=models.CASCADE,
        related_name='allocations',
    )
    lot = models.ForeignKey(
        'stock_ledger.StockLot',
        on_delete=models.PROTECT,
        related_name='plan_allocations',
    )
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='plan_allocations',
    )
    quantity = models.DecimalField(max_digits=16, decimal_places=6)
    stock_reservation = models.ForeignKey(
        'stock_ledger.StockReservation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='plan_allocations',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'plan_allocation'
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=Decimal('0')),
                name='chk_plan_alloc_qty',
            ),
            models.UniqueConstraint(
                fields=['requirement', 'lot', 'location'],
                name='uq_plan_alloc_req_lot_loc',
            ),
        ]
        indexes = [
            models.Index(fields=['requirement'], name='idx_plan_alloc_req'),
            models.Index(
                fields=['stock_reservation'],
                name='idx_plan_alloc_reservation',
            ),
        ]

    def __str__(self):
        return f'plan_allocation:{self.id}:req:{self.requirement_id}'


class PlanSupply(models.Model):
    product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='plan_supplies',
    )
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='plan_supplies',
    )
    expected_at = models.DateTimeField()
    quantity = models.DecimalField(max_digits=16, decimal_places=6)
    unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        related_name='plan_supplies',
    )
    kind = models.CharField(max_length=32, choices=PlanSupplyKind.choices)
    source_document_type = models.CharField(max_length=24, null=True, blank=True)
    source_document_id = models.BigIntegerField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'plan_supply'
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=Decimal('0')),
                name='chk_plan_supply_qty',
            ),
            models.CheckConstraint(
                check=models.Q(kind__in=PlanSupplyKind.values),
                name='chk_plan_supply_kind',
            ),
        ]
        indexes = [
            models.Index(
                fields=['product', 'location', 'expected_at'],
                name='idx_plan_supply_atp',
            ),
            models.Index(
                fields=['source_document_type', 'source_document_id'],
                name='idx_plan_supply_doc',
            ),
        ]

    def __str__(self):
        return f'plan_supply:{self.id}:product:{self.product_id}'


class PlanEvent(models.Model):
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name='events',
    )
    event_type = models.CharField(max_length=64)
    payload_json = models.JSONField(null=True, blank=True)
    actor_user_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'plan_event'
        indexes = [
            models.Index(
                fields=['plan', 'created_at'],
                name='idx_plan_event_plan_time',
            ),
        ]

    def __str__(self):
        return f'plan_event:{self.id}:{self.event_type}'


class DemandProfile(models.Model):
    """Weekday demand means — Chunk 10. Table reserved now."""

    product = models.ForeignKey(
        'product.Product',
        on_delete=models.CASCADE,
        related_name='demand_profiles',
    )
    weekday = models.PositiveSmallIntegerField()
    mean_quantity = models.DecimalField(max_digits=16, decimal_places=6)
    sample_count = models.IntegerField()
    computed_at = models.DateTimeField()

    class Meta:
        db_table = 'demand_profile'
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'weekday'],
                name='uq_demand_profile_product_weekday',
            ),
            models.CheckConstraint(
                check=models.Q(weekday__gte=0) & models.Q(weekday__lte=6),
                name='chk_demand_profile_weekday',
            ),
        ]

    def __str__(self):
        return f'demand_profile:{self.id}:product:{self.product_id}:wd:{self.weekday}'


class PlanResourceSlot(models.Model):
    """Resource-day job slot — Chunk 9. Table reserved now."""

    resource = models.ForeignKey(
        Resource,
        on_delete=models.CASCADE,
        related_name='slots',
    )
    slot_date = models.DateField()
    requirement = models.OneToOneField(
        PlanRequirement,
        on_delete=models.CASCADE,
        related_name='resource_slot',
    )
    position = models.IntegerField()
    job_start = models.DateTimeField(null=True, blank=True)
    job_finish = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'plan_resource_slot'
        constraints = [
            models.UniqueConstraint(
                fields=['resource', 'slot_date', 'position'],
                name='uq_plan_resource_slot_pos',
            ),
        ]

    def __str__(self):
        return f'plan_resource_slot:{self.id}:res:{self.resource_id}'
