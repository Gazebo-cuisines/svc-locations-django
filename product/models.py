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
    """Category tree. PK matches legacy tblcategories.id."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=256)
    parent = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        related_name='children',
        null=True,
        blank=True,
    )
    is_default = models.BooleanField(default=False)
    is_container = models.BooleanField(null=True, blank=True)
    purchase_unit = models.ForeignKey(
        'Unit',
        on_delete=models.PROTECT,
        related_name='purchase_unit_categories',
        null=True,
        blank=True,
    )
    multiplier = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    path = models.CharField(max_length=256, null=True, blank=True)
    path_nodes = models.CharField(max_length=128, null=True, blank=True)
    code_generator = models.CharField(max_length=256, null=True, blank=True)
    code_generator_path = models.CharField(max_length=256, null=True, blank=True)
    last_increment_auto_code = models.IntegerField(default=0)
    item_flag = models.SmallIntegerField(default=-1)
    is_range = models.BooleanField(default=False)
    is_resource = models.BooleanField(default=False)
    is_container_flag = models.BooleanField(default=False)
    is_other = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)
    is_locked_assigned = models.BooleanField(default=False)
    is_locked_path = models.BooleanField(default=False)
    remarks = models.TextField(null=True, blank=True)

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


class ProductLabelMode(models.TextChoices):
    PRODUCT = 'product', 'Reusable product label, FIFO picked on scan'
    BATCH = 'batch', 'One label per batch'
    PER_UNIT = 'per_unit', 'One label per physical unit'


class Product(models.Model):
    """Core product identity + classification."""

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=128, unique=True)
    alternate_name = models.CharField(max_length=128, null=True, blank=True)
    recipe_code = models.CharField(max_length=32, unique=True, null=True, blank=True)
    alternate_recipe_code = models.CharField(max_length=32, null=True, blank=True)
    gff_code = models.CharField(max_length=32, null=True, blank=True)
    secondary_gff_recipe = models.CharField(max_length=32, null=True, blank=True)
    external_barcode = models.CharField(max_length=16, null=True, blank=True)
    label_mode = models.CharField(
        max_length=16,
        choices=ProductLabelMode.choices,
        default=ProductLabelMode.PRODUCT,
    )
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


class ProductSupplier(models.Model):
    """Supplier buy-pack for a product. Shape: outer_qty x outer_unit x inner_qty x inner_unit."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='suppliers',
    )
    supplier = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        related_name='supplied_products',
    )
    supplier_code = models.CharField(max_length=64)
    supplier_product_name = models.CharField(max_length=128)
    cost = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    # Shape Format: e.g. 2 Bag x 5 KG = 10 KG
    outer_qty = models.DecimalField(max_digits=16, decimal_places=6)
    outer_unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        related_name='supplier_products_outer',
    )
    inner_qty = models.DecimalField(max_digits=16, decimal_places=6)
    inner_unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        related_name='supplier_products_inner',
    )
    multiplier = models.DecimalField(max_digits=16, decimal_places=6)
    shape_format_label = models.CharField(max_length=128)
    purchase_shape_format = models.ForeignKey(
        PurchaseShapeFormat,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='supplier_products',
    )
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'product_supplier'
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'supplier', 'supplier_code'],
                name='uniq_product_supplier_code',
            ),
            models.CheckConstraint(
                check=models.Q(outer_qty__gt=0),
                name='chk_product_supplier_outer_qty_positive',
            ),
            models.CheckConstraint(
                check=models.Q(inner_qty__gt=0),
                name='chk_product_supplier_inner_qty_positive',
            ),
            models.CheckConstraint(
                check=models.Q(multiplier__gt=0),
                name='chk_product_supplier_multiplier_positive',
            ),
            models.UniqueConstraint(
                fields=['product'],
                condition=models.Q(is_default=True),
                name='uniq_product_supplier_default',
            ),
        ]
        indexes = [
            models.Index(fields=['supplier'], name='idx_product_supplier_supplier'),
        ]

    def __str__(self):
        return f'supplier_product:{self.product_id}:{self.supplier_id}:{self.supplier_code}'

    @staticmethod
    def build_multiplier(outer_qty: Decimal, inner_qty: Decimal) -> Decimal:
        return (outer_qty * inner_qty).quantize(Decimal('0.000001'))

    @staticmethod
    def build_shape_label(
        outer_qty: Decimal,
        outer_unit_name: str,
        inner_qty: Decimal,
        inner_unit_name: str,
        multiplier: Decimal,
    ) -> str:
        def _fmt(value: Decimal) -> str:
            text = format(value.normalize(), 'f')
            if '.' in text:
                text = text.rstrip('0').rstrip('.')
            return text

        outer_u = (outer_unit_name or '').strip().upper()
        inner_u = (inner_unit_name or '').strip().upper()
        return (
            f'{_fmt(outer_qty)}{outer_u} x {_fmt(inner_qty)}{inner_u}'
            f' = {_fmt(multiplier)}{inner_u}'
        )

    def apply_shape_calc(self):
        self.multiplier = self.build_multiplier(self.outer_qty, self.inner_qty)
        outer_name = ''
        inner_name = ''
        if self.outer_unit_id:
            if getattr(self, 'outer_unit', None) is not None:
                outer_name = self.outer_unit.name
            else:
                outer_name = Unit.objects.filter(pk=self.outer_unit_id).values_list(
                    'name', flat=True,
                ).first() or ''
        if self.inner_unit_id:
            if getattr(self, 'inner_unit', None) is not None:
                inner_name = self.inner_unit.name
            else:
                inner_name = Unit.objects.filter(pk=self.inner_unit_id).values_list(
                    'name', flat=True,
                ).first() or ''
        self.shape_format_label = self.build_shape_label(
            self.outer_qty, outer_name, self.inner_qty, inner_name, self.multiplier,
        )

    def save(self, *args, **kwargs):
        self.apply_shape_calc()
        super().save(*args, **kwargs)

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
        Product,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='packaging_as_container_vessel',
    )
    tray = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='packaging_as_tray',
    )
    box = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='packaging_as_box',
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
    is_vegetarian = models.BooleanField(default=False)
    is_vegan = models.BooleanField(default=False)
    country_of_origin = models.CharField(max_length=64, null=True, blank=True)
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


class ProductAuditAction(models.TextChoices):
    CREATE = 'create', 'Create'
    UPDATE = 'update', 'Update'
    DELETE = 'delete', 'Delete'


class ProductAudit(models.Model):
    """Product lifecycle timeline per product."""

    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='audit',
    )
    created_by_user_id = models.IntegerField(null=True, blank=True)
    lan_username = models.CharField(max_length=128, null=True, blank=True)
    actor_email = models.CharField(max_length=128, null=True, blank=True)
    actor_sub = models.CharField(max_length=128, null=True, blank=True)
    source_workstation = models.CharField(max_length=255, null=True, blank=True)
    source_workstation_ip = models.CharField(max_length=45, null=True, blank=True)
    timeline_events = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'product_audit'

    def __str__(self):
        return f'audit:{self.product_id}'


class AllergenCode(models.TextChoices):
    NONE = 'none', 'None'
    CELERY = 'celery', 'Celery'
    CRUSTACEANS = 'crustaceans', 'Crustaceans'
    EGGS = 'eggs', 'Eggs'
    FISH = 'fish', 'Fish'
    GLUTEN = 'gluten', 'Gluten'
    LUPIN = 'lupin', 'Lupin'
    MILK = 'milk', 'Milk'
    MOLLUSCS = 'molluscs', 'Molluscs'
    MUSTARD = 'mustard', 'Mustard'
    NUTS = 'nuts', 'Nuts'
    PEANUTS = 'peanuts', 'Peanuts'
    SESAME = 'sesame', 'Sesame'
    SOYA = 'soya', 'Soya'
    SULPHITES = 'sulphites', 'Sulphites'


class ProductAllergen(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='allergens',
    )
    allergen_code = models.CharField(max_length=16, choices=AllergenCode.choices)
    contains = models.BooleanField(default=False)
    may_contain = models.BooleanField(default=False)

    class Meta:
        db_table = 'product_allergen'
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'allergen_code'],
                name='uniq_product_allergen_code',
            ),
        ]

    def __str__(self):
        return f'allergen:{self.product_id}:{self.allergen_code}'


class ProductNutrition(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='nutrition',
    )
    energy_kj = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    energy_kcal = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    fat_g = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    saturates_g = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    carbohydrate_g = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    sugars_g = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    fibre_g = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    protein_g = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    salt_g = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    class Meta:
        db_table = 'product_nutrition'

    def __str__(self):
        return f'nutrition:{self.product_id}'


class ProductIngredientLabel(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='ingredient_label',
    )
    name = models.CharField(max_length=128, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    per_srp = models.CharField(max_length=64, null=True, blank=True)
    per_box = models.CharField(max_length=64, null=True, blank=True)
    free_text_a = models.TextField(null=True, blank=True)
    free_text_b = models.TextField(null=True, blank=True)
    size_text = models.CharField(max_length=64, null=True, blank=True)
    storage = models.TextField(null=True, blank=True)
    cooking_preparation = models.TextField(null=True, blank=True)
    average_weight = models.CharField(max_length=64, null=True, blank=True)
    per_pallet = models.CharField(max_length=64, null=True, blank=True)
    per_case = models.CharField(max_length=64, null=True, blank=True)
    ingredients_text = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'product_ingredient_label'

    def __str__(self):
        return f'ingredient_label:{self.product_id}'


class ProductAcceptance(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='acceptance',
    )
    min_acceptable_shelf_life_days = models.IntegerField(null=True, blank=True)
    acceptance_note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'product_acceptance'

    def __str__(self):
        return f'acceptance:{self.product_id}'
