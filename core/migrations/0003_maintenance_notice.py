from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_app_version'),
    ]

    operations = [
        migrations.CreateModel(
            name='MaintenanceNotice',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('is_active', models.BooleanField(default=False)),
                ('message', models.CharField(blank=True, default='', max_length=256)),
                ('resume_at', models.DateTimeField(blank=True, null=True)),
                (
                    'updated_by_username',
                    models.CharField(blank=True, default='', max_length=128),
                ),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'ops_maintenance',
            },
        ),
    ]
