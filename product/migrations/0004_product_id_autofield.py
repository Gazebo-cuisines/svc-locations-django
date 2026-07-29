from django.db import migrations, models


def set_auto_increment(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SELECT IFNULL(MAX(id), 0) + 1 FROM product')
        next_id = cursor.fetchone()[0]
        cursor.execute(f'ALTER TABLE product AUTO_INCREMENT = {int(next_id)}')


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0003_category_parent'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='id',
            field=models.AutoField(primary_key=True, serialize=False),
        ),
        migrations.RunPython(set_auto_increment, migrations.RunPython.noop),
    ]
