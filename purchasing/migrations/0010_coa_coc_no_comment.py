from django.db import migrations


def forwards(apps, schema_editor):
    GoodsInCheckItem = apps.get_model('purchasing', 'GoodsInCheckItem')
    GoodsInCheckItem.objects.filter(code='coa_coc_received').update(
        allows_comment=False,
    )


def backwards(apps, schema_editor):
    GoodsInCheckItem = apps.get_model('purchasing', 'GoodsInCheckItem')
    GoodsInCheckItem.objects.filter(code='coa_coc_received').update(
        allows_comment=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0009_adhoc_goods_in_receive'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
