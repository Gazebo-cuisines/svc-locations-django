from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users_rbac', '0002_rbacuser_photo_key'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userdepartment',
            name='department',
            field=models.CharField(
                choices=[
                    ('production', 'Production'),
                    ('warehouse', 'Warehouse'),
                    ('admin', 'Admin'),
                    ('it', 'IT'),
                ],
                max_length=32,
            ),
        ),
    ]
