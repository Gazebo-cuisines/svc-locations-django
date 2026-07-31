# Chunk 5 — Planning schema (MVP + deferred empty tables). Hand-written; no SPs/triggers.

from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('locations', '0002_loc_edge_relation_type'),
        ('product', '0006_productsupplier_shape_format'),
        ('recipe', '0001_recipe_schema_chunk3'),
        ('stock_ledger', '0006_unit_conversion_chunk8'),
    ]

    operations = [
        migrations.CreateModel(
            name='Resource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=64, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'resource',
            },
        ),
        migrations.CreateModel(
            name='Plan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plan_date', models.DateField()),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('locked', 'Locked'), ('closed', 'Closed')], default='draft', max_length=16)),
                ('remarks', models.TextField(blank=True, null=True)),
                ('created_by_user_id', models.BigIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('location', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plans', to='locations.location')),
            ],
            options={
                'db_table': 'plan',
            },
        ),
        migrations.CreateModel(
            name='PlanLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.DecimalField(decimal_places=6, max_digits=16)),
                ('source', models.CharField(choices=[('manual', 'Manual'), ('order', 'Order'), ('forecast', 'Forecast')], default='manual', max_length=16)),
                ('override_consider_stock', models.BooleanField(blank=True, null=True)),
                ('override_full_batches', models.BooleanField(blank=True, null=True)),
                ('override_align_last_batch', models.BooleanField(blank=True, null=True)),
                ('sort_order', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='planning.plan')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plan_lines', to='product.product')),
                ('recipe_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='plan_lines', to='recipe.recipeversion')),
                ('unit', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plan_lines', to='product.unit')),
            ],
            options={
                'db_table': 'plan_line',
            },
        ),
        migrations.CreateModel(
            name='PlanRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('run_number', models.IntegerField()),
                ('status', models.CharField(choices=[('running', 'Running'), ('complete', 'Complete'), ('failed', 'Failed')], default='running', max_length=16)),
                ('driver_version', models.CharField(max_length=32)),
                ('error_message', models.TextField(blank=True, null=True)),
                ('started_at', models.DateTimeField()),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='runs', to='planning.plan')),
            ],
            options={
                'db_table': 'plan_run',
            },
        ),
        migrations.CreateModel(
            name='PlanRequirement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('level', models.IntegerField()),
                ('batch_number', models.IntegerField(default=1)),
                ('position', models.IntegerField(blank=True, null=True)),
                ('net_required', models.DecimalField(decimal_places=6, max_digits=16)),
                ('gross_required', models.DecimalField(decimal_places=6, max_digits=16)),
                ('yield_factor', models.DecimalField(decimal_places=6, max_digits=16)),
                ('process_loss', models.DecimalField(decimal_places=6, max_digits=16)),
                ('min_shelf_life_days', models.IntegerField(default=0)),
                ('stock_on_hand', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=16)),
                ('balance', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=16)),
                ('closed', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('default_resource', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='default_requirements', to='planning.resource')),
                ('destination_location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='plan_requirement_destinations', to='locations.location')),
                ('parent_requirement', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='planning.planrequirement')),
                ('plan_line', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='requirements', to='planning.planline')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plan_requirements', to='product.product')),
                ('recipe_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='plan_requirements', to='recipe.recipeversion')),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='requirements', to='planning.planrun')),
                ('source_location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='plan_requirement_sources', to='locations.location')),
            ],
            options={
                'db_table': 'plan_requirement',
            },
        ),
        migrations.CreateModel(
            name='PlanAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.DecimalField(decimal_places=6, max_digits=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('location', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plan_allocations', to='locations.location')),
                ('lot', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plan_allocations', to='stock_ledger.stocklot')),
                ('requirement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='allocations', to='planning.planrequirement')),
                ('stock_reservation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='plan_allocations', to='stock_ledger.stockreservation')),
            ],
            options={
                'db_table': 'plan_allocation',
            },
        ),
        migrations.CreateModel(
            name='PlanSupply',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('expected_at', models.DateTimeField()),
                ('quantity', models.DecimalField(decimal_places=6, max_digits=16)),
                ('kind', models.CharField(choices=[('purchase_order', 'Purchase order'), ('production_output', 'Production output'), ('opening', 'Opening'), ('manual', 'Manual')], max_length=32)),
                ('source_document_type', models.CharField(blank=True, max_length=24, null=True)),
                ('source_document_id', models.BigIntegerField(blank=True, null=True)),
                ('remarks', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('location', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plan_supplies', to='locations.location')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plan_supplies', to='product.product')),
                ('unit', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plan_supplies', to='product.unit')),
            ],
            options={
                'db_table': 'plan_supply',
            },
        ),
        migrations.CreateModel(
            name='PlanEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(max_length=64)),
                ('payload_json', models.JSONField(blank=True, null=True)),
                ('actor_user_id', models.BigIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='planning.plan')),
            ],
            options={
                'db_table': 'plan_event',
            },
        ),
        migrations.CreateModel(
            name='DemandProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('weekday', models.PositiveSmallIntegerField()),
                ('mean_quantity', models.DecimalField(decimal_places=6, max_digits=16)),
                ('sample_count', models.IntegerField()),
                ('computed_at', models.DateTimeField()),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='demand_profiles', to='product.product')),
            ],
            options={
                'db_table': 'demand_profile',
            },
        ),
        migrations.CreateModel(
            name='PlanResourceSlot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slot_date', models.DateField()),
                ('position', models.IntegerField()),
                ('job_start', models.DateTimeField(blank=True, null=True)),
                ('job_finish', models.DateTimeField(blank=True, null=True)),
                ('requirement', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='resource_slot', to='planning.planrequirement')),
                ('resource', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='slots', to='planning.resource')),
            ],
            options={
                'db_table': 'plan_resource_slot',
            },
        ),
        migrations.AddIndex(
            model_name='plan',
            index=models.Index(fields=['status', 'plan_date'], name='idx_plan_status_date'),
        ),
        migrations.AddIndex(
            model_name='plan',
            index=models.Index(fields=['location', 'plan_date'], name='idx_plan_location_date'),
        ),
        migrations.AddConstraint(
            model_name='plan',
            constraint=models.UniqueConstraint(fields=('plan_date', 'location'), name='uq_plan_date_location'),
        ),
        migrations.AddConstraint(
            model_name='plan',
            constraint=models.CheckConstraint(check=models.Q(('status__in', ['draft', 'locked', 'closed'])), name='chk_plan_status'),
        ),
        migrations.AddIndex(
            model_name='planline',
            index=models.Index(fields=['plan'], name='idx_plan_line_plan'),
        ),
        migrations.AddIndex(
            model_name='planline',
            index=models.Index(fields=['product'], name='idx_plan_line_product'),
        ),
        migrations.AddConstraint(
            model_name='planline',
            constraint=models.CheckConstraint(check=models.Q(('quantity__gt', Decimal('0'))), name='chk_plan_line_qty'),
        ),
        migrations.AddConstraint(
            model_name='planline',
            constraint=models.CheckConstraint(check=models.Q(('source__in', ['manual', 'order', 'forecast'])), name='chk_plan_line_source'),
        ),
        migrations.AddIndex(
            model_name='planrun',
            index=models.Index(fields=['plan', 'status'], name='idx_plan_run_plan_status'),
        ),
        migrations.AddConstraint(
            model_name='planrun',
            constraint=models.UniqueConstraint(fields=('plan', 'run_number'), name='uq_plan_run_number'),
        ),
        migrations.AddConstraint(
            model_name='planrun',
            constraint=models.CheckConstraint(check=models.Q(('status__in', ['running', 'complete', 'failed'])), name='chk_plan_run_status'),
        ),
        migrations.AddIndex(
            model_name='planrequirement',
            index=models.Index(fields=['run', 'level'], name='idx_plan_req_run_level'),
        ),
        migrations.AddIndex(
            model_name='planrequirement',
            index=models.Index(fields=['run', 'product'], name='idx_plan_req_run_product'),
        ),
        migrations.AddIndex(
            model_name='planrequirement',
            index=models.Index(fields=['run', 'closed'], name='idx_plan_req_run_closed'),
        ),
        migrations.AddIndex(
            model_name='planrequirement',
            index=models.Index(fields=['parent_requirement'], name='idx_plan_req_parent'),
        ),
        migrations.AddConstraint(
            model_name='planrequirement',
            constraint=models.CheckConstraint(check=models.Q(('level__gte', 1)), name='chk_plan_req_level'),
        ),
        migrations.AddConstraint(
            model_name='planrequirement',
            constraint=models.CheckConstraint(check=models.Q(('batch_number__gte', 1)), name='chk_plan_req_batch'),
        ),
        migrations.AddConstraint(
            model_name='planrequirement',
            constraint=models.CheckConstraint(check=models.Q(('net_required__gte', 0), ('gross_required__gte', 0)), name='chk_plan_req_qty'),
        ),
        migrations.AddConstraint(
            model_name='planrequirement',
            constraint=models.CheckConstraint(check=models.Q(('process_loss__gt', 0), ('yield_factor__gt', 0)), name='chk_plan_req_factors'),
        ),
        migrations.AddIndex(
            model_name='planallocation',
            index=models.Index(fields=['requirement'], name='idx_plan_alloc_req'),
        ),
        migrations.AddIndex(
            model_name='planallocation',
            index=models.Index(fields=['stock_reservation'], name='idx_plan_alloc_reservation'),
        ),
        migrations.AddConstraint(
            model_name='planallocation',
            constraint=models.CheckConstraint(check=models.Q(('quantity__gt', Decimal('0'))), name='chk_plan_alloc_qty'),
        ),
        migrations.AddConstraint(
            model_name='planallocation',
            constraint=models.UniqueConstraint(fields=('requirement', 'lot', 'location'), name='uq_plan_alloc_req_lot_loc'),
        ),
        migrations.AddIndex(
            model_name='plansupply',
            index=models.Index(fields=['product', 'location', 'expected_at'], name='idx_plan_supply_atp'),
        ),
        migrations.AddIndex(
            model_name='plansupply',
            index=models.Index(fields=['source_document_type', 'source_document_id'], name='idx_plan_supply_doc'),
        ),
        migrations.AddConstraint(
            model_name='plansupply',
            constraint=models.CheckConstraint(check=models.Q(('quantity__gt', Decimal('0'))), name='chk_plan_supply_qty'),
        ),
        migrations.AddConstraint(
            model_name='plansupply',
            constraint=models.CheckConstraint(check=models.Q(('kind__in', ['purchase_order', 'production_output', 'opening', 'manual'])), name='chk_plan_supply_kind'),
        ),
        migrations.AddIndex(
            model_name='planevent',
            index=models.Index(fields=['plan', 'created_at'], name='idx_plan_event_plan_time'),
        ),
        migrations.AddConstraint(
            model_name='demandprofile',
            constraint=models.UniqueConstraint(fields=('product', 'weekday'), name='uq_demand_profile_product_weekday'),
        ),
        migrations.AddConstraint(
            model_name='demandprofile',
            constraint=models.CheckConstraint(check=models.Q(('weekday__gte', 0), ('weekday__lte', 6)), name='chk_demand_profile_weekday'),
        ),
        migrations.AddConstraint(
            model_name='planresourceslot',
            constraint=models.UniqueConstraint(fields=('resource', 'slot_date', 'position'), name='uq_plan_resource_slot_pos'),
        ),
    ]
