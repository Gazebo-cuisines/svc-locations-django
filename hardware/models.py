from django.db import models


class HardwareDeviceStatus(models.TextChoices):
    ACTIVE = 'active', 'Active'
    REPAIR = 'repair', 'Repair'
    RETIRED = 'retired', 'Retired'


class HardwareDeviceAction(models.TextChoices):
    ENROLL = 'enroll', 'Enroll'
    LOGIN = 'login', 'Login'
    SCAN = 'scan', 'Scan'
    HEARTBEAT = 'heartbeat', 'Heartbeat'
    ALLOCATE = 'allocate', 'Allocate'


class HardwareDevice(models.Model):
    """Physical scanner gun. Sticker `code` (GUN-03) is what people use."""

    code = models.CharField(max_length=16, unique=True)
    serial = models.CharField(max_length=32, unique=True, null=True, blank=True)
    model = models.CharField(max_length=32, blank=True, default='')
    nickname = models.CharField(max_length=64, blank=True, default='')
    zebra_uuid = models.CharField(max_length=64, null=True, blank=True)
    bt_mac = models.CharField(max_length=17, null=True, blank=True)
    home_location = models.ForeignKey(
        'locations.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hardware_devices',
        db_column='home_location_id',
    )
    assigned_user = models.ForeignKey(
        'users_rbac.RbacUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_devices',
        db_column='assigned_user_id',
    )
    identity_json = models.JSONField(null=True, blank=True)
    last_ip = models.CharField(max_length=45, null=True, blank=True)
    last_screen = models.CharField(max_length=32, blank=True, default='')
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_user = models.ForeignKey(
        'users_rbac.RbacUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='last_used_devices',
        db_column='last_user_id',
    )
    last_location = models.ForeignKey(
        'locations.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='last_seen_devices',
        db_column='last_location_id',
    )
    status = models.CharField(
        max_length=16,
        choices=HardwareDeviceStatus.choices,
        default=HardwareDeviceStatus.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'hw_device'
        ordering = ['code']

    def __str__(self):
        return self.code


class HardwareDeviceEvent(models.Model):
    device = models.ForeignKey(
        HardwareDevice,
        on_delete=models.CASCADE,
        related_name='events',
        db_column='device_id',
    )
    user = models.ForeignKey(
        'users_rbac.RbacUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='device_events',
        db_column='user_id',
    )
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='device_events',
        db_column='location_id',
    )
    action = models.CharField(max_length=16, choices=HardwareDeviceAction.choices)
    at = models.DateTimeField(auto_now_add=True)
    request_path = models.CharField(max_length=512, null=True, blank=True)
    detail_json = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'hw_device_event'
        ordering = ['-at']
        indexes = [
            models.Index(fields=['device', '-at'], name='idx_hw_event_device_at'),
            models.Index(fields=['user', '-at'], name='idx_hw_event_user_at'),
        ]

    def __str__(self):
        return f'{self.action}:{self.device_id}'


class HardwareDevicePost(models.Model):
    """Photo/video post on a gun — the admin device feed."""

    device = models.ForeignKey(
        HardwareDevice,
        on_delete=models.CASCADE,
        related_name='posts',
        db_column='device_id',
    )
    user = models.ForeignKey(
        'users_rbac.RbacUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='device_posts',
        db_column='user_id',
    )
    caption = models.CharField(max_length=512, blank=True, default='')
    media_key = models.CharField(max_length=512)
    content_type = models.CharField(max_length=64, blank=True, default='')
    metadata_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'hw_device_post'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['device', '-created_at'], name='idx_hw_post_device_at'),
            models.Index(fields=['-created_at'], name='idx_hw_post_at'),
        ]

    def __str__(self):
        return f'post:{self.id}:{self.device_id}'


class HardwareDeviceMessage(models.Model):
    """Admin → gun inbox. Delivered on heartbeat; acked when the operator taps OK."""

    device = models.ForeignKey(
        HardwareDevice,
        on_delete=models.CASCADE,
        related_name='messages',
        db_column='device_id',
    )
    created_by = models.ForeignKey(
        'users_rbac.RbacUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='device_messages',
        db_column='created_by_id',
    )
    title = models.CharField(max_length=128)
    body = models.CharField(max_length=512, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    acked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'hw_device_message'
        ordering = ['-created_at']

    def __str__(self):
        return f'msg:{self.id}:{self.device_id}'
