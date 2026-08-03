from decimal import Decimal

from django.db import models


class StationCode(models.TextChoices):
    HIGH_RISK = 'high_risk', 'High Risk'
    SLEEVING = 'sleeving', 'Sleeving'
    INTERNAL_PROCESS = 'internal_process', 'Internal Process'
    WAREHOUSE = 'warehouse', 'Warehouse'


class ProductionRunStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    POSTED = 'posted', 'Posted'
    VOID = 'void', 'Void'


class ProductionStation(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='production_stations',
    )
    default_output_location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='production_station_outputs',
    )
    default_consume_location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='production_station_consumes',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'prod_reg_station'

    def __str__(self):
        return f'prod_reg_station:{self.id}:{self.code}'


class ProductionRun(models.Model):
    station = models.ForeignKey(
        ProductionStation,
        on_delete=models.PROTECT,
        related_name='runs',
    )
    status = models.CharField(
        max_length=16,
        choices=ProductionRunStatus.choices,
        default=ProductionRunStatus.DRAFT,
    )
    product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='production_runs',
    )
    quantity_made = models.DecimalField(max_digits=16, decimal_places=6)
    unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        related_name='production_runs',
    )
    recipe_version = models.ForeignKey(
        'recipe.RecipeVersion',
        on_delete=models.PROTECT,
        related_name='production_runs',
    )
    plan_line = models.ForeignKey(
        'planning.PlanLine',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='production_runs',
    )
    from_location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='production_runs_from',
    )
    to_location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='production_runs_to',
    )
    resource_id = models.IntegerField(null=True, blank=True)
    shift = models.CharField(max_length=32, null=True, blank=True)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    production_date = models.DateField()
    use_by = models.DateField(null=True, blank=True)
    trace_number = models.CharField(max_length=64, null=True, blank=True)
    staff_count = models.IntegerField(null=True, blank=True)
    trays = models.IntegerField(null=True, blank=True)
    batches = models.IntegerField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)
    output_stock_entry = models.ForeignKey(
        'stock_ledger.StockEntry',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='production_run_outputs',
    )
    actor_user_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'prod_reg_run'
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=ProductionRunStatus.values),
                name='chk_prod_reg_run_status',
            ),
            models.CheckConstraint(
                check=models.Q(quantity_made__gt=Decimal('0')),
                name='chk_prod_reg_run_qty',
            ),
            models.UniqueConstraint(
                fields=['idempotency_key'],
                name='uq_prod_reg_run_idempotency',
                condition=models.Q(idempotency_key__isnull=False),
            ),
        ]
        indexes = [
            models.Index(
                fields=['station', 'status', 'production_date'],
                name='idx_prod_reg_run_station',
            ),
            models.Index(
                fields=['production_date', 'status'],
                name='idx_prod_reg_run_date',
            ),
        ]

    def __str__(self):
        return f'prod_reg_run:{self.id}:{self.status}'


class ProductionRunConsumption(models.Model):
    run = models.ForeignKey(
        ProductionRun,
        on_delete=models.CASCADE,
        related_name='consumptions',
    )
    component_product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='production_run_consumptions',
    )
    lot = models.ForeignKey(
        'stock_ledger.StockLot',
        on_delete=models.PROTECT,
        related_name='production_run_consumptions',
    )
    quantity = models.DecimalField(max_digits=16, decimal_places=6)
    unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        related_name='production_run_consumptions',
    )
    needed_qty = models.DecimalField(max_digits=16, decimal_places=6)
    stock_entry = models.ForeignKey(
        'stock_ledger.StockEntry',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='production_run_consumptions',
    )

    class Meta:
        db_table = 'prod_reg_run_consumption'
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=Decimal('0')),
                name='chk_prod_reg_consumption_qty',
            ),
            models.CheckConstraint(
                check=models.Q(needed_qty__gte=Decimal('0')),
                name='chk_prod_reg_consumption_needed',
            ),
        ]
        indexes = [
            models.Index(fields=['run'], name='idx_prod_reg_consumption_run'),
        ]

    def __str__(self):
        return f'prod_reg_run_consumption:{self.id}:run:{self.run_id}'


class ProductionDowntime(models.Model):
    station = models.ForeignKey(
        ProductionStation,
        on_delete=models.CASCADE,
        related_name='downtimes',
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    resource_id = models.IntegerField(null=True, blank=True)
    shift = models.CharField(max_length=32, null=True, blank=True)
    remarks = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'prod_reg_downtime'
        indexes = [
            models.Index(
                fields=['station', 'start_at'],
                name='idx_prod_reg_downtime_station',
            ),
        ]

    def __str__(self):
        return f'prod_reg_downtime:{self.id}:station:{self.station_id}'
