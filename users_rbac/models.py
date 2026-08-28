from django.db import models


class Department(models.TextChoices):
    PRODUCTION = 'production', 'Production'
    WAREHOUSE = 'warehouse', 'Warehouse'
    ADMIN = 'admin', 'Admin'
    IT = 'it', 'IT'


class ProductionArea(models.TextChoices):
    LOW_RISK = 'low_risk', 'Low Risk'
    HIGH_RISK = 'high_risk', 'High Risk'
    SLEEVING = 'sleeving', 'Sleeving'
    DISPATCH = 'dispatch', 'Dispatch'


class WarehouseUnit(models.TextChoices):
    UNIT_1 = 'unit_1', 'Unit 1'
    UNIT_2 = 'unit_2', 'Unit 2'
    UNIT_11 = 'unit_11', 'Unit 11'


class AdminArea(models.TextChoices):
    TECHNICAL = 'technical', 'Technical'
    OPERATIONAL = 'operational', 'Operational'
    NPD = 'npd', 'NPD'
    FINANCE = 'finance', 'Finance'


class RbacAuditAction(models.TextChoices):
    USER_CREATED = 'user.created', 'User created'
    USER_UPDATED = 'user.updated', 'User updated'
    USER_ENABLED = 'user.enabled', 'User enabled'
    USER_DISABLED = 'user.disabled', 'User disabled'
    USER_PASSWORD_RESET = 'user.password_reset', 'User password reset'
    GRANTS_REPLACED = 'grants.replaced', 'Grants replaced'
    AUTH_LOGIN_SUCCESS = 'auth.login_success', 'Login success'
    AUTH_LOGIN_FAILURE = 'auth.login_failure', 'Login failure'
    AUTH_ACCESS_DENIED = 'auth.access_denied', 'Access denied'


class RbacUser(models.Model):
    cognito_sub = models.CharField(max_length=64, unique=True)
    username = models.CharField(max_length=128, unique=True)
    email = models.EmailField(max_length=254, unique=True, null=True, blank=True)
    display_name = models.CharField(max_length=128, blank=True, default='')
    photo_key = models.CharField(max_length=512, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by_sub = models.CharField(max_length=64, null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.CharField(max_length=45, null=True, blank=True)
    last_user_agent = models.CharField(max_length=256, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rbac_user'
        ordering = ['username']
        indexes = [
            models.Index(fields=['-last_seen_at'], name='idx_rbac_user_last_seen'),
        ]

    def __str__(self):
        return self.username


class UserDepartment(models.Model):
    user = models.ForeignKey(
        RbacUser,
        on_delete=models.CASCADE,
        related_name='departments',
        db_column='user_id',
    )
    department = models.CharField(max_length=32, choices=Department.choices)

    class Meta:
        db_table = 'rbac_user_department'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'department'],
                name='uq_rbac_user_department',
            ),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.department}'


class ProductionAccess(models.Model):
    user = models.ForeignKey(
        RbacUser,
        on_delete=models.CASCADE,
        related_name='production_access',
        db_column='user_id',
    )
    area = models.CharField(max_length=32, choices=ProductionArea.choices)

    class Meta:
        db_table = 'rbac_production_access'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'area'],
                name='uq_rbac_production_access',
            ),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.area}'


class WarehouseAccess(models.Model):
    user = models.ForeignKey(
        RbacUser,
        on_delete=models.CASCADE,
        related_name='warehouse_access',
        db_column='user_id',
    )
    unit = models.CharField(max_length=32, choices=WarehouseUnit.choices)
    can_goods_in = models.BooleanField(default=False)
    can_goods_out = models.BooleanField(default=False)
    goods_in_previous = models.BooleanField(default=False)
    goods_in_today = models.BooleanField(default=False)
    goods_in_future = models.BooleanField(default=False)

    class Meta:
        db_table = 'rbac_warehouse_access'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'unit'],
                name='uq_rbac_warehouse_access',
            ),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.unit}'


class AdminAccess(models.Model):
    user = models.ForeignKey(
        RbacUser,
        on_delete=models.CASCADE,
        related_name='admin_access',
        db_column='user_id',
    )
    area = models.CharField(max_length=32, choices=AdminArea.choices)

    class Meta:
        db_table = 'rbac_admin_access'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'area'],
                name='uq_rbac_admin_access',
            ),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.area}'


class RbacAuditEvent(models.Model):
    at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=64, choices=RbacAuditAction.choices)
    actor_sub = models.CharField(max_length=64, null=True, blank=True)
    actor_username = models.CharField(max_length=128, null=True, blank=True)
    actor_display_name = models.CharField(max_length=128, null=True, blank=True)
    target_user = models.ForeignKey(
        RbacUser,
        on_delete=models.SET_NULL,
        related_name='audit_events_as_target',
        db_column='target_user_id',
        null=True,
        blank=True,
    )
    target_username = models.CharField(max_length=128, null=True, blank=True)
    target_sub = models.CharField(max_length=64, null=True, blank=True)
    request_method = models.CharField(max_length=8, null=True, blank=True)
    request_path = models.CharField(max_length=512, null=True, blank=True)
    request_id = models.CharField(max_length=64, null=True, blank=True)
    source_ip = models.CharField(max_length=64, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    before_json = models.JSONField(null=True, blank=True)
    after_json = models.JSONField(null=True, blank=True)
    detail_json = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'rbac_audit_event'
        ordering = ['-at']
        indexes = [
            models.Index(fields=['actor_sub', '-at'], name='idx_rbac_audit_actor_at'),
            models.Index(fields=['target_user', '-at'], name='idx_rbac_audit_target_at'),
        ]

    def __str__(self):
        return f'{self.action}:{self.actor_username or "-"}'
