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
    packing_rate_per_hour = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text='Cases per hour for packing line (Packing Plan Time — Rate Per Hour).',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'resource'
        ordering = ['code']

    def __str__(self):
        return f'resource:{self.id}:{self.code}'


class ResourceProductRate(models.Model):
    """Production speed table: resource × SKU × headcount → units per minute.

    Replaces the Excel VLOOKUP against CONTENT CODES "Master File" sheet
    (columns Q–U = Number Of Person 5–1, column O = One Mix Qty).
    One row per (resource, product, staff_count) combination.
    """

    id = models.BigAutoField(primary_key=True)
    resource = models.ForeignKey(
        Resource,
        on_delete=models.CASCADE,
        related_name='product_rates',
    )
    product = models.ForeignKey(
        'product.Product',
        on_delete=models.CASCADE,
        related_name='resource_rates',
    )
    staff_count = models.PositiveSmallIntegerField(
        help_text='Number Of People on the station (1–5).',
    )
    units_per_minute = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text='Units produced per minute at this staff count.',
    )
    batch_size = models.DecimalField(
        max_digits=16,
        decimal_places=6,
        null=True,
        blank=True,
        help_text='One Mix Qty — units per mixer batch (CONTENT CODES col O).',
    )
    notes = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'resource_product_rate'
        constraints = [
            models.UniqueConstraint(
                fields=['resource', 'product', 'staff_count'],
                name='uq_resource_product_rate',
            ),
            models.CheckConstraint(
                check=models.Q(staff_count__gte=1) & models.Q(staff_count__lte=5),
                name='chk_resource_product_rate_staff_count',
            ),
            models.CheckConstraint(
                check=models.Q(units_per_minute__gt=0),
                name='chk_resource_product_rate_upm',
            ),
        ]
        indexes = [
            models.Index(fields=['product', 'is_active'], name='idx_rpr_product_active'),
            models.Index(fields=['resource', 'is_active'], name='idx_rpr_resource_active'),
        ]

    def __str__(self):
        return (
            f'resource_product_rate:{self.id}:'
            f'res:{self.resource_id}:prod:{self.product_id}:staff:{self.staff_count}'
        )


class ResourceShift(models.Model):
    """Factory shift definition: start time, break schedule, end time.

    Replaces the hardcoded times in the Excel plan template.
    The engine uses this to:
    - Calculate available production minutes per shift
    - Skip break windows when chaining sequential job start/finish times

    Breaks are stored individually (not as a total) so the scheduler can
    detect when a job crosses a break boundary and defer the next job's
    start to after the break ends.
    """

    id = models.BigAutoField(primary_key=True)
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='shifts',
    )
    weekday = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text='0=Mon … 6=Sun. Null = applies every day of the week.',
    )
    shift_start = models.TimeField(help_text='e.g. 06:30')
    shift_end = models.TimeField(help_text='e.g. 18:00')
    morning_break_start = models.TimeField(
        null=True, blank=True, help_text='Morning Tea start (e.g. 10:00).',
    )
    morning_break_minutes = models.PositiveSmallIntegerField(
        default=15, help_text='Morning Tea duration in minutes.',
    )
    lunch_break_start = models.TimeField(
        null=True, blank=True, help_text='Lunch break start (e.g. 12:30).',
    )
    lunch_break_minutes = models.PositiveSmallIntegerField(
        default=30, help_text='Lunch break duration in minutes.',
    )
    evening_break_start = models.TimeField(
        null=True, blank=True, help_text='Evening Tea start (e.g. 15:00).',
    )
    evening_break_minutes = models.PositiveSmallIntegerField(
        default=15, help_text='Evening Tea duration in minutes.',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'resource_shift'
        constraints = [
            models.UniqueConstraint(
                fields=['location', 'weekday'],
                name='uq_resource_shift_location_weekday',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(weekday__isnull=True)
                    | (models.Q(weekday__gte=0) & models.Q(weekday__lte=6))
                ),
                name='chk_resource_shift_weekday',
            ),
        ]
        indexes = [
            models.Index(
                fields=['location', 'weekday', 'is_active'],
                name='idx_resource_shift_lookup',
            ),
        ]

    @property
    def total_break_minutes(self) -> int:
        return (
            (self.morning_break_minutes if self.morning_break_start else 0)
            + (self.lunch_break_minutes if self.lunch_break_start else 0)
            + (self.evening_break_minutes if self.evening_break_start else 0)
        )

    def __str__(self):
        wd = self.weekday if self.weekday is not None else '*'
        return (
            f'resource_shift:{self.id}:loc:{self.location_id}:'
            f'wd:{wd}:{self.shift_start}–{self.shift_end}'
        )


def default_plan_name(plan_number: int) -> str:
    return f'Production Plan - {plan_number}'


class Plan(models.Model):
    plan_date = models.DateField()
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='plans',
    )
    plan_number = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255, blank=True)
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

    def save(self, *args, **kwargs):
        if self.plan_number is None:
            last = (
                Plan.objects.order_by('-plan_number')
                .values_list('plan_number', flat=True)
                .first()
            )
            self.plan_number = (last or 0) + 1
        if not (self.name or '').strip():
            self.name = default_plan_name(self.plan_number)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'plan:{self.id}:#{self.plan_number}:{self.plan_date}:{self.status}'


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
    stamp_json = models.JSONField(null=True, blank=True)

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
    calc_json = models.JSONField(null=True, blank=True)
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


class ExcelCompareReport(models.Model):
    """TEMP testing dump of excel-compare responses. Drop when signed off."""

    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.SET_NULL,
        null=True,
        related_name='excel_compare_reports',
    )
    plan_date = models.DateField()
    dry_run = models.BooleanField()
    file_name = models.CharField(max_length=255, blank=True)
    plan_id = models.IntegerField(null=True, blank=True)
    run_id = models.IntegerField(null=True, blank=True)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'excel_compare_report_temp'
        ordering = ['-id']

    def __str__(self):
        return f'excel_compare_report_temp:{self.id}'
