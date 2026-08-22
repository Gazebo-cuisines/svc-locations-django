"""Seed resource_product_rate from CONTENT CODES Master File (units/min by headcount).

Usage:
  python manage.py seed_resource_product_rates --dry-run
  python manage.py seed_resource_product_rates
  python manage.py seed_resource_product_rates --resource "Belt - 1"
"""

from decimal import Decimal, InvalidOperation
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from planning.models import Resource, ResourceProductRate
from product.models import Product

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'

DEFAULT_XLSX = (
    Path(__file__).resolve().parents[2]
    / 'legacy-document'
    / 'CONTENT CODES 22.10.2015.xlsx'
)

# Excel Q–U = Number Of Person 5…1
STAFF_COLS = {'Q': 5, 'R': 4, 'S': 3, 'T': 2, 'U': 1}

DEFAULT_RESOURCE = 'Belt - 1'
RESOURCE_BY_SUBCATEGORY = (
    ('chicken', 'Belt - 2'),
    ('lamb', 'Belt - 2'),
    ('bhaji', 'Forming'),
    ('pakora', 'Forming'),
    ('onion', 'Forming'),
)


def _cell_text(si):
    texts = [t.text or '' for t in si.findall(f'.//{{{NS}}}t')]
    return ''.join(texts)


def _col_row(ref):
    col = ''.join(ch for ch in ref if ch.isalpha())
    row = int(''.join(ch for ch in ref if ch.isdigit()))
    return col, row


def read_master_file(path):
    with zipfile.ZipFile(path) as z:
        ss = ET.fromstring(z.read('xl/sharedStrings.xml'))
        strings = [_cell_text(si) for si in ss]
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        rid_to_file = {r.attrib['Id']: r.attrib['Target'] for r in rels}
        sheet_file = None
        for sh in wb.findall(f'{{{NS}}}sheets/{{{NS}}}sheet'):
            if sh.attrib['name'] == 'Master File':
                sheet_file = 'xl/' + rid_to_file[sh.attrib[REL]]
                break
        if not sheet_file:
            raise CommandError('Sheet Master File not found')
        ws = ET.fromstring(z.read(sheet_file))
        rows = {}
        for row in ws.findall(f'.//{{{NS}}}row'):
            cells = {}
            for c in row:
                ref = c.attrib.get('r')
                if not ref:
                    continue
                col, ridx = _col_row(ref)
                v = c.find(f'{{{NS}}}v')
                if v is None or v.text is None:
                    continue
                if c.attrib.get('t') == 's':
                    cells[col] = strings[int(v.text)]
                else:
                    cells[col] = v.text
            if cells:
                rows[int(row.attrib['r'])] = cells
        return rows


def _dec(val):
    if val is None or val == '':
        return None
    try:
        d = Decimal(str(val))
    except InvalidOperation:
        return None
    return d if d > 0 else None


def resource_for_subcategory(sub, override):
    if override:
        return override
    text = (sub or '').lower()
    for needle, code in RESOURCE_BY_SUBCATEGORY:
        if needle in text:
            return code
    return DEFAULT_RESOURCE


def find_product(code, by_recipe, by_gff, by_alt):
    if code in by_recipe:
        return by_recipe[code]
    if code in by_gff:
        return by_gff[code]
    if code in by_alt:
        return by_alt[code]
    return None


class Command(BaseCommand):
    help = 'Seed ResourceProductRate from CONTENT CODES Master File'

    def add_arguments(self, parser):
        parser.add_argument('--xlsx', default=str(DEFAULT_XLSX))
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--resource',
            default='',
            help='Force all rows onto this resource.code (skip subcategory map)',
        )

    def handle(self, *args, **options):
        path = Path(options['xlsx'])
        if not path.exists():
            raise CommandError(f'File not found: {path}')

        override_code = (options['resource'] or '').strip() or None
        resources = {r.code: r for r in Resource.objects.filter(is_active=True)}
        if override_code and override_code not in resources:
            raise CommandError(f'Unknown resource.code: {override_code}')
        for code in {DEFAULT_RESOURCE, 'Belt - 2', 'Forming'}:
            if code not in resources and not override_code:
                self.stderr.write(self.style.WARNING(f'Missing resource {code}'))

        products = list(Product.objects.all())
        by_recipe = {p.recipe_code: p for p in products if p.recipe_code}
        by_gff = {p.gff_code: p for p in products if p.gff_code}
        by_alt = {
            p.alternate_recipe_code: p
            for p in products
            if p.alternate_recipe_code
        }

        rows = read_master_file(path)
        created = updated = skipped_status = skipped_speed = skipped_product = 0
        skipped_resource = 0
        misses = []
        to_write = []

        for ridx, cells in rows.items():
            if ridx < 3:
                continue
            sku = str(cells.get('J') or '').strip()
            status = str(cells.get('N') or '').strip()
            if not sku or sku == 'Code':
                continue
            if status != 'Live':
                skipped_status += 1
                continue
            speeds = {
                staff: _dec(cells.get(col))
                for col, staff in STAFF_COLS.items()
            }
            if not any(speeds.values()):
                skipped_speed += 1
                continue
            product = find_product(sku, by_recipe, by_gff, by_alt)
            if product is None:
                skipped_product += 1
                misses.append(sku)
                continue
            res_code = resource_for_subcategory(cells.get('F'), override_code)
            resource = resources.get(res_code)
            if resource is None:
                skipped_resource += 1
                misses.append(f'{sku} (no resource {res_code})')
                continue
            batch = _dec(cells.get('O'))
            for staff, upm in speeds.items():
                if upm is None:
                    continue
                to_write.append((resource, product, staff, upm, batch))

        self.stdout.write(
            f'Live+speed rows to upsert: {len(to_write)}  '
            f'skip status={skipped_status} no-speed={skipped_speed} '
            f'no-product={skipped_product} no-resource={skipped_resource}'
        )
        if misses:
            shown = misses[:40]
            self.stdout.write('Unmatched SKUs: ' + ', '.join(shown))
            if len(misses) > 40:
                self.stdout.write(f'  … {len(misses) - 40} more')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('dry-run: no writes'))
            return

        with transaction.atomic():
            for resource, product, staff, upm, batch in to_write:
                _, was_created = ResourceProductRate.objects.update_or_create(
                    resource=resource,
                    product=product,
                    staff_count=staff,
                    defaults={
                        'units_per_minute': upm,
                        'batch_size': batch,
                        'is_active': True,
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(f'created={created} updated={updated}'))
