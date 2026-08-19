from django.db import migrations, models
import django.db.models.deletion


GUN01 = {
    'code': 'GUN-01',
    'serial': '26202524703110',
    'model': 'TC22F',
    'nickname': 'BSD01_Gazeboo_cloud',
    'zebra_uuid': 'f375db664e6b0501F9F8DCBd39d840a',
    'bt_mac': '88:BC:AC:D5:21:BF',
    'status': 'active',
    'identity_json': {
        'product': 'TC22F',
        'android': '14',
        'sdk': 34,
        'build': '14-20-14.00-UG-U00-STD-ATH-04',
        'soc': 'QCM5430',
        'imager': 'SE4710',
        'android_id': 'd008cd109f0700c6',
        'usb_vid_pid': '05E0:2106',
    },
}


def seed_gun01(apps, schema_editor):
    HardwareDevice = apps.get_model('hardware', 'HardwareDevice')
    HardwareDevice.objects.get_or_create(serial=GUN01['serial'], defaults=GUN01)


def unseed_gun01(apps, schema_editor):
    HardwareDevice = apps.get_model('hardware', 'HardwareDevice')
    HardwareDevice.objects.filter(serial=GUN01['serial']).delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('locations', '0001_loc_schema_chunk1'),
        ('users_rbac', '0001_rbac_schema_chunk1'),
    ]

    operations = [
        migrations.CreateModel(
            name='HardwareDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=16, unique=True)),
                ('serial', models.CharField(blank=True, max_length=32, null=True, unique=True)),
                ('model', models.CharField(blank=True, default='', max_length=32)),
                ('nickname', models.CharField(blank=True, default='', max_length=64)),
                ('zebra_uuid', models.CharField(blank=True, max_length=64, null=True)),
                ('bt_mac', models.CharField(blank=True, max_length=17, null=True)),
                ('identity_json', models.JSONField(blank=True, null=True)),
                ('last_seen_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('repair', 'Repair'), ('retired', 'Retired')], default='active', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assigned_user', models.ForeignKey(blank=True, db_column='assigned_user_id', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_devices', to='users_rbac.rbacuser')),
                ('home_location', models.ForeignKey(blank=True, db_column='home_location_id', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hardware_devices', to='locations.location')),
                ('last_location', models.ForeignKey(blank=True, db_column='last_location_id', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='last_seen_devices', to='locations.location')),
                ('last_user', models.ForeignKey(blank=True, db_column='last_user_id', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='last_used_devices', to='users_rbac.rbacuser')),
            ],
            options={
                'db_table': 'hw_device',
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='HardwareDeviceEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('enroll', 'Enroll'), ('login', 'Login'), ('scan', 'Scan'), ('heartbeat', 'Heartbeat'), ('allocate', 'Allocate')], max_length=16)),
                ('at', models.DateTimeField(auto_now_add=True)),
                ('request_path', models.CharField(blank=True, max_length=512, null=True)),
                ('detail_json', models.JSONField(blank=True, null=True)),
                ('device', models.ForeignKey(db_column='device_id', on_delete=django.db.models.deletion.CASCADE, related_name='events', to='hardware.hardwaredevice')),
                ('location', models.ForeignKey(blank=True, db_column='location_id', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='device_events', to='locations.location')),
                ('user', models.ForeignKey(blank=True, db_column='user_id', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='device_events', to='users_rbac.rbacuser')),
            ],
            options={
                'db_table': 'hw_device_event',
                'ordering': ['-at'],
            },
        ),
        migrations.AddIndex(
            model_name='hardwaredeviceevent',
            index=models.Index(fields=['device', '-at'], name='idx_hw_event_device_at'),
        ),
        migrations.AddIndex(
            model_name='hardwaredeviceevent',
            index=models.Index(fields=['user', '-at'], name='idx_hw_event_user_at'),
        ),
        migrations.RunPython(seed_gun01, unseed_gun01),
    ]
