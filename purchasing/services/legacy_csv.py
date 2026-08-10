"""Legacy Access Validation-tab CSV: 10 columns, no header row."""

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from locations.models import Location, LocationRole
from product.models import Product, ProductSupplier
from purchasing.models import PurchaseOrderSource, PurchaseOrderStatus
from purchasing.serialize import po_detail_dict
from purchasing.services.po import PoValidationError, create_purchase_order


class LegacyCsvError(ValueError):
    pass


DATE_FORMATS = (
    '%Y-%m-%d',
    '%d/%m/%Y',
    '%d-%m-%Y',
    '%m/%d/%Y',
    '%d/%m/%y',
    '%Y/%m/%d',
)


def _parse_legacy_date(raw, field_name: str, row_no: int):
    text = str(raw or '').strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise LegacyCsvError(
        f'Row {row_no}: invalid {field_name}={text!r}.',
    )


def _parse_qty(raw, row_no: int) -> Decimal:
    try:
        qty = Decimal(str(raw).strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LegacyCsvError(f'Row {row_no}: invalid quantity={raw!r}.') from exc
    if qty <= 0:
        raise LegacyCsvError(f'Row {row_no}: quantity must be > 0.')
    return qty


def _supplier_by_external_code(code: str, row_no: int) -> Location:
    code = str(code or '').strip()
    if not code:
        raise LegacyCsvError(f'Row {row_no}: supplier external code is required.')
    location = (
        Location.objects.filter(external_code=code, roles__role=LocationRole.SUPPLIER)
        .distinct()
        .first()
    )
    if location is None:
        raise LegacyCsvError(
            f'Row {row_no}: supplier external_code={code!r} not found.',
        )
    return location


def _resolve_product_supplier(
    *,
    product_id: int,
    supplier_id: int,
    shape_id,
    row_no: int,
) -> int | None:
    if shape_id in (None, ''):
        return None
    try:
        shape_id = int(str(shape_id).strip())
    except (TypeError, ValueError) as exc:
        raise LegacyCsvError(
            f'Row {row_no}: invalid shape id={shape_id!r}.',
        ) from exc

    row = ProductSupplier.objects.filter(
        pk=shape_id,
        product_id=product_id,
        supplier_id=supplier_id,
        is_active=True,
    ).first()
    if row is not None:
        return row.id

    row = ProductSupplier.objects.filter(
        product_id=product_id,
        supplier_id=supplier_id,
        purchase_shape_format_id=shape_id,
        is_active=True,
    ).first()
    if row is not None:
        return row.id
    return None


def _read_rows(file_bytes: bytes) -> list[list[str]]:
    text = file_bytes.decode('utf-8-sig', errors='replace')
    # sniffer is overkill; Access exports are comma or semicolon
    dialect = csv.excel
    if text.count(';') > text.count(','):
        dialect = csv.excel
        reader = csv.reader(io.StringIO(text), delimiter=';')
    else:
        reader = csv.reader(io.StringIO(text), dialect=dialect)
    rows = [list(r) for r in reader if any(str(c).strip() for c in r)]
    return rows


@transaction.atomic
def import_legacy_csv(
    *,
    file_bytes: bytes,
    dry_run: bool = False,
    status: str = PurchaseOrderStatus.ORDERED,
    created_by_user_id=None,
) -> dict:
    raw_rows = _read_rows(file_bytes)
    if not raw_rows:
        raise LegacyCsvError('CSV is empty.')

    errors = []
    # key: (legacy_po_number, supplier_id)
    groups: dict[tuple[str, int], dict] = {}

    for index, cols in enumerate(raw_rows, start=1):
        if len(cols) < 7:
            errors.append(f'Row {index}: expected at least 7 columns, got {len(cols)}.')
            continue
        # Pad to 10
        while len(cols) < 10:
            cols.append('')

        legacy_po = str(cols[0]).strip()
        supplier_code = cols[1]
        create_date = cols[2]
        delivery_date = cols[3]
        item_id = cols[4]
        # cols[5] item version — ignored (no version on Product)
        quantity = cols[6]
        shape_id = cols[7]
        # cols[8] shape label — informational
        line_value = cols[9]

        try:
            if not legacy_po:
                raise LegacyCsvError(f'Row {index}: PO number (col1) is required.')
            supplier = _supplier_by_external_code(supplier_code, index)
            try:
                product_id = int(str(item_id).strip())
            except (TypeError, ValueError) as exc:
                raise LegacyCsvError(
                    f'Row {index}: invalid item id={item_id!r}.',
                ) from exc
            if not Product.objects.filter(pk=product_id, is_active=True).exists():
                raise LegacyCsvError(
                    f'Row {index}: product_id={product_id} not found.',
                )
            qty = _parse_qty(quantity, index)
            ordered_at = _parse_legacy_date(create_date, 'create date', index)
            expected_at = _parse_legacy_date(delivery_date, 'delivery date', index)
            ps_id = _resolve_product_supplier(
                product_id=product_id,
                supplier_id=supplier.id,
                shape_id=shape_id,
                row_no=index,
            )
            unit_cost = None
            if str(line_value).strip():
                try:
                    unit_cost = (Decimal(str(line_value).strip()) / qty).quantize(
                        Decimal('0.000001'),
                    )
                except (InvalidOperation, ZeroDivisionError):
                    unit_cost = None
        except LegacyCsvError as exc:
            errors.append(str(exc))
            continue

        key = (legacy_po, supplier.id)
        if key not in groups:
            groups[key] = {
                'external_number': legacy_po,
                'supplier_id': supplier.id,
                'ordered_at': ordered_at,
                'expected_at': expected_at,
                'lines': [],
            }
        else:
            # keep first dates unless blank
            if ordered_at and not groups[key]['ordered_at']:
                groups[key]['ordered_at'] = ordered_at
            if expected_at and not groups[key]['expected_at']:
                groups[key]['expected_at'] = expected_at

        groups[key]['lines'].append({
            'product_id': product_id,
            'product_supplier_id': ps_id,
            'qty_ordered': str(qty),
            'unit_cost': str(unit_cost) if unit_cost is not None else None,
        })

    if errors and not groups:
        raise LegacyCsvError('; '.join(errors[:20]))

    created = []
    skipped = []
    if dry_run:
        for (legacy_po, supplier_id), payload in groups.items():
            created.append({
                'external_number': legacy_po,
                'supplier_id': supplier_id,
                'line_count': len(payload['lines']),
                'ordered_at': (
                    payload['ordered_at'].isoformat()
                    if payload['ordered_at'] else None
                ),
                'expected_at': (
                    payload['expected_at'].isoformat()
                    if payload['expected_at'] else None
                ),
                'dry_run': True,
            })
    else:
        for (legacy_po, supplier_id), payload in groups.items():
            try:
                po = create_purchase_order(
                    supplier_id=supplier_id,
                    lines=payload['lines'],
                    ordered_at=payload['ordered_at'],
                    expected_at=payload['expected_at'],
                    status=status,
                    source=PurchaseOrderSource.LEGACY_CSV,
                    external_number=legacy_po,
                    created_by_user_id=created_by_user_id,
                )
                created.append(po_detail_dict(po))
            except PoValidationError as exc:
                skipped.append({
                    'external_number': legacy_po,
                    'supplier_id': supplier_id,
                    'error': str(exc),
                })

    return {
        'dry_run': dry_run,
        'row_count': len(raw_rows),
        'po_count': len(created),
        'errors': errors,
        'skipped': skipped,
        'purchase_orders': created,
    }
