import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0013_product_goods_in_masters'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductImage',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image_key', models.CharField(max_length=512)),
                ('is_main', models.BooleanField(default=False)),
                ('sort_order', models.IntegerField(default=0)),
                ('original_filename', models.CharField(blank=True, max_length=255, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='images',
                    to='product.product',
                )),
            ],
            options={
                'db_table': 'product_image',
                'ordering': ['-is_main', 'sort_order', 'id'],
            },
        ),
    ]
