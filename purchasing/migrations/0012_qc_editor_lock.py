from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0011_goods_in_answer'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorderdelivery',
            name='editor_user_id',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='purchaseorderdelivery',
            name='editor_heartbeat_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='purchaseorderdeliveryline',
            name='editor_user_id',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='purchaseorderdeliveryline',
            name='editor_heartbeat_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='adhocgoodsinsession',
            name='editor_user_id',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='adhocgoodsinsession',
            name='editor_heartbeat_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='adhocgoodsinline',
            name='editor_user_id',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='adhocgoodsinline',
            name='editor_heartbeat_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
