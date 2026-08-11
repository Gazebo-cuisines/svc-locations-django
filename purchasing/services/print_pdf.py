"""GFF001F-style printable goods-in PDF from stored PO QC answers."""

import io

from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from purchasing.services.goods_in_form import GoodsInFormError, resolve_goods_in_form


def _fmt_answer(raw) -> str:
    if raw is None:
        return ''
    if isinstance(raw, dict):
        value = raw.get('value')
        comment = raw.get('comment')
        if value is True:
            text = 'Yes'
        elif value is False:
            text = 'No'
        elif value is None:
            text = ''
        else:
            text = str(value)
        if comment:
            text = f'{text} ({comment})' if text else str(comment)
        return text
    return str(raw)


def _yn(value) -> str:
    if value is True:
        return 'Yes'
    if value is False:
        return 'No'
    return ''


def build_goods_in_pdf(po_id: int) -> bytes:
    form = resolve_goods_in_form(po_id)
    doc_meta = form['header']['document']
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        'GiTitle', parent=styles['Heading1'], fontSize=14, spaceAfter=6,
    )
    h2 = ParagraphStyle(
        'GiH2', parent=styles['Heading2'], fontSize=11, spaceBefore=8, spaceAfter=4,
    )
    body = ParagraphStyle('GiBody', parent=styles['Normal'], fontSize=9, leading=11)
    small = ParagraphStyle('GiSmall', parent=styles['Normal'], fontSize=8, leading=10)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"Goods Inward {form['number'] or po_id}",
    )
    story = []

    story.append(Paragraph('Goods Inward Form', title))
    ctrl = [
        ['Document No', doc_meta.get('document_no') or ''],
        ['Issue No', doc_meta.get('issue_no') or ''],
        ['Issue Date', doc_meta.get('issue_date') or ''],
        ['Review Date', doc_meta.get('review_date') or ''],
        ['Previous Issue', doc_meta.get('previous_issue_date') or ''],
        ['Reason for Change', doc_meta.get('reason_for_change') or ''],
    ]
    t = Table(ctrl, colWidths=[40 * mm, 140 * mm])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (0, -1), colors.Color(0.95, 0.95, 0.95)),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))

    header_rows = [
        ['PO Number', form.get('number') or ''],
        ['Supplier', form.get('supplier_name') or ''],
        ['Status', form.get('status') or ''],
        ['Ordered', form.get('ordered_at') or ''],
        ['Expected', form.get('expected_at') or ''],
        ['Delivery Date', form.get('delivery_at') or form.get('suggested_delivery_date') or ''],
        ['Trace No', form.get('delivery_trace_number') or form.get('suggested_trace_number') or ''],
        ['Vehicle Temp', form.get('vehicle_temperature') or ''],
        ['Reject Delivery', _yn(form.get('reject_delivery'))],
        ['Checked By', str(form.get('checked_by_user_id') or '')],
        ['Checked At', form.get('checked_at') or ''],
        ['QC/TL By', str(form.get('qc_tl_checked_by_user_id') or '')],
        ['QC/TL At', form.get('qc_tl_checked_at') or ''],
        ['QC/TL Comment', form.get('qc_tl_comment') or ''],
    ]
    story.append(Paragraph('Delivery header', h2))
    ht = Table(header_rows, colWidths=[40 * mm, 140 * mm])
    ht.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(ht)

    story.append(Paragraph('Header checks', h2))
    answers = form.get('saved_header_answers') or {}
    check_rows = [['Code', 'Check', 'Answer']]
    for item in form['header']['items']:
        check_rows.append([
            item['code'],
            Paragraph(item['label'], small),
            Paragraph(_fmt_answer(answers.get(item['code'])), small),
        ])
    ct = Table(check_rows, colWidths=[28 * mm, 100 * mm, 52 * mm])
    ct.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(ct)

    for line in form['lines']:
        story.append(Paragraph(
            f"Line {line['line_no']}: {line['product_name']} "
            f"(ord {line['qty_ordered']} / recv {line['qty_received']} / "
            f"bal {line['qty_balance']})",
            h2,
        ))
        story.append(Paragraph(
            f"Type={line['goods_in_type']} regime={line['storage_regime'] or '-'} "
            f"OK={_yn(line.get('line_check_ok'))}",
            body,
        ))
        la = line.get('saved_answers') or {}
        lrows = [['Code', 'Check', 'Answer']]
        for item in line['template']['items']:
            lrows.append([
                item['code'],
                Paragraph(item['label'], small),
                Paragraph(_fmt_answer(la.get(item['code'])), small),
            ])
        lt = Table(lrows, colWidths=[28 * mm, 100 * mm, 52 * mm])
        lt.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(lt)

    doc.build(story)
    return buf.getvalue()


def pdf_http_response(po_id: int) -> HttpResponse:
    try:
        pdf = build_goods_in_pdf(po_id)
    except GoodsInFormError:
        raise
    filename = f'goods-in-{po_id}.pdf'
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
