from django.db import models


class ErrorTicketStatus(models.TextChoices):
    OPEN = 'open', 'Open'
    INVESTIGATING = 'investigating', 'Investigating'
    RESOLVED = 'resolved', 'Resolved'


class ErrorTicketSource(models.TextChoices):
    CLIENT = 'client', 'Client'
    SERVER = 'server', 'Server'


class ErrorTicket(models.Model):
    fingerprint = models.CharField(max_length=32, unique=True)
    status = models.CharField(
        max_length=16,
        choices=ErrorTicketStatus.choices,
        default=ErrorTicketStatus.OPEN,
        db_index=True,
    )
    note = models.TextField(blank=True, default='')
    message = models.CharField(max_length=1024)
    stack = models.TextField(blank=True, default='')
    url = models.CharField(max_length=512, blank=True, default='')
    source = models.CharField(
        max_length=16,
        choices=ErrorTicketSource.choices,
        default=ErrorTicketSource.CLIENT,
    )
    occurrences = models.PositiveIntegerField(default=1)
    actor_sub = models.CharField(max_length=128, blank=True, default='')
    actor_username = models.CharField(max_length=128, blank=True, default='')
    payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField()

    class Meta:
        db_table = 'ops_error_ticket'
        ordering = ['-last_seen_at']
        indexes = [
            models.Index(fields=['status', '-last_seen_at'], name='idx_ops_err_status_seen'),
        ]

    def __str__(self):
        return f'{self.status}:{self.fingerprint}'


class AppVersion(models.Model):
    platform = models.CharField(max_length=16, unique=True)
    min_version = models.CharField(max_length=32)
    latest_version = models.CharField(max_length=32, blank=True, default='')
    message = models.CharField(max_length=256, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'app_version'

    def __str__(self):
        return f'{self.platform}:{self.min_version}'


class MaintenanceNotice(models.Model):
    is_active = models.BooleanField(default=False)
    message = models.CharField(max_length=256, blank=True, default='')
    resume_at = models.DateTimeField(null=True, blank=True)
    updated_by_username = models.CharField(max_length=128, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ops_maintenance'

    def __str__(self):
        return f'active={self.is_active}'
