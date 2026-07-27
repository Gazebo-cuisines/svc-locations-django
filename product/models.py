from decimal import Decimal

from django.db import models


class ProductClass(models.Model):
    """Lookup stub. PK matches legacy Class.id."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)

    class Meta:
        db_table = 'product_class'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class Category(models.Model):
    """Lookup stub. PK matches legacy Categories.id."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)

    class Meta:
        db_table = 'product_category'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class Range(models.Model):
    """Lookup stub. PK matches legacy Range.id. Table named product_range (SQL reserved word)."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)

    class Meta:
        db_table = 'product_range'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class SubRange(models.Model):
    """Lookup stub for legacy subrange."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)
    range = models.ForeignKey(
        Range,
        on_delete=models.PROTECT,
        related_name='product_sub_ranges',
        null=True,
        blank=True,
    )

    class Meta:
        db_table = 'product_sub_range'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class Unit(models.Model):
    """Lookup stub. PK matches legacy Units.id."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)

    class Meta:
        db_table = 'product_unit'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class PurchaseShapeFormat(models.Model):
    """Lookup stub for legacy purchaseshapeformat."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)

    class Meta:
        db_table = 'product_purchase_shape_format'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class Product(models.Model):
    """Core product identity + classification. PK matches legacy tblproducts.id."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=128, unique=True)
    alternate_name = models.CharField(max_length=128, null=True, blank=True)
    recipe_code = models.CharField(max_length=32, unique=True, null=True, blank=True)
    alternate_recipe_code = models.CharField(max_length=32, null=True, blank=True)
    gff_code = models.CharField(max_length=32, null=True, blank=True)
    secondary_gff_recipe = models.CharField(max_length=32, null=True, blank=True)
    external_barcode = models.CharField(max_length=16, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_downtime = models.BooleanField(default=False)
    purchasing_version = models.IntegerField(null=True, blank=True)
    ingredient_count = models.IntegerField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    product_class = models.ForeignKey(
        ProductClass,
        on_delete=models.PROTECT,
        related_name='products',
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='products',
    )
    range = models.ForeignKey(
        Range,
        on_delete=models.PROTECT,
        related_name='products',
    )
    sub_range = models.ForeignKey(
        SubRange,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='products',
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        related_name='products',
    )
    purchasing_unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='purchased_products',
    )
    purchase_shape_format = models.ForeignKey(
        PurchaseShapeFormat,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='products',
    )
    source_container = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='source_products',
    )
    destination_container = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='destination_products',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'product'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class PackagingType(models.Model):
    """Lookup stub for legacy packagingType."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)

    class Meta:
        db_table = 'product_packaging_type'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class PhysicalState(models.Model):
    """Lookup stub for legacy physicalState."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)

    class Meta:
        db_table = 'product_physical_state'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class DeliveryState(models.Model):
    """Lookup stub for legacy deliveryState."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)

    class Meta:
        db_table = 'product_delivery_state'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class ProductCosting(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='costing',
    )
    unit_cost = models.DecimalField(
        max_digits=16, decimal_places=6, default=Decimal('0'),
    )
    unit_price = models.DecimalField(
        max_digits=16, decimal_places=6, default=Decimal('0'),
    )
    nominal_code = models.CharField(max_length=4, null=True, blank=True)
    case_size_description = models.CharField(max_length=64, null=True, blank=True)
    lead_time_days = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'product_costing'

    def __str__(self):
        return f'costing:{self.product_id}'


class ProductStockPolicy(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='stock_policy',
    )
    reorder_level = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    min_stock = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    max_stock = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    clear_stock_level = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )

    class Meta:
        db_table = 'product_stock_policy'

    def __str__(self):
        return f'stock_policy:{self.product_id}'


class ProductShelfLife(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='shelf_life',
    )
    shelf_life_days = models.IntegerField(null=True, blank=True)
    shelf_life_intrinsic_days = models.IntegerField(null=True, blank=True)
    shelf_life_depot_days = models.IntegerField(null=True, blank=True)
    absolute_min_shelf_life_days = models.IntegerField(null=True, blank=True)
    force_production_date = models.BooleanField(default=False)
    force_trace_number = models.BooleanField(default=False)
    force_use_by = models.BooleanField(default=False)

    class Meta:
        db_table = 'product_shelf_life'

    def __str__(self):
        return f'shelf_life:{self.product_id}'


class ProductPackaging(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='packaging',
    )
    pack_weight = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    unitary_weight = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    gross_unitary_weight = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    align_unitary_weight = models.BooleanField(default=False)
    default_length = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    items_per_unit = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    units_per_tray = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    units_per_batch = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    is_gas_flush = models.BooleanField(default=False)
    container_vessel = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='vessel_packaged_products',
    )
    tray = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='tray_packaged_products',
    )
    box = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='box_packaged_products',
    )
    packaging_type = models.ForeignKey(
        PackagingType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='products',
    )
    physical_state = models.ForeignKey(
        PhysicalState,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='products',
    )
    delivery_state = models.ForeignKey(
        DeliveryState,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='products',
    )

    class Meta:
        db_table = 'product_packaging'

    def __str__(self):
        return f'packaging:{self.product_id}'


class ProductProduction(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='production',
    )
    avg_run_size = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    avg_minutes = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    avg_rate_product = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    avg_staff_min_per_unit = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    avg_staff_per_minute = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    avg_rate_range = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    average_rate = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    unitary_gap_time = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    unitary_dwell_time = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    relative_plan_position = models.IntegerField(null=True, blank=True)
    # Legacy genDefaultResource — Resource model not yet defined
    default_resource_id = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'product_production'

    def __str__(self):
        return f'production:{self.product_id}'


class ProductYield(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='yield_data',
    )
    yield_factor = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal('1.0000'),
    )
    yield_factor_auto = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal('1.0000'),
    )
    chilling_loss_factor = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
    )

    class Meta:
        db_table = 'product_yield'

    def __str__(self):
        return f'yield:{self.product_id}'


class ProductFlags(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='flags',
    )
    in_stock_list = models.BooleanField(default=False)
    auto_yield = models.BooleanField(default=False)
    has_plan = models.BooleanField(default=False)
    auto_rate = models.BooleanField(default=False)
    auto_clear_stock = models.BooleanField(default=False)
    consider_stock_in_plan = models.BooleanField(default=False)
    has_recipe = models.BooleanField(default=False)
    full_batches_only = models.BooleanField(default=False)
    auto_trends = models.BooleanField(default=False)
    is_implicit = models.BooleanField(default=False)
    include_in_projections = models.BooleanField(default=False)
    record_flag = models.BooleanField(default=False)
    has_chilling_loss = models.BooleanField(default=False)
    use_batch_quantity = models.BooleanField(default=False)
    freezer_no_deduct = models.BooleanField(default=False)
    is_purchase_item = models.BooleanField(default=False)
    is_sales_item = models.BooleanField(default=False)
    is_dispatch_support = models.BooleanField(default=False)

    class Meta:
        db_table = 'product_flags'

    def __str__(self):
        return f'flags:{self.product_id}'


class ProductTechnical(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='technical',
    )
    is_gmo_free = models.BooleanField(default=False)
    spec_sign_off_date = models.DateField(null=True, blank=True)
    next_review_date = models.DateField(null=True, blank=True)
    requires_temperature_check = models.BooleanField(default=False)
    temp_check_lower_bound = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
    )
    temp_check_upper_bound = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
    )

    class Meta:
        db_table = 'product_technical'

    def __str__(self):
        return f'technical:{self.product_id}'


class ProductAudit(models.Model):
    """Transitional — replace workstation/IP with device_id later (Manual 8)."""

    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='audit',
    )
    created_by_user_id = models.IntegerField(null=True, blank=True)
    lan_username = models.CharField(max_length=64, null=True, blank=True)
    source_workstation = models.CharField(max_length=64, null=True, blank=True)
    source_workstation_ip = models.CharField(max_length=45, null=True, blank=True)

    class Meta:
        db_table = 'product_audit'

    def __str__(self):
        return f'audit:{self.product_id}'
