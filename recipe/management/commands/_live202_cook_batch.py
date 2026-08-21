"""One-shot live-202 cooking wave helpers (imported by shell runner)."""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

DUMP = Path('docs/Recipe-import-task/source-of-truth/Dump20260819 - Pedro.sql')
LR = Path('docs/Recipe-import-task/harvi/1 LR/Signed copy-Print from here')
LIVE = Path('docs/Recipe-import-task/live-202-product.md')

# Gazebo categories
CAT_COOK = 160
CAT_SAUCE = 161
CAT_RICE = 162
CAT_WRAP = 163
CAT_SAMOSA = 164
CAT_NOODLE = 166

SRC_COOK, DST_HR, DST_MIX = 82, 4, 84

NAME_ALIASES = {
    'rapeseed oil': 'INGRAD-01',
    'onion sliced': 'VEGCHI-01',
    'sliced onion': 'VEGCHI-01',
    'garlic puree': 'VEGFRO-11',
    'iqf garlic puree': 'VEGFRO-11',
    'iqf garlic pure': 'VEGFRO-11',
    'green chilli puree': 'VEGFRO-27',
    'green chillies puree': 'VEGFRO-27',
    'red chilli puree': 'VEGFRO-28',
    'coarse red chilli puree': 'VEGFRO-28',
    'iqf red chilli puree': 'VEGFRO-28',
    'ginger puree': 'VEGFRO-15',
    'onion puree': 'SAUCE0-07',
    'tomato paste': 'SAUCE0-03',
    'water': 'AFFINITY-S1',
    'water (1)': 'AFFINITY-S1',
    'water-1': 'AFFINITY-S1',
    'water (2)': 'AFFINITY-S2',
    'water for starch': 'AFFINITY-S1',
    'water (for starch)': 'AFFINITY-S1',
    'dice red pepper 20mm': 'VEGCHI-13',
    'red pepper 20mm': 'VEGCHI-13',
    'dice onion 25mm': 'VEGCHI-10',
    'diced onion 25 mm': 'VEGCHI-10',
    'diced onion 25mm': 'VEGCHI-10',
    'fresh chopped coriander': 'VEGCHI-09',
    'coriander chopped (fresh)': 'VEGCHI-09',
    'coriander (frozen)': 'VEGFRO-10',
    'chopped coriander (frozen)': 'VEGFRO-10',
    'single cream (milk)': 'DAIRY0-02',
    'single cream (milK)': 'DAIRY0-02',
    'yoghurt (milk)': 'DAIRY0-04',
    'yogurt (milk)': 'DAIRY0-04',
    'yoghurt': 'DAIRY0-04',
    'lemon juice': 'SAUCE0-02',
    'lemon juice nfc (vegan)': 'SAUCE0-02',
    'corn flour': 'SPICE0-07',
    'cornflour': 'SPICE0-07',
    'butter (milk)': 'DAIRY0-01',
    'unsalted butter (milk)': 'DAIRY0-01',
    'butter unsalted (milk)': 'DAIRY0-01',
}


def get_cols(table: str) -> list[str]:
    cols, cap = [], False
    with DUMP.open(errors='ignore') as f:
        for line in f:
            if f'CREATE TABLE `{table}`' in line:
                cap = True
                continue
            if cap:
                if line.startswith(')'):
                    break
                m = re.match(r'\s+`(\w+)`', line)
                if m:
                    cols.append(m.group(1))
    return cols


def split_values(s: str) -> list[list[str]]:
    rows, i, n = [], 0, len(s)
    while i < n:
        if s[i] != '(':
            i += 1
            continue
        i += 1
        vals, cur, in_str, esc = [], [], False, False
        while i < n:
            c = s[i]
            if in_str:
                cur.append(c)
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == "'":
                    if i + 1 < n and s[i + 1] == "'":
                        cur.append("'")
                        i += 1
                    else:
                        in_str = False
                i += 1
                continue
            if c == "'":
                in_str = True
                cur.append(c)
                i += 1
                continue
            if c == ',':
                vals.append(''.join(cur).strip())
                cur = []
                i += 1
                continue
            if c == ')':
                vals.append(''.join(cur).strip())
                rows.append(vals)
                i += 1
                break
            cur.append(c)
            i += 1
    return rows


def unquote(v: str):
    if v == 'NULL':
        return None
    if v.startswith("'") and v.endswith("'"):
        return v[1:-1]
    return v


def load_pedro():
    prod_cols = get_cols('tblproducts')
    tree_cols = get_cols('tblproducttree')
    cont_cols = get_cols('tblContainers')
    idx, cont, tree = {}, {}, defaultdict(list)
    with DUMP.open(errors='ignore') as f:
        for line in f:
            if line.startswith('INSERT INTO `tblContainers`'):
                for r in split_values(line[line.index('VALUES') + 6:]):
                    d = {cont_cols[i]: unquote(r[i]) for i in range(min(len(cont_cols), len(r)))}
                    cont[int(d['id'])] = d['container']
            if line.startswith('INSERT INTO `tblproducts`'):
                for r in split_values(line[line.index('VALUES') + 6:]):
                    d = {prod_cols[i]: unquote(r[i]) for i in range(min(len(prod_cols), len(r)))}
                    idx[int(d['id'])] = d
            if line.startswith('INSERT INTO `tblproducttree`'):
                for r in split_values(line[line.index('VALUES') + 6:]):
                    d = {tree_cols[i]: unquote(r[i]) for i in range(min(len(tree_cols), len(r)))}
                    tree[int(d['parentprod'])].append(d)
    return idx, cont, tree


def live_fgs():
    fgs = []
    for line in LIVE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) < 4:
            continue
        code = parts[0].strip()
        pedro = None
        for p in parts[1:6]:
            if re.fullmatch(r'\d{2,5}', p.strip()):
                pedro = int(p.strip())
                break
        if code and pedro:
            fgs.append((code, pedro))
    return fgs


def walk(tree, root):
    seen, stack = set(), [root]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for d in tree.get(pid, []):
            stack.append(int(d['item']))
    return seen


def is_cook(info):
    name = info.get('productname') or ''
    recipe = info.get('productreceipecode') or ''
    if re.search(r'(?i)(\-S\b| - S\b)', recipe) or re.search(r'(?i) - Spice', name):
        return False
    if re.search(r'(?i)(\-St\b| - St\b)', recipe) or re.search(r'(?i)Steaming', name):
        return False
    if re.search(r'(?i)(\-Ma\b| - Ma\b)', recipe) or re.search(r'(?i)Marination', name):
        return False
    if re.search(r'(?i)(\-Mx\b| - Mx\b)', recipe) or re.search(r'(?i) - Mixer\b', name):
        return False
    if re.search(r'(?i)(\-C\b| - C\b| - Co\b)', recipe):
        return True
    if re.search(r'(?i)\bCooking\b', name):
        return True
    return False


def pedro_code(info) -> str:
    r = info.get('productreceipecode') or ''
    if re.match(r'\d{4}-\d{2}-\d{2}', r):
        s = info.get('alternateproductreceipecode') or ''
        return s if s and not re.match(r'\d{4}-', s) else ''
    return r


def norm_code(c: str) -> str:
    c = (c or '').strip()
    c = re.sub(r'\s*-\s*', '-', c)
    c = re.sub(r'\s+', '', c)
    return c


def cook_category(name: str) -> int:
    n = (name or '').lower()
    if 'noodle' in n:
        return CAT_NOODLE
    if 'rice' in n:
        return CAT_RICE
    if any(x in n for x in ('samosa', 'filling', 'mushroom', 'mix -', 'mix of')):
        return CAT_SAMOSA
    if 'wrap' in n:
        return CAT_WRAP
    if any(x in n for x in ('sauce', 'gravy', 'dip', 'curry', 'potato', 'paneer', 'bhuna', 'dopiaza')):
        return CAT_SAUCE
    return CAT_COOK


def find_pdf(info):
    code = pedro_code(info)
    stem_m = re.search(
        r'(GFF[\d\-]+R?)',
        code + ' ' + (info.get('gffCode') or '') + ' ' + (info.get('productname') or ''),
        re.I,
    )
    if not stem_m:
        return None
    stem = stem_m.group(1).replace(' ', '')
    stem_base = re.sub(r'(-C|-Co)$', '', stem, flags=re.I)
    cands = []
    for p in LR.glob('*.pdf'):
        if 'Obsolete' in str(p) or p.name.startswith('~$'):
            continue
        fn = p.name.replace(' ', '').upper()
        for s in {stem.upper(), stem_base.upper()}:
            if fn.startswith(s):
                rest = fn[len(s):]
                if re.match(r'-S([^A-Z]|$)', rest):
                    continue
                cands.append(p)
                break
    if not cands:
        return None

    def score(p):
        n = p.name.lower()
        sc = 0
        if 'cook' in n:
            sc += 20
        if 'spice' in n:
            sc -= 50
        m = re.search(r'v\.?\s*(\d+)', n)
        sc += int(m.group(1)) if m else 0
        return sc

    cands.sort(key=score, reverse=True)
    return cands[0]


def parse_cooking_pdf(path: Path):
    text = '\n'.join((p.extract_text() or '') for p in PdfReader(str(path)).pages)
    text = text.replace('\u00a0', ' ')
    tm = re.search(r'\bTotal\s+(\d{3,7})\b', text, re.I)
    total = int(tm.group(1)) if tm else None
    if total is None:
        m = re.search(r'\n\s*(\d{4,7})\s*\n\s*(?:Batch Size|ALLERGEN|Batch No)', text, re.I)
        if m:
            total = int(m.group(1))

    low = text.lower()
    idx = low.rfind('ingredients')
    body = text[idx:] if idx >= 0 else text
    body = re.split(r'(?i)ALLERGEN|Comments?\b|Batch No|Batch Size', body)[0]

    lines = []
    for raw in body.splitlines():
        line = re.sub(r'\s+', ' ', raw).strip()
        if not line:
            continue
        m = re.match(r'^Total\s+(\d{3,7})\s*$', line, re.I)
        if m:
            total = int(m.group(1))
            break
        if re.fullmatch(r'(?:\d+\s+)+\d+', line):
            continue
        if re.search(r'(?i)amount per mix|actual weight|trace no|use by date', line):
            continue
        m = re.match(r'^(.+?)\s+(\d{2,7})\s*$', line)
        if not m:
            continue
        name, g = m.group(1).strip(), int(m.group(2))
        if len(re.findall(r'[A-Za-z]', name)) < 3:
            continue
        if re.fullmatch(r'[\d\s]+', name):
            continue
        lines.append((name, g))
    if lines and total is not None and sum(g for _, g in lines) == total:
        return lines, total, 'lines'

    flat = re.sub(r'\s+', ' ', body)
    flat = re.sub(r'\d+\s*[mM]{2}', 'XXmm', flat)
    flat = re.sub(
        r'(?i)Amount per Mix|Actual Weight|Trace No|Trace Date|Use by Date|Unit Weight|\(g\)|\bDate\b',
        ' ',
        flat,
    )
    flat = re.sub(r'\s+', ' ', flat)
    if re.search(r'(?i)\bTotal\s+\d+', flat):
        flat = re.split(r'(?i)\bTotal\s+\d+', flat)[0]
    flat = re.sub(r'(?i)^.*?form \(GFF[^)]+\)\s*', '', flat)
    flat = re.sub(r'(?i)^.*?Ingredients\s*', '', flat)
    pairs = []
    for m in re.finditer(r'([A-Za-z(][A-Za-z0-9XXmm \-,\(\)/%&\'+.]*?)\s+(\d{2,7})(?=\s|$)', flat):
        name = re.sub(r'\s+', ' ', m.group(1)).strip(' -,')
        g = int(m.group(2))
        if len(re.findall(r'[A-Za-z]', name)) < 3:
            continue
        if re.search(r'(?i)traceability|indicate so|amount per|actual weight', name):
            continue
        if name in {')', 'R)', '(EGG)'}:
            continue
        pairs.append((name, g))
    if pairs and total is not None and sum(g for _, g in pairs) == total:
        return pairs, total, 'flat'
    return pairs, total, 'fail'


def norm_name(s: str) -> str:
    s = (s or '').lower()
    s = s.replace('xxmm', ' ')
    s = re.sub(r'\([^)]*\)', ' ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def spice_child_code(cook_code: str) -> str:
    base = re.sub(r'(?i)(-C|-Co)$', '', norm_code(cook_code))
    return f'{base}-S'
