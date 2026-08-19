from django.db import migrations, models


KNOWN_IPS = {
    '26202524703110': '172.16.0.98',
    '26203524700055': '172.16.0.30',
    '26202524703099': '172.16.0.224',
}


def seed_ips(apps, schema_editor):
    HardwareDevice = apps.get_model('hardware', 'HardwareDevice')
    for serial, ip in KNOWN_IPS.items():
        HardwareDevice.objects.filter(serial=serial, last_ip__isnull=True).update(last_ip=ip)


class Migration(migrations.Migration):

    dependencies = [
        ('hardware', '0002_device_post'),
    ]

    operations = [
        migrations.AddField(
            model_name='hardwaredevice',
            name='last_ip',
            field=models.CharField(blank=True, max_length=45, null=True),
        ),
        migrations.RunPython(seed_ips, migrations.RunPython.noop),
    ]
