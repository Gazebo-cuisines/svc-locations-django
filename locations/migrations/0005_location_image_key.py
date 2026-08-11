from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0004_supplier_approval_quarantine'),
    ]

    operations = [
        migrations.AddField(
            model_name='location',
            name='image_key',
            field=models.CharField(blank=True, max_length=512, null=True),
        ),
    ]
