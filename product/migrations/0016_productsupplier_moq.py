from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0015_product_code_fields_128'),
    ]

    operations = [
        migrations.AddField(
            model_name='productsupplier',
            name='moq',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='productsupplier',
            constraint=models.CheckConstraint(
                check=models.Q(('moq__isnull', True)) | models.Q(('moq__gt', 0)),
                name='chk_product_supplier_moq_positive',
            ),
        ),
    ]
