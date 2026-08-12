from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0006_location_audit_event'),
    ]

    operations = [
        migrations.AlterField(
            model_name='locationcontact',
            name='phone',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name='locationcontact',
            name='email',
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AlterField(
            model_name='locationaddress',
            name='contact_point_phone',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
