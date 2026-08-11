from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0005_location_image_key'),
    ]

    operations = [
        migrations.CreateModel(
            name='LocationAuditEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('at', models.DateTimeField(auto_now_add=True)),
                ('action', models.CharField(choices=[('create', 'Create'), ('update', 'Update'), ('delete', 'Delete'), ('image_update', 'Image update')], max_length=32)),
                ('location_id', models.IntegerField(db_index=True)),
                ('location_name', models.CharField(blank=True, max_length=64, null=True)),
                ('actor_sub', models.CharField(blank=True, max_length=128, null=True)),
                ('actor_username', models.CharField(blank=True, max_length=128, null=True)),
                ('actor_display_name', models.CharField(blank=True, max_length=128, null=True)),
                ('actor_email', models.CharField(blank=True, max_length=128, null=True)),
                ('request_method', models.CharField(blank=True, max_length=8, null=True)),
                ('request_path', models.CharField(blank=True, max_length=512, null=True)),
                ('source_ip', models.CharField(blank=True, max_length=64, null=True)),
                ('user_agent', models.TextField(blank=True, null=True)),
                ('before_json', models.JSONField(blank=True, null=True)),
                ('after_json', models.JSONField(blank=True, null=True)),
                ('changed_fields', models.JSONField(blank=True, null=True)),
            ],
            options={
                'db_table': 'loc_audit_event',
                'ordering': ['-at'],
            },
        ),
        migrations.AddIndex(
            model_name='locationauditevent',
            index=models.Index(fields=['location_id', '-at'], name='idx_loc_audit_loc_at'),
        ),
        migrations.AddIndex(
            model_name='locationauditevent',
            index=models.Index(fields=['actor_sub', '-at'], name='idx_loc_audit_actor_at'),
        ),
    ]
