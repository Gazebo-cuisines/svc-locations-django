"""GFF001F-style printable goods-in PDF from stored PO QC answers."""

import io
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from locations.models import LocationAddress, LocationContact
from purchasing.services.goods_in_form import GoodsInFormError, resolve_goods_in_form

_LOGO_PATH = Path(__file__).resolve().parent.parent / 'assests' / 'image.png'

_BLACK = colors.black
_LIGHT = colors.Color(0.93, 0.93, 0.93)
_GRID = TableStyle([
    ('GRID', (0, 0), (-1, -1), 0.6, _BLACK),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('LEFTPADDING', (0, 0), (-1, -1), 4),
    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ('TOPPADDING', (0, 0), (-1, -1), 3),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
])


def _answer_value(answers: dict, code: str):
    raw = (answers or {}).get(code)
    if isinstance(raw, dict):
        return raw.get('value')
    return raw


def _answer_comment(answers: dict, code: str) -> str:
    raw = (answers or {}).get(code)
    if isinstance(raw, dict):
        return str(raw.get('comment') or '')
    return ''


def _yn_circle(value) -> str:
    """Match paper: circle the chosen YES/NO."""
    if value is True:
        return '(YES)  NO'
    if value is False:
        return 'YES  (NO)'
    return 'YES / NO'


def _fmt(value) -> str:
    if value is None:
        return ''
    return str(value)


def _fmt_date(value) -> str:
    """ISO / date → dd/mm/yyyy (GFF001F paper style)."""
    if value in (None, ''):
        return ''
    text = str(value).strip()[:10]
    if len(text) >= 10 and text[4] == '-' and text[7] == '-':
        y, m, d = text[:4], text[5:7], text[8:10]
        return f'{d}/{m}/{y}'
    return text


def _qty_display(line: dict) -> str:
    visit = line.get('delivery_qty_received')
    if visit not in (None, ''):
        return _fmt(visit)
    recv = line.get('qty_received')
    if recv not in (None, '', '0'):
        return _fmt(recv)
    return _fmt(line.get('qty_ordered'))


def _supplier_contact(supplier_id: int | None) -> tuple[str, str]:
    if not supplier_id:
        return '', ''
    addr = (
        LocationAddress.objects.filter(location_id=supplier_id, is_primary=True)
        .first()
        or LocationAddress.objects.filter(location_id=supplier_id).first()
    )
    contact = LocationContact.objects.filter(location_id=supplier_id).first()
    address = (addr.address if addr else '') or ''
    tel = ''
    if contact and contact.phone:
        tel = contact.phone
    elif addr and addr.contact_point_phone:
        tel = addr.contact_point_phone
    return address, tel


def _total_qty(lines: list) -> str:
    total = Decimal('0')
    for line in lines:
        raw = _qty_display(line)
        try:
            total += Decimal(str(raw or '0'))
        except (InvalidOperation, TypeError, ValueError):
            continue
    text = f'{total:f}'
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def build_goods_in_pdf(po_id: int, delivery_id: int | None = None) -> bytes:
    form = resolve_goods_in_form(po_id, delivery_id=delivery_id)
    doc_meta = form['header']['document']
    answers = form.get('saved_header_answers') or {}
    lines = form.get('lines') or []
    address, tel = _supplier_contact(form.get('supplier_id'))

    styles = getSampleStyleSheet()
    logo = Image(str(_LOGO_PATH), width=32 * mm, height=28 * mm)
    logo.hAlign = 'CENTER'
    title = ParagraphStyle(
        'GiTitle', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=16, leading=18, alignment=1,
    )
    small = ParagraphStyle(
        'GiSmall', parent=styles['Normal'], fontSize=8, leading=10,
    )
    label = ParagraphStyle(
        'GiLabel', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, leading=11,
    )
    body = ParagraphStyle(
        'GiBody', parent=styles['Normal'], fontSize=9, leading=11,
    )
    tiny = ParagraphStyle(
        'GiTiny', parent=styles['Normal'], fontSize=7, leading=9,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"Goods Inward {form['number'] or po_id}",
    )
    story = []
    page_w = A4[0] - 20 * mm

    # --- Header: brand | title | document control ---
    ctrl_data = [
        [Paragraph('<b>Document No:</b>', tiny), Paragraph(_fmt(doc_meta.get('document_no')), tiny)],
        [Paragraph('<b>Issue no:</b>', tiny), Paragraph(_fmt(doc_meta.get('issue_no')), tiny)],
        [Paragraph('<b>Issue Date:</b>', tiny), Paragraph(_fmt_date(doc_meta.get('issue_date')), tiny)],
        [Paragraph('<b>Review Date:</b>', tiny), Paragraph(_fmt_date(doc_meta.get('review_date')), tiny)],
        [Paragraph('<b>Previous Issue Date:</b>', tiny), Paragraph(_fmt_date(doc_meta.get('previous_issue_date')), tiny)],
        [Paragraph('<b>Reason for change:</b>', tiny), Paragraph(_fmt(doc_meta.get('reason_for_change')), tiny)],
    ]
    ctrl = Table(ctrl_data, colWidths=[32 * mm, 28 * mm])
    ctrl.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, _BLACK),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))

    header = Table(
        [[
            logo,
            Paragraph('GOODS INWARD FORM', title),
            ctrl,
        ]],
        colWidths=[40 * mm, page_w - 100 * mm, 60 * mm],
    )
    header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (1, 0), 'CENTER'),
        ('BOX', (0, 0), (-1, -1), 1, _BLACK),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(header)
    story.append(Spacer(1, 4 * mm))

    # --- Supplier (left) + delivery checks (right) ---
    supplier_inner = [
        [Paragraph('<b>Supplier Name:</b>', label)],
        [Paragraph(_fmt(form.get('supplier_name')), body)],
        [Paragraph('<b>Address:</b>', label)],
        [Paragraph(address.replace('\n', '<br/>') if address else '&nbsp;<br/>&nbsp;', body)],
        [Paragraph(f'<b>Tel:</b> {_fmt(tel)}&nbsp;&nbsp;&nbsp;<b>Fax:</b>', body)],
    ]
    supplier_box = Table(supplier_inner, colWidths=[88 * mm])
    supplier_box.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, _BLACK),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('BACKGROUND', (0, 0), (-1, 0), _LIGHT),
    ]))

    delivery_date = form.get('delivery_at') or form.get('suggested_delivery_date') or ''
    trace = form.get('delivery_trace_number') or form.get('suggested_trace_number') or ''
    header_items = form.get('header', {}).get('items') or []
    skip_codes = {'comment', 'random_qc_tl_check'}
    meta_after = (
        'primary_outer_packaging_damaged'
        if any(i.get('code') == 'primary_outer_packaging_damaged' for i in header_items)
        else 'vehicle_clean_fb_pest_odour'
    )
    meta_rows = [
        [
            Paragraph('<b>Delivery Date</b>', small),
            Paragraph(_fmt_date(delivery_date), body),
        ],
        [
            Paragraph('<b>Order No</b>', small),
            Paragraph(_fmt(form.get('number')), body),
        ],
        [
            Paragraph('<b>Trace No</b>', small),
            Paragraph(_fmt(trace), body),
        ],
    ]
    checks = []
    meta_done = False
    for item in header_items:
        code = item.get('code')
        if not code or code in skip_codes:
            continue
        label_txt = (item.get('label') or code).replace('&', '&amp;')
        value = _answer_value(answers, code)
        if code == 'vehicle_temperature':
            if value in (None, ''):
                value = form.get('vehicle_temperature')
            display = 'N/A' if value in (None, '') else _fmt(value)
        elif code == 'reject_delivery':
            if value is None:
                value = form.get('reject_delivery')
            reject_comment = _answer_comment(answers, 'reject_delivery')
            display = _yn_circle(value)
            if reject_comment:
                display += f'<br/>Comment: {reject_comment}'
        elif isinstance(value, bool):
            display = _yn_circle(value)
        else:
            display = _fmt(value)
        checks.append([
            Paragraph(f'<b>{label_txt}</b>', small),
            Paragraph(display, small if code == 'reject_delivery' else body),
        ])
        if code == meta_after:
            checks.extend(meta_rows)
            meta_done = True
    if not meta_done:
        checks[0:0] = meta_rows
    checks.append([
        Paragraph('<b>Checked By</b>', small),
        Paragraph(_fmt(form.get('checked_by_user_id')), body),
    ])
    checks_box = Table(checks, colWidths=[62 * mm, 38 * mm])
    checks_box.setStyle(_GRID)

    mid = Table(
        [[supplier_box, checks_box]],
        colWidths=[90 * mm, page_w - 90 * mm],
    )
    mid.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 3),
        ('LEFTPADDING', (1, 0), (1, 0), 3),
    ]))
    story.append(mid)
    story.append(Spacer(1, 4 * mm))

    # --- Product lines (paper columns) ---
    prod_header = [
        Paragraph('<b>Qty</b>', small),
        Paragraph('<b>Product Description</b>', small),
        Paragraph('<b>Pack Size</b>', small),
        Paragraph('<b>UBD / BBE</b>', small),
        Paragraph('<b>Product Temp</b>', small),
        Paragraph('<b>Spec Check</b>', small),
    ]
    prod_rows = [prod_header]
    for line in lines:
        la = line.get('saved_answers') or {}
        use_by = _answer_value(la, 'use_by')
        temp = _answer_value(la, 'product_temperature')
        spec = _answer_value(la, 'spec_check')
        if isinstance(spec, bool):
            spec_txt = 'yes' if spec else 'no'
        else:
            spec_txt = _fmt(spec)
        prod_rows.append([
            Paragraph(_qty_display(line), small),
            Paragraph(_fmt(line.get('product_name')), small),
            Paragraph(_fmt(line.get('pack_size')), small),
            Paragraph(_fmt_date(use_by), small),
            Paragraph(_fmt(temp), small),
            Paragraph(spec_txt, small),
        ])
    # Blank rows so it looks like the paper form when few lines
    while len(prod_rows) < 8:
        prod_rows.append(['', '', '', '', '', ''])

    prod = Table(
        prod_rows,
        colWidths=[22 * mm, 70 * mm, 28 * mm, 28 * mm, 26 * mm, 26 * mm],
        repeatRows=1,
    )
    prod.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.6, _BLACK),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('BACKGROUND', (0, 0), (-1, 0), _LIGHT),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 1), (0, -1), 'RIGHT'),
        ('ALIGN', (3, 1), (5, -1), 'CENTER'),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
    ]))
    story.append(prod)
    story.append(Spacer(1, 2 * mm))

    # --- Total Qty ---
    total = Table(
        [[Paragraph(f'<b>Total Qty:</b> {_total_qty(lines)}', body)]],
        colWidths=[page_w],
    )
    total.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, _BLACK),
        ('BACKGROUND', (0, 0), (-1, -1), _LIGHT),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(total)
    story.append(Spacer(1, 3 * mm))

    # --- Temperature guidelines (paper footer notice) ---
    guidelines = Paragraph(
        '<b>Temperature Guidelines:</b> '
        'Chilled goods 0–8°C &nbsp;|&nbsp; '
        'Frozen goods −18°C ± 3°C &nbsp;|&nbsp; '
        'Chicken &lt;4°C &nbsp;|&nbsp; '
        'Lamb &amp; Beef &lt;7°C',
        tiny,
    )
    story.append(guidelines)
    story.append(Spacer(1, 3 * mm))

    # --- Comment ---
    comment = (
        _fmt(form.get('qc_tl_comment'))
        or _fmt(_answer_value(answers, 'comment'))
        or _answer_comment(answers, 'random_qc_tl_check')
        or ''
    )
    qc_flag = _answer_value(answers, 'random_qc_tl_check')
    if qc_flag is True and not comment:
        comment = 'Random QC or TL Check'
    comment_box = Table(
        [
            [Paragraph('<b>Comment:</b>', label)],
            [Paragraph(comment.replace('\n', '<br/>') if comment else '&nbsp;<br/>&nbsp;<br/>&nbsp;', body)],
        ],
        colWidths=[page_w],
    )
    comment_box.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, _BLACK),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), _LIGHT),
    ]))
    story.append(comment_box)

    doc.build(story)
    return buf.getvalue()


def pdf_http_response(po_id: int, delivery_id: int | None = None) -> HttpResponse:
    try:
        pdf = build_goods_in_pdf(po_id, delivery_id=delivery_id)
    except GoodsInFormError:
        raise
    suffix = f'{po_id}-{delivery_id}' if delivery_id else str(po_id)
    filename = f'goods-in-{suffix}.pdf'
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
