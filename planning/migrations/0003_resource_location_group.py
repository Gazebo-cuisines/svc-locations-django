# Extend resource with Location + ResourceGroup; legacy Integer PK (tblresources.id).

import django.db.models.deletion
from django.db import migrations, models

MYSQL_ALTER = [
    'ALTER TABLE plan_requirement '
    'DROP FOREIGN KEY plan_requirement_default_resource_id_3a88eae6_fk_resource_id',
    'ALTER TABLE plan_resource_slot '
    'DROP FOREIGN KEY plan_resource_slot_resource_id_b83e2a87_fk_resource_id',
    'ALTER TABLE resource MODIFY id INT NOT NULL',
    'ALTER TABLE resource '
    'ADD COLUMN location_id INT NOT NULL, '
    'ADD COLUMN group_id INT NULL',
    'ALTER TABLE plan_requirement '
    'MODIFY default_resource_id INT NULL',
    'ALTER TABLE plan_resource_slot '
    'MODIFY resource_id INT NOT NULL',
    'ALTER TABLE resource '
    'ADD CONSTRAINT resource_location_id_fk '
    'FOREIGN KEY (location_id) REFERENCES loc_location (id)',
    'ALTER TABLE resource '
    'ADD CONSTRAINT resource_group_id_fk '
    'FOREIGN KEY (group_id) REFERENCES resource_group (id)',
    'ALTER TABLE plan_requirement '
    'ADD CONSTRAINT plan_requirement_default_resource_id_fk '
    'FOREIGN KEY (default_resource_id) REFERENCES resource (id) '
    'ON DELETE SET NULL',
    'ALTER TABLE plan_resource_slot '
    'ADD CONSTRAINT plan_resource_slot_resource_id_fk '
    'FOREIGN KEY (resource_id) REFERENCES resource (id) '
    'ON DELETE CASCADE',
]


def _pg_drop_fks(cursor, table, column):
    cursor.execute(
        """
        SELECT tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_schema = 'public'
          AND tc.table_name = %s
          AND kcu.column_name = %s
          AND tc.constraint_type = 'FOREIGN KEY'
        """,
        [table, column],
    )
    for (name,) in cursor.fetchall():
        cursor.execute(f'ALTER TABLE {table} DROP CONSTRAINT {name}')


def alter_resource_db(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'mysql':
            for sql in MYSQL_ALTER:
                cursor.execute(sql)
            return
        if vendor != 'postgresql':
            return
        _pg_drop_fks(cursor, 'plan_requirement', 'default_resource_id')
        _pg_drop_fks(cursor, 'plan_resource_slot', 'resource_id')
        cursor.execute('ALTER TABLE resource ALTER COLUMN id DROP IDENTITY IF EXISTS')
        cursor.execute('ALTER TABLE resource ALTER COLUMN id TYPE integer USING id::integer')
        cursor.execute('ALTER TABLE resource ADD COLUMN IF NOT EXISTS location_id integer NOT NULL')
        cursor.execute('ALTER TABLE resource ADD COLUMN IF NOT EXISTS group_id integer NULL')
        cursor.execute(
            'ALTER TABLE plan_requirement ALTER COLUMN default_resource_id '
            'TYPE integer USING default_resource_id::integer'
        )
        cursor.execute(
            'ALTER TABLE plan_resource_slot ALTER COLUMN resource_id '
            'TYPE integer USING resource_id::integer'
        )
        cursor.execute(
            'ALTER TABLE resource ADD CONSTRAINT resource_location_id_fk '
            'FOREIGN KEY (location_id) REFERENCES loc_location (id)'
        )
        cursor.execute(
            'ALTER TABLE resource ADD CONSTRAINT resource_group_id_fk '
            'FOREIGN KEY (group_id) REFERENCES resource_group (id)'
        )
        cursor.execute(
            'ALTER TABLE plan_requirement ADD CONSTRAINT plan_requirement_default_resource_id_fk '
            'FOREIGN KEY (default_resource_id) REFERENCES resource (id) ON DELETE SET NULL'
        )
        cursor.execute(
            'ALTER TABLE plan_resource_slot ADD CONSTRAINT plan_resource_slot_resource_id_fk '
            'FOREIGN KEY (resource_id) REFERENCES resource (id) ON DELETE CASCADE'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0002_loc_edge_relation_type'),
        ('planning', '0002_plan_published_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='ResourceGroup',
            fields=[
                ('id', models.IntegerField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=64)),
            ],
            options={
                'db_table': 'resource_group',
                'ordering': ['name'],
            },
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(alter_resource_db, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name='resource',
                    name='id',
                    field=models.IntegerField(primary_key=True, serialize=False),
                ),
                migrations.AddField(
                    model_name='resource',
                    name='location',
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='resources',
                        to='locations.location',
                    ),
                ),
                migrations.AddField(
                    model_name='resource',
                    name='group',
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='resources',
                        to='planning.resourcegroup',
                    ),
                ),
                migrations.AlterModelOptions(
                    name='resource',
                    options={'ordering': ['code']},
                ),
            ],
        ),
    ]
