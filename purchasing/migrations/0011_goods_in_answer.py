from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import migrations, models
import django.db.models.deletion


def _parse_date(value):
    if value in (None, ''):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _infer_input_type(value):
    if isinstance(value, bool):
        return 'bool'
    parsed = _parse_date(value)
    if parsed is not None and str(value)[:10] == parsed.isoformat():
        return 'date'
    if value not in (None, ''):
        try:
            Decimal(str(value))
            return 'decimal'
        except (InvalidOperation, TypeError, ValueError):
            pass
    return 'text'


def _typed(input_type, value):
    fields = {
        'value_bool': None,
        'value_decimal': None,
        'value_text': None,
        'value_date': None,
    }
    if value in (None, ''):
        return fields
    if input_type == 'bool':
        fields['value_bool'] = bool(value)
    elif input_type == 'decimal':
        try:
            fields['value_decimal'] = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            fields['value_text'] = str(value)
            fields['input_type_override'] = 'text'
    elif input_type == 'date':
        parsed = _parse_date(value)
        if parsed is None:
            fields['value_text'] = str(value)
            fields['input_type_override'] = 'text'
        else:
            fields['value_date'] = parsed
    else:
        fields['value_text'] = str(value)
    return fields


def _type_by_code(GoodsInCheckItem):
    mapping = {}
    for row in GoodsInCheckItem.objects.values('code', 'input_type'):
        mapping.setdefault(row['code'], row['input_type'])
    return mapping


def _rows_from_json(checks, type_by_code, answered_at, user_id, **parents):
    if not isinstance(checks, dict) or not checks:
        return []
    out = []
    for code, raw in checks.items():
        if not isinstance(raw, dict):
            raw = {'value': raw}
        value = raw.get('value')
        input_type = type_by_code.get(code) or _infer_input_type(value)
        typed = _typed(input_type, value)
        if 'input_type_override' in typed:
            input_type = typed.pop('input_type_override')
        out.append({
            **parents,
            'check_code': str(code)[:64],
            'input_type': input_type,
            'comment': raw.get('comment') or None,
            'answered_by_user_id': user_id,
            'answered_at': answered_at,
            **typed,
        })
    return out


def forwards(apps, schema_editor):
    GoodsInAnswer = apps.get_model('purchasing', 'GoodsInAnswer')
    GoodsInCheckItem = apps.get_model('purchasing', 'GoodsInCheckItem')
    Delivery = apps.get_model('purchasing', 'PurchaseOrderDelivery')
    DeliveryLine = apps.get_model('purchasing', 'PurchaseOrderDeliveryLine')
    AdhocSession = apps.get_model('purchasing', 'AdhocGoodsInSession')
    AdhocLine = apps.get_model('purchasing', 'AdhocGoodsInLine')
    type_by_code = _type_by_code(GoodsInCheckItem)
    rows = []

    for delivery in Delivery.objects.exclude(header_checks={}):
        rows.extend(_rows_from_json(
            delivery.header_checks,
            type_by_code,
            delivery.checked_at or delivery.updated_at or delivery.created_at,
            delivery.checked_by_user_id,
            delivery_id=delivery.id,
            scope='header',
        ))
    for dline in DeliveryLine.objects.exclude(line_checks={}):
        rows.extend(_rows_from_json(
            dline.line_checks,
            type_by_code,
            dline.updated_at or dline.created_at,
            None,
            delivery_id=dline.delivery_id,
            delivery_line_id=dline.id,
            scope='line',
        ))
    for session in AdhocSession.objects.exclude(header_checks={}):
        rows.extend(_rows_from_json(
            session.header_checks,
            type_by_code,
            session.checked_at or session.updated_at or session.created_at,
            session.checked_by_user_id,
            adhoc_session_id=session.id,
            scope='header',
        ))
    for aline in AdhocLine.objects.exclude(line_checks={}):
        rows.extend(_rows_from_json(
            aline.line_checks,
            type_by_code,
            aline.updated_at or aline.created_at,
            None,
            adhoc_session_id=aline.session_id,
            adhoc_line_id=aline.id,
            scope='line',
        ))
    if rows:
        GoodsInAnswer.objects.bulk_create([
            GoodsInAnswer(**row) for row in rows
        ])


def backwards(apps, schema_editor):
    GoodsInAnswer = apps.get_model('purchasing', 'GoodsInAnswer')
    GoodsInAnswer.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0010_coa_coc_no_comment'),
    ]

    operations = [
        migrations.CreateModel(
            name='GoodsInAnswer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scope', models.CharField(choices=[('header', 'Header'), ('line', 'Line')], max_length=16)),
                ('check_code', models.CharField(max_length=64)),
                ('input_type', models.CharField(choices=[('bool', 'Yes/No'), ('decimal', 'Decimal'), ('text', 'Text'), ('date', 'Date')], max_length=16)),
                ('value_bool', models.BooleanField(blank=True, null=True)),
                ('value_decimal', models.DecimalField(blank=True, decimal_places=6, max_digits=16, null=True)),
                ('value_text', models.TextField(blank=True, null=True)),
                ('value_date', models.DateField(blank=True, null=True)),
                ('comment', models.TextField(blank=True, null=True)),
                ('answered_by_user_id', models.IntegerField(blank=True, null=True)),
                ('answered_at', models.DateTimeField()),
                ('adhoc_line', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='answers', to='purchasing.adhocgoodsinline')),
                ('adhoc_session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='answers', to='purchasing.adhocgoodsinsession')),
                ('delivery', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='answers', to='purchasing.purchaseorderdelivery')),
                ('delivery_line', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='answers', to='purchasing.purchaseorderdeliveryline')),
            ],
            options={
                'db_table': 'po_goods_in_answer',
                'ordering': ['id'],
            },
        ),
        migrations.AddIndex(
            model_name='goodsinanswer',
            index=models.Index(fields=['delivery', 'scope'], name='idx_gin_answer_delivery'),
        ),
        migrations.AddIndex(
            model_name='goodsinanswer',
            index=models.Index(fields=['adhoc_session', 'scope'], name='idx_gin_answer_adhoc'),
        ),
        migrations.AddConstraint(
            model_name='goodsinanswer',
            constraint=models.CheckConstraint(check=models.Q(('scope__in', ['header', 'line'])), name='chk_gin_answer_scope'),
        ),
        migrations.AddConstraint(
            model_name='goodsinanswer',
            constraint=models.CheckConstraint(check=models.Q(('input_type__in', ['bool', 'decimal', 'text', 'date'])), name='chk_gin_answer_input_type'),
        ),
        migrations.AddConstraint(
            model_name='goodsinanswer',
            constraint=models.CheckConstraint(
                check=models.Q(
                    models.Q(
                        models.Q(('adhoc_line_id__isnull', True), ('adhoc_session_id__isnull', True), ('delivery_id__isnull', False)),
                        models.Q(models.Q(('delivery_line_id__isnull', True), ('scope', 'header')), models.Q(('delivery_line_id__isnull', False), ('scope', 'line')), _connector='OR'),
                    ),
                    models.Q(
                        models.Q(('adhoc_session_id__isnull', False), ('delivery_id__isnull', True), ('delivery_line_id__isnull', True)),
                        models.Q(models.Q(('adhoc_line_id__isnull', True), ('scope', 'header')), models.Q(('adhoc_line_id__isnull', False), ('scope', 'line')), _connector='OR'),
                    ),
                    _connector='OR',
                ),
                name='chk_gin_answer_parent',
            ),
        ),
        migrations.AddConstraint(
            model_name='goodsinanswer',
            constraint=models.UniqueConstraint(condition=models.Q(('delivery_id__isnull', False), ('scope', 'header')), fields=('delivery', 'check_code'), name='uniq_gin_answer_po_header'),
        ),
        migrations.AddConstraint(
            model_name='goodsinanswer',
            constraint=models.UniqueConstraint(condition=models.Q(('delivery_line_id__isnull', False)), fields=('delivery_line', 'check_code'), name='uniq_gin_answer_po_line'),
        ),
        migrations.AddConstraint(
            model_name='goodsinanswer',
            constraint=models.UniqueConstraint(condition=models.Q(('adhoc_session_id__isnull', False), ('scope', 'header')), fields=('adhoc_session', 'check_code'), name='uniq_gin_answer_adhoc_header'),
        ),
        migrations.AddConstraint(
            model_name='goodsinanswer',
            constraint=models.UniqueConstraint(condition=models.Q(('adhoc_line_id__isnull', False)), fields=('adhoc_line', 'check_code'), name='uniq_gin_answer_adhoc_line'),
        ),
        migrations.RunPython(forwards, backwards),
    ]
