# Extend resource with Location + ResourceGroup; legacy Integer PK (tblresources.id).

import django.db.models.deletion
from django.db import migrations, models


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
                migrations.RunSQL(
                    sql=[
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
                    ],
                    reverse_sql=migrations.RunSQL.noop,
                ),
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
