"""Diff the batch-multiplier driver's RM rollup against a day-plan workbook."""

import os
import sys
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from planning.models import Plan, PlanLine, PlanLineSource, PlanStatus  # noqa: E402
from planning.services import excel_compare as ec  # noqa: E402
from planning.services import explode_batchmult  # noqa: E402
from product.models import Product  # noqa: E402

PATH = sys.argv[1]
LOCATION_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 32

fg_lines, excel_rm = ec.parse_workbook(PATH)
resolve = ec._product_index(ec.load_code_map())

plan = Plan.objects.filter(remarks='batchmult compare', status=PlanStatus.DRAFT).first()
if plan is None:
    from planning.services import lifecycle
    plan = lifecycle.create_plan(
        plan_date='2026-09-02', location_id=LOCATION_ID, remarks='batchmult compare',
    )
PlanLine.objects.filter(plan=plan).delete()
for i, line in enumerate(fg_lines, start=1):
    product, how = resolve(line['code'], line['name'])
    if product is None:
        print('UNMAPPED FG', line['code'])
        continue
    print(f'FG {line["code"]} -> {product.id} {product.name} qty={line["cases"]} ({how})')
    PlanLine.objects.create(
        plan=plan, product=product, quantity=line['cases'], unit_id=product.unit_id,
        source=PlanLineSource.MANUAL, sort_order=i,
    )

data = explode_batchmult.run_batchmult_plan(plan.id)
sys_by_id = {}
for row in data['ingredients']:
    kg = row['kg']
    sys_by_id[row['product_id']] = {
        'kg': Decimal(kg) if kg is not None else None,
        'unit': row['unit_name'],
        'name': row['product_name'],
        'qty': row['quantity'],
    }

print(f'\n{"code":16} {"name":42} {"excel kg":>11} {"sys kg":>11} {"diff":>10} {"%":>8}')
seen = set()
for row in excel_rm:
    product, how = resolve(row['code'], row['name'])
    s = sys_by_id.get(product.id) if product else None
    if product:
        seen.add(product.id)
    sys_kg = s['kg'] if s else None
    diff = pct = None
    if sys_kg is not None:
        diff = sys_kg - row['kg']
        pct = (diff / row['kg'] * 100) if row['kg'] else None
    print(
        f'{row["code"]:16} {row["name"][:42]:42} {row["kg"]:>11.3f} '
        f'{(f"{sys_kg:.3f}" if sys_kg is not None else "-"):>11} '
        f'{(f"{diff:.3f}" if diff is not None else "-"):>10} '
        f'{(f"{pct:.1f}" if pct is not None else "-"):>8}'
    )

extra = [(pid, s) for pid, s in sys_by_id.items() if pid not in seen]
if extra:
    print('\nIn system, not in Excel:')
    for pid, s in extra:
        print(f'  {pid:6} {s["name"][:50]:50} {s["qty"]} {s["unit"]}')
