from django.core.exceptions import ObjectDoesNotExist

from product.audit_log import _actor_stamp
from purchasing.models import PurchaseOrder, PurchaseOrderHistory
from purchasing.serialize import _iso_dt, _qty_str, rbac_actors
from stock_ledger.models import StockEntry


def _related(obj, name):
    try:
        return getattr(obj, name)
    except ObjectDoesNotExist:
        return None


def actor_json(request=None, *, user_id=None, audit=None) -> dict:
    audit = dict(audit or {})
    if user_id in (None, ''):
        user_id = audit.get('actor_user_id')
    try:
        user_id = int(user_id) if user_id not in (None, '') else None
    except (TypeError, ValueError):
        user_id = None

    stamp = {}
    if request is not None:
        stamp = _actor_stamp(request)
        rbac = getattr(request, 'rbac_user', None)
        if rbac is not None and user_id is None:
            user_id = rbac.id

    stored = rbac_actors({user_id}).get(user_id, {}) if user_id else {}
    return {
        'user_id': user_id,
        'sub': stamp.get('actor_sub') or stored.get('sub'),
        'name': (
            stamp.get('actor_name')
            or audit.get('lan_username')
            or stored.get('name')
        ),
        'email': stamp.get('actor_email') or stored.get('email'),
        'source_workstation': (
            stamp.get('source_workstation') or audit.get('source_workstation')
        ),
        'source_workstation_ip': (
            stamp.get('source_workstation_ip')
            or audit.get('source_workstation_ip')
        ),
    }


def record_history(
    *,
    po,
    event_type,
    before,
    after,
    actor=None,
    delivery=None,
    remarks=None,
):
    actor = dict(actor or {})
    after = dict(after or {})
    before = dict(before or {})
    PurchaseOrderHistory.objects.create(
        purchase_order=po,
        delivery=delivery,
        event_type=event_type,
        remarks=remarks,
        payload={
            **after,
            'before_json': before,
            'after_json': after,
            'actor': actor,
        },
        actor_user_id=actor.get('user_id'),
    )


def _line_snapshot(line) -> dict:
    return {
        'id': line.id,
        'line_no': line.line_no,
        'product_id': line.product_id,
        'product_name': line.product.name if line.product_id else None,
        'qty_ordered': _qty_str(line.qty_ordered),
        'qty_received': _qty_str(line.qty_received),
        'qty_rejected': _qty_str(line.qty_rejected),
        'qty_balance': _qty_str(line.qty_balance),
        'unit_id': line.unit_id,
        'unit_name': line.unit.name if line.unit_id else None,
        'unit_cost': _qty_str(line.unit_cost),
        'multiplier': _qty_str(line.multiplier),
        'shape_format_label': line.shape_format_label,
        'product_supplier_id': line.product_supplier_id,
        'remarks': line.remarks,
    }


def _flatten_line_fields(lines: list) -> dict:
    """Scalar keys so timeline tables can show Before/After without [object Object]."""
    flat = {}
    for line in lines or []:
        no = line.get('line_no')
        prefix = f'Line {no}'
        name = line.get('product_name') or f"product {line.get('product_id')}"
        # qty_ordered is pack/count on the PO; unit_name is inner stock unit (e.g. Kg) — do not append it.
        qty = line.get('qty_ordered') or '0'
        pack = line.get('shape_format_label')
        flat[f'{prefix} product'] = name
        flat[f'{prefix} qty ordered'] = qty
        if pack:
            flat[f'{prefix} pack'] = pack
        if line.get('remarks'):
            flat[f'{prefix} remarks'] = line['remarks']
    return flat


def po_snapshot(po) -> dict:
    lines = list(
        po.lines.select_related('product', 'unit').order_by('line_no'),
    )
    line_rows = [_line_snapshot(line) for line in lines]
    return {
        'id': po.id,
        'number': po.number,
        'status': po.status,
        'revision_no': po.revision_no,
        'sage_po_number': po.external_number,
        'supplier_id': po.supplier_id,
        'ship_to_location_id': po.ship_to_location_id,
        'ordered_at': po.ordered_at.isoformat() if po.ordered_at else None,
        'expected_at': po.expected_at.isoformat() if po.expected_at else None,
        'remarks': po.remarks,
        'source': po.source,
        'reject_delivery': po.reject_delivery,
        'lines': line_rows,
        **_flatten_line_fields(line_rows),
    }


def _timeline_changed_fields(before_data, after_data) -> list[str]:
    """Skip nested objects (e.g. lines[]) — FE stringifies those as [object Object]."""
    before = before_data or {}
    after = after_data or {}
    keys = (set(before.keys()) | set(after.keys())) - {'lines'}
    return sorted(
        key
        for key in keys
        if before.get(key) != after.get(key)
        and not isinstance(before.get(key), (dict, list))
        and not isinstance(after.get(key), (dict, list))
    )


def _event(*, at, entity, action, actor=None, before_json=None, after_json=None):
    actor = dict(actor or {})
    before = before_json or {}
    after = after_json or {}
    return {
        'at': _iso_dt(at),
        'entity': entity,
        'action': action,
        'actor': actor,
        'actor_sub': actor.get('sub'),
        'actor_name': actor.get('name'),
        'actor_email': actor.get('email'),
        'request_method': None,
        'request_path': None,
        'source_workstation_ip': actor.get('source_workstation_ip'),
        'source_workstation': actor.get('source_workstation'),
        'before_json': before,
        'after_json': after,
        'changed_fields': _timeline_changed_fields(before, after),
    }


def _history_entity(row: PurchaseOrderHistory) -> str:
    payload = row.payload or {}
    after = payload.get('after_json') if isinstance(payload.get('after_json'), dict) else payload
    if after.get('line_id') is not None or after.get('line_no') is not None:
        return 'po_line'
    if row.delivery_id is not None or after.get('delivery_id') is not None:
        return 'delivery'
    return 'purchase_order'


def _payload_parts(payload: dict, *, fallback_actor: dict) -> tuple[dict, dict, dict]:
    payload = payload or {}
    before = payload.get('before_json') if isinstance(payload.get('before_json'), dict) else {}
    after = payload.get('after_json') if isinstance(payload.get('after_json'), dict) else None
    actor = payload.get('actor') if isinstance(payload.get('actor'), dict) else None
    if after is None:
        after = {
            k: v for k, v in payload.items()
            if k not in ('before_json', 'after_json', 'actor')
        }
    if not actor:
        actor = fallback_actor
    return before, after, actor


def _stock_actor(user_id, actors, *, lan_username=None, workstation=None, ip=None):
    actor = dict(actors.get(user_id) or {'user_id': user_id, 'sub': None, 'name': None, 'email': None})
    if not actor.get('name'):
        actor['name'] = lan_username
    actor['source_workstation'] = workstation
    actor['source_workstation_ip'] = ip
    return actor


def po_timeline(po_id: int) -> list[dict]:
    po = PurchaseOrder.objects.get(pk=po_id)
    history = list(po.history.all())
    entries = list(
        StockEntry.objects
        .filter(source_document_type='po', source_document_id=po_id)
        .select_related('label', 'posting')
        .prefetch_related('label_scans')
    )

    actor_ids = {po.created_by_user_id}
    actor_ids.update(row.actor_user_id for row in history)
    actor_ids.update(entry.actor_user_id for entry in entries)
    for entry in entries:
        label = _related(entry, 'label')
        if label is not None:
            actor_ids.add(label.actor_user_id)
        posting = _related(entry, 'posting')
        if posting is not None:
            actor_ids.add(posting.actor_user_id)
        for scan in entry.label_scans.all():
            actor_ids.add(scan.actor_user_id)
    actors = rbac_actors(actor_ids)

    events = []
    if not any(row.event_type == 'create' for row in history):
        created_actor = _stock_actor(po.created_by_user_id, actors)
        events.append(_event(
            at=po.created_at,
            entity='purchase_order',
            action='create',
            actor=created_actor,
            before_json={},
            after_json=po_snapshot(po),
        ))

    for row in history:
        fallback = _stock_actor(row.actor_user_id, actors)
        before, after, actor = _payload_parts(row.payload, fallback_actor=fallback)
        events.append(_event(
            at=row.created_at,
            entity=_history_entity(row),
            action=row.event_type,
            actor=actor,
            before_json=before,
            after_json=after,
        ))

    for entry in entries:
        entry_after = {
            'entry_id': entry.id,
            'line_no': entry.source_document_line,
            'quantity': str(entry.quantity),
        }
        events.append(_event(
            at=entry.recorded_at,
            entity='stock_entry',
            action='goods_in',
            actor=_stock_actor(
                entry.actor_user_id, actors,
                lan_username=entry.lan_username,
                workstation=entry.source_workstation,
                ip=entry.source_workstation_ip,
            ),
            before_json={},
            after_json=entry_after,
        ))
        label = _related(entry, 'label')
        if label is not None and label.printed_at:
            events.append(_event(
                at=label.printed_at,
                entity='stock_entry',
                action='label_printed',
                actor=_stock_actor(
                    label.actor_user_id, actors,
                    lan_username=label.lan_username,
                    workstation=label.source_workstation,
                ),
                before_json={**entry_after, 'printed_at': None},
                after_json={
                    **entry_after,
                    'printed_at': _iso_dt(label.printed_at),
                    'label_count': label.label_count,
                },
            ))
        for scan in entry.label_scans.all():
            events.append(_event(
                at=scan.scanned_at,
                entity='stock_entry',
                action='label_scanned',
                actor=_stock_actor(
                    scan.actor_user_id, actors,
                    lan_username=scan.lan_username,
                    workstation=scan.source_workstation,
                ),
                before_json=entry_after,
                after_json={
                    **entry_after,
                    'code': scan.code,
                    'result': scan.result,
                },
            ))
        posting = _related(entry, 'posting')
        if posting is not None and posting.posted_at:
            events.append(_event(
                at=posting.posted_at,
                entity='stock_entry',
                action='stock_posted',
                actor=_stock_actor(
                    posting.actor_user_id, actors,
                    lan_username=posting.lan_username,
                    workstation=posting.source_workstation,
                ),
                before_json={**entry_after, 'status': 'queued'},
                after_json={**entry_after, 'status': 'posted'},
            ))

    events.sort(key=lambda e: e['at'] or '', reverse=True)
    return events
