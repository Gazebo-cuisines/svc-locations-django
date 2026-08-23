from django.db import migrations, models


def backfill_plan_number_and_name(apps, schema_editor):
    Plan = apps.get_model('planning', 'Plan')
    for plan in Plan.objects.order_by('id'):
        plan.plan_number = plan.id
        if not (plan.name or '').strip():
            plan.name = f'Production Plan - {plan.id}'
        plan.save(update_fields=['plan_number', 'name'])


class Migration(migrations.Migration):

    dependencies = [
        ('planning', '0005_run_stamp_req_calc'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='plan',
            name='uq_plan_date_location',
        ),
        migrations.AddField(
            model_name='plan',
            name='plan_number',
            field=models.PositiveIntegerField(null=True, unique=True),
        ),
        migrations.AddField(
            model_name='plan',
            name='name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.RunPython(backfill_plan_number_and_name, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='plan',
            name='plan_number',
            field=models.PositiveIntegerField(unique=True),
        ),
    ]
