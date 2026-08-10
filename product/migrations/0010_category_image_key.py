from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0009_product_label_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='image_key',
            field=models.CharField(blank=True, max_length=512, null=True),
        ),
    ]
