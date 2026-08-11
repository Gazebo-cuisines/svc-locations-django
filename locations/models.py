from django.db import models


class LocationRole(models.TextChoices):
    INTERNAL = 'internal', 'Internal'
    PROCESS = 'process', 'Process'
    SUPPLIER = 'supplier', 'Supplier'
    CUSTOMER = 'customer', 'Customer'
    COURIER = 'courier', 'Courier'
    DEPOT = 'depot', 'Depot'
    STORAGE = 'storage', 'Storage'
    TRANSFORM = 'transform', 'Transform'


class LocationFeature(models.TextChoices):
    CUSTOMER_ORDERS = 'customer_orders', 'Customer Orders'
    CUSTOMER_FORECASTS = 'customer_forecasts', 'Customer Forecasts'
    PRODUCTION_PLAN_SHORT = 'production_plan_short', 'Production Plan Short'
    PICKING_LIST = 'picking_list', 'Picking List'
    BREAKDOWN_LIST = 'breakdown_list', 'Breakdown List'
    LINEAR_PLAN = 'linear_plan', 'Linear Plan'
    ISSUE_RECIPES = 'issue_recipes', 'Issue Recipes'
    STAFF_BUDGET = 'staff_budget', 'Staff Budget'
    ANOMALY_REPORT_STK_IN = 'anomaly_report_stk_in', 'Anomaly Report Stock In'
    PLAN_CLOSING_REPORT = 'plan_closing_report', 'Plan Closing Report'


class Location(models.Model):
    """Core location identity. PK matches legacy tblcontainers.id."""

    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=64)
    external_code = models.CharField(max_length=16, null=True, blank=True, unique=True)
    visible = models.BooleanField(default=True)
    static = models.BooleanField(default=False)
    locked = models.BooleanField(default=False)
    remarks = models.TextField(null=True, blank=True)
    po_remarks = models.TextField(null=True, blank=True)
    image_key = models.CharField(max_length=512, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'loc_location'
        ordering = ['name']

    def __str__(self):
        return f'{self.id}:{self.name}'


class LocationRoleAssignment(models.Model):
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='roles',
        db_column='location_id',
    )
    role = models.CharField(max_length=32, choices=LocationRole.choices)

    class Meta:
        db_table = 'loc_location_role'
        constraints = [
            models.UniqueConstraint(
                fields=['location', 'role'],
                name='uq_loc_location_role',
            ),
        ]

    def __str__(self):
        return f'{self.location_id}:{self.role}'


class LocationFeatureAssignment(models.Model):
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='features',
        db_column='location_id',
    )
    feature = models.CharField(max_length=64, choices=LocationFeature.choices)

    class Meta:
        db_table = 'loc_location_feature'
        constraints = [
            models.UniqueConstraint(
                fields=['location', 'feature'],
                name='uq_loc_location_feature',
            ),
        ]

    def __str__(self):
        return f'{self.location_id}:{self.feature}'


class LocationStockProfile(models.Model):
    location = models.OneToOneField(
        Location,
        on_delete=models.CASCADE,
        related_name='stock_profile',
        primary_key=True,
        db_column='location_id',
    )
    stock_identifier = models.CharField(max_length=64)
    production_identifier = models.CharField(max_length=64)
    production_form_identifier = models.CharField(max_length=64, null=True, blank=True)
    usage_form_identifier = models.CharField(max_length=64, null=True, blank=True)
    # Legacy realstock: -1/null => True, 0 => False
    real_stock = models.BooleanField(default=True)
    # When False (default), balances at this location hide FG lots from incomplete MADE.
    show_incomplete_stock = models.BooleanField(default=False)
    min_shelf_life = models.IntegerField(null=True, blank=True)
    max_shelf_life = models.IntegerField(null=True, blank=True)
    use_by_modifier = models.IntegerField(null=True, blank=True)
    extends_component_use_by = models.BooleanField(default=False)
    metric_unit = models.IntegerField(null=True, blank=True)
    document_header = models.CharField(max_length=64, null=True, blank=True)
    default_report = models.CharField(max_length=64, null=True, blank=True)
    is_quarantine = models.BooleanField(default=False)

    class Meta:
        db_table = 'loc_stock_profile'

    def __str__(self):
        return f'stock:{self.location_id}'


class SupplierApprovalStatus(models.TextChoices):
    APPROVED = 'approved', 'Approved'
    PENDING = 'pending', 'Pending'
    SUSPENDED = 'suspended', 'Suspended'


class LocationSupplierProfile(models.Model):
    location = models.OneToOneField(
        Location,
        on_delete=models.CASCADE,
        related_name='supplier_profile',
        primary_key=True,
        db_column='location_id',
    )
    approval_status = models.CharField(
        max_length=16,
        choices=SupplierApprovalStatus.choices,
        null=True,
        blank=True,
    )
    approval_expires_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = 'loc_supplier_profile'

    def __str__(self):
        return f'supplier:{self.location_id}:{self.approval_status}'


class LocationRelationType(models.TextChoices):
    ZONE_GROUP = 'zone_group', 'Zone group'
    SUBORDINATE_STORAGE = 'subordinate_storage', 'Subordinate storage'


class LocationEdge(models.Model):
    parent = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='child_edges',
        db_column='parent_id',
    )
    child = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='parent_edges',
        db_column='child_id',
    )
    relation_type = models.CharField(
        max_length=32,
        choices=LocationRelationType.choices,
        default=LocationRelationType.ZONE_GROUP,
    )

    class Meta:
        db_table = 'loc_location_edge'
        constraints = [
            models.UniqueConstraint(
                fields=['relation_type', 'parent', 'child'],
                name='uq_loc_location_edge',
            ),
            models.UniqueConstraint(
                fields=['relation_type', 'child'],
                name='uq_loc_location_edge_child_per_type',
            ),
            models.CheckConstraint(
                check=~models.Q(parent=models.F('child')),
                name='chk_loc_edge_parent_ne_child',
            ),
        ]

    def __str__(self):
        return f'{self.relation_type}:{self.parent_id}->{self.child_id}'


class LocationAddress(models.Model):
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='addresses',
        db_column='location_id',
    )
    name = models.CharField(max_length=64, null=True, blank=True)
    contact_point_name = models.CharField(max_length=64, null=True, blank=True)
    contact_point_phone = models.CharField(max_length=16, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        db_table = 'loc_address'

    def __str__(self):
        return f'addr:{self.location_id}:{self.name or self.id}'


class LocationContact(models.Model):
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='contacts',
        db_column='location_id',
    )
    name = models.CharField(max_length=64, null=True, blank=True)
    phone = models.CharField(max_length=16, null=True, blank=True)
    email = models.CharField(max_length=64, null=True, blank=True)
    contact_type = models.IntegerField(null=True, blank=True)
    contact_value = models.CharField(max_length=64, null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'loc_contact'

    def __str__(self):
        return f'contact:{self.location_id}:{self.name or self.id}'
