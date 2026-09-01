from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users_rbac', '0004_rbacuser_last_seen'),
    ]

    operations = [
        migrations.AlterField(
            model_name='adminaccess',
            name='area',
            field=models.CharField(
                choices=[
                    ('technical', 'Technical'),
                    ('operational', 'Operational'),
                    ('npd', 'NPD'),
                    ('finance', 'Finance'),
                    ('stock_management', 'Stock Management Tool'),
                ],
                max_length=32,
            ),
        ),
    ]
