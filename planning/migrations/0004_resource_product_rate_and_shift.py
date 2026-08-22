# Chunk 1: production speed table (ResourceProductRate) + shift/break config
# (ResourceShift) + packing rate field on Resource.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0002_loc_edge_relation_type'),
        ('planning', '0003_resource_location_group'),
        ('product', '0014_product_image'),
    ]

    operations = [
        # ── Resource: add packing_rate_per_hour ──────────────────────────────
        migrations.AddField(
            model_name='resource',
            name='packing_rate_per_hour',
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                help_text='Cases per hour for packing line (Packing Plan Time — Rate Per Hour).',
                max_digits=10,
                null=True,
            ),
        ),

        # ── ResourceProductRate ───────────────────────────────────────────────
        migrations.CreateModel(
            name='ResourceProductRate',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                (
                    'resource',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='product_rates',
                        to='planning.resource',
                    ),
                ),
                (
                    'product',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='resource_rates',
                        to='product.product',
                    ),
                ),
                (
                    'staff_count',
                    models.PositiveSmallIntegerField(
                        help_text='Number Of People on the station (1–5).',
                    ),
                ),
                (
                    'units_per_minute',
                    models.DecimalField(
                        decimal_places=4,
                        help_text='Units produced per minute at this staff count.',
                        max_digits=10,
                    ),
                ),
                (
                    'batch_size',
                    models.DecimalField(
                        blank=True,
                        decimal_places=6,
                        help_text='One Mix Qty — units per mixer batch (CONTENT CODES col O).',
                        max_digits=16,
                        null=True,
                    ),
                ),
                ('notes', models.TextField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'resource_product_rate',
            },
        ),
        migrations.AddConstraint(
            model_name='resourceproductrate',
            constraint=models.UniqueConstraint(
                fields=['resource', 'product', 'staff_count'],
                name='uq_resource_product_rate',
            ),
        ),
        migrations.AddConstraint(
            model_name='resourceproductrate',
            constraint=models.CheckConstraint(
                check=models.Q(staff_count__gte=1) & models.Q(staff_count__lte=5),
                name='chk_resource_product_rate_staff_count',
            ),
        ),
        migrations.AddConstraint(
            model_name='resourceproductrate',
            constraint=models.CheckConstraint(
                check=models.Q(units_per_minute__gt=0),
                name='chk_resource_product_rate_upm',
            ),
        ),
        migrations.AddIndex(
            model_name='resourceproductrate',
            index=models.Index(
                fields=['product', 'is_active'],
                name='idx_rpr_product_active',
            ),
        ),
        migrations.AddIndex(
            model_name='resourceproductrate',
            index=models.Index(
                fields=['resource', 'is_active'],
                name='idx_rpr_resource_active',
            ),
        ),

        # ── ResourceShift ─────────────────────────────────────────────────────
        migrations.CreateModel(
            name='ResourceShift',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                (
                    'location',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='shifts',
                        to='locations.location',
                    ),
                ),
                (
                    'weekday',
                    models.PositiveSmallIntegerField(
                        blank=True,
                        help_text='0=Mon … 6=Sun. Null = applies every day of the week.',
                        null=True,
                    ),
                ),
                ('shift_start', models.TimeField(help_text='e.g. 06:30')),
                ('shift_end', models.TimeField(help_text='e.g. 18:00')),
                (
                    'morning_break_start',
                    models.TimeField(
                        blank=True,
                        help_text='Morning Tea start (e.g. 10:00).',
                        null=True,
                    ),
                ),
                (
                    'morning_break_minutes',
                    models.PositiveSmallIntegerField(
                        default=15,
                        help_text='Morning Tea duration in minutes.',
                    ),
                ),
                (
                    'lunch_break_start',
                    models.TimeField(
                        blank=True,
                        help_text='Lunch break start (e.g. 12:30).',
                        null=True,
                    ),
                ),
                (
                    'lunch_break_minutes',
                    models.PositiveSmallIntegerField(
                        default=30,
                        help_text='Lunch break duration in minutes.',
                    ),
                ),
                (
                    'evening_break_start',
                    models.TimeField(
                        blank=True,
                        help_text='Evening Tea start (e.g. 15:00).',
                        null=True,
                    ),
                ),
                (
                    'evening_break_minutes',
                    models.PositiveSmallIntegerField(
                        default=15,
                        help_text='Evening Tea duration in minutes.',
                    ),
                ),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'resource_shift',
            },
        ),
        migrations.AddConstraint(
            model_name='resourceshift',
            constraint=models.UniqueConstraint(
                fields=['location', 'weekday'],
                name='uq_resource_shift_location_weekday',
            ),
        ),
        migrations.AddConstraint(
            model_name='resourceshift',
            constraint=models.CheckConstraint(
                check=(
                    models.Q(weekday__isnull=True)
                    | (models.Q(weekday__gte=0) & models.Q(weekday__lte=6))
                ),
                name='chk_resource_shift_weekday',
            ),
        ),
        migrations.AddIndex(
            model_name='resourceshift',
            index=models.Index(
                fields=['location', 'weekday', 'is_active'],
                name='idx_resource_shift_lookup',
            ),
        ),
    ]
