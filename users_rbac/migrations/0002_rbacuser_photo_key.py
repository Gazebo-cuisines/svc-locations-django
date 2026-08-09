from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users_rbac', '0001_rbac_schema_chunk1'),
    ]

    operations = [
        migrations.AddField(
            model_name='rbacuser',
            name='photo_key',
            field=models.CharField(blank=True, max_length=512, null=True),
        ),
    ]
