from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('hardware', '0001_initial'),
        ('users_rbac', '0001_rbac_schema_chunk1'),
    ]

    operations = [
        migrations.CreateModel(
            name='HardwareDevicePost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('caption', models.CharField(blank=True, default='', max_length=512)),
                ('media_key', models.CharField(max_length=512)),
                ('content_type', models.CharField(blank=True, default='', max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('device', models.ForeignKey(db_column='device_id', on_delete=django.db.models.deletion.CASCADE, related_name='posts', to='hardware.hardwaredevice')),
                ('user', models.ForeignKey(blank=True, db_column='user_id', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='device_posts', to='users_rbac.rbacuser')),
            ],
            options={
                'db_table': 'hw_device_post',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='hardwaredevicepost',
            index=models.Index(fields=['device', '-created_at'], name='idx_hw_post_device_at'),
        ),
        migrations.AddIndex(
            model_name='hardwaredevicepost',
            index=models.Index(fields=['-created_at'], name='idx_hw_post_at'),
        ),
    ]
