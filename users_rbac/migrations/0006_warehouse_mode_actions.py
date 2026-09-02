from django.db import migrations, models


def backfill_mode_flags(apps, schema_editor):
    """Keep current behaviour: goods_in/out already meant all modes."""
    WarehouseAccess = apps.get_model('users_rbac', 'WarehouseAccess')
    WarehouseAccess.objects.filter(can_goods_in=True).update(
        can_goods_in_without_po=True,
        can_goods_in_stock_adjustment=True,
    )
    WarehouseAccess.objects.filter(can_goods_out=True).update(
        can_goods_out_without_plan=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('users_rbac', '0005_admin_area_stock_management'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehouseaccess',
            name='can_goods_in_without_po',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='warehouseaccess',
            name='can_goods_in_stock_adjustment',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='warehouseaccess',
            name='can_goods_out_without_plan',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(backfill_mode_flags, migrations.RunPython.noop),
    ]
