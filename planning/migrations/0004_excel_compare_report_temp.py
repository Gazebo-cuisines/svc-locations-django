import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0002_loc_edge_relation_type'),
        ('planning', '0003_resource_location_group'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExcelCompareReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plan_date', models.DateField()),
                ('dry_run', models.BooleanField()),
                ('file_name', models.CharField(blank=True, max_length=255)),
                ('plan_id', models.IntegerField(blank=True, null=True)),
                ('run_id', models.IntegerField(blank=True, null=True)),
                ('payload', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('location', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='excel_compare_reports',
                    to='locations.location',
                )),
            ],
            options={
                'db_table': 'excel_compare_report_temp',
                'ordering': ['-id'],
            },
        ),
    ]
