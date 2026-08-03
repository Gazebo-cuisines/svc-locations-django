from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('planning', '0003_resource_location_group'),
        ('stock_ledger', '0006_unit_conversion_chunk8'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductionRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('shift_code', models.CharField(blank=True, max_length=32, null=True)),
                ('staff_count', models.PositiveIntegerField(blank=True, null=True)),
                ('base_date', models.DateField()),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('resource', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='production_runs',
                    to='planning.resource',
                )),
                ('stock_entry', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='production_run',
                    to='stock_ledger.stockentry',
                )),
            ],
            options={
                'db_table': 'production_run',
            },
        ),
        migrations.AddIndex(
            model_name='productionrun',
            index=models.Index(fields=['resource', 'base_date'], name='idx_prod_run_resource_day'),
        ),
        migrations.AddIndex(
            model_name='productionrun',
            index=models.Index(fields=['base_date'], name='idx_prod_run_base_date'),
        ),
    ]
