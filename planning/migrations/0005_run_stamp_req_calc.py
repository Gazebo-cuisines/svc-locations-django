from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('planning', '0004_resource_product_rate_and_shift'),
    ]

    operations = [
        migrations.AddField(
            model_name='planrun',
            name='stamp_json',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='planrequirement',
            name='calc_json',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
