from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users_rbac', '0003_department_it'),
    ]

    operations = [
        migrations.AddField(
            model_name='rbacuser',
            name='last_ip',
            field=models.CharField(blank=True, max_length=45, null=True),
        ),
        migrations.AddField(
            model_name='rbacuser',
            name='last_seen_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='rbacuser',
            name='last_user_agent',
            field=models.CharField(blank=True, default='', max_length=256),
        ),
        migrations.AddIndex(
            model_name='rbacuser',
            index=models.Index(fields=['-last_seen_at'], name='idx_rbac_user_last_seen'),
        ),
    ]
