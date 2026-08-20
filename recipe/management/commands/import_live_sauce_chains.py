"""
Import real recipe ingredients for all sauce/sub-recipe chain stubs
created by import_live_remaining_fg_batch.py.

Each GFF chain has:
  - A spice product  (GFF127R-S)  created fresh if missing
  - A sauce/mix product (GFF127R-Mx) already in DB as empty stub

Usage:
    python manage.py import_live_sauce_chains
    python manage.py import_live_sauce_chains --dry-run   # show what would run, no writes
    python manage.py import_live_sauce_chains --chain GFF127R  # single chain

Sources:
    harvi/1 LR/Signed copy-Print from here/<GFF>R *.pdf
    harvi/2 SPICES/Signed copy SPICE/<GFF>R-S *.pdf
"""
import pymysql
pymysql.install_as_MySQLdb()

from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Category, Product
from recipe.utils import sync_has_recipe

from recipe.management.commands._live202_helpers import (
    U_G, U_UNIT, CAT_SPICE, CAT_SAUCE, CAT_RM,
    LOC, make_product, make_draft, get_or_create_version, add_line,
    activate, require_products,
)

BASE_DIR = Path(__file__).resolve().parents[4]
LR_DIR = BASE_DIR / 'docs/Recipe-import-task/harvi/1 LR/Signed copy-Print from here'
SP_DIR = BASE_DIR / 'docs/Recipe-import-task/harvi/2 SPICES/Signed copy SPICE'

# ── Ingredient name → ERP recipe_code  ────────────────────────────────────────
# Maps lowercase partial ingredient name to (recipe_code, note_if_approx)
RM_MAP = {
    # VEG FROZEN
    'garlic puree':                ('VEGFRO-11', ''),
    'ginger puree':                ('VEGFRO-15', ''),
    'green chilli puree':          ('VEGFRO-27', ''),
    'green chillies puree':        ('VEGFRO-27', ''),
    'red chilli puree':            ('VEGFRO-28', ''),
    'coarse red chilli puree':     ('VEGFRO-28', ''),
    'spinach leaves':              ('VEGFRO-12', ''),
    'cauliflower 15/30':           ('VEGFRO-14', ''),
    'cauliflower florets':         ('VEGFRO-14', ''),
    'baby corncobs cut':           ('VEGFRO-19', ''),
    'water chestnut':              ('VEGFRO-09', ''),
    'frozen coriander':            ('VEGFRO-10', ''),
    'coriander (frozen)':          ('VEGFRO-10', ''),
    'coriander chopped (frozen)':  ('VEGFRO-10', ''),
    'coriander (iqf)':             ('VEGFRO-10', ''),
    'coriander iqf':               ('VEGFRO-10', ''),
    'spring onion chopped 4mm':    ('VEGFRO-06', ''),
    'chopped spring onion 4mm':    ('VEGFRO-06', ''),
    'spring onion chopped 4mm (frozen)': ('VEGFRO-06', ''),
    'basil large (frozen)':        ('VEGFRO-41', ''),
    'frozen basil':                ('VEGFRO-41', ''),
    'kaffir lime leaves':          ('VEGFRO-43', 'Lime leaves milled frozen - closest match'),
    'mint (frozen)':               ('VEGFRO-48', ''),
    'galangal puree':              ('VEGFRO-38', ''),
    'red pepper diced 10mm (frozen)': ('VEGFRO-05', ''),
    'red pepper diced 20mm (frozen)': ('VEGFRO-13', ''),
    'soya beans (frozen)':         ('VEGFRO-25', ''),
    'green pepper diced 10mm (frozen)': ('VEGFRO-23', ''),
    'broad beans':                 ('VEGFRO-29', ''),
    'pineapple diced':             ('VEGFRO-30', 'Using frozen pineapple tidbits'),
    'onion diced 10mm (frozen)':   ('VEGFRO-02', ''),
    'onion white diced 10mm':      ('VEGFRO-02', ''),
    'curry leaves (frozen)':       ('VEGFRO-44', ''),
    # VEG FRESH / CHILLED
    'sliced onion':                ('VEGCHI-01', ''),
    'onion sliced':                ('VEGCHI-01', ''),
    'diced onion 25 mm':           ('VEGCHI-10', ''),
    'diced onion 25mm':            ('VEGCHI-10', ''),
    'dice onion 25mm':             ('VEGCHI-10', ''),
    'dice onion 25 mm':            ('VEGCHI-10', ''),
    'onion chopped 2-6mm':         ('VEGCHI-05', ''),
    'onion chopped 2-6mm (fresh)': ('VEGCHI-05', ''),
    'coriander chopped (fresh)':   ('VEGCHI-09', ''),
    'fresh chopped coriander':     ('VEGCHI-09', ''),
    'chopped coriander (fresh)':   ('VEGCHI-09', ''),
    'coriander chopped fresh':     ('VEGCHI-09', ''),
    'red pepper diced 20mm (fresh)': ('VEGCHI-13', ''),
    'red pepper 20mm diced (fresh)': ('VEGCHI-13', ''),
    'red pepper 20mm':             ('VEGCHI-13', 'Assumed fresh diced 20mm'),
    'potato diced 20mm':           ('VEGCHI-14', ''),
    'potato diced 20mm (fresh)':   ('VEGCHI-14', ''),
    'steamed diced potato 20mm':   ('VEGCHI-14', 'Raw 20mm potato - steam in process'),
    'potato diced 20mm (steamed)': ('VEGCHI-14', 'Raw 20mm potato - steam in process'),
    'sweet potato grated':         ('VEGCHI-16', ''),
    'grated sweet potato 3mm':     ('VEGCHI-16', ''),
    'fresh spring onion 10mm':     ('VEGCHI-18', ''),
    'spring onion chopped 10mm':   ('VEGCHI-18', ''),
    'fresh chopped spring onion':  ('VEGCHI-18', ''),
    'fresh chopped spring onion 10mm': ('VEGCHI-18', ''),
    'carrot baton 30mm (fresh)':   ('VEGCHI-19', ''),
    'carrot baton 30mm':           ('VEGCHI-19', ''),
    'green beans baton 30mm (fresh)': ('VEGCHI-23', ''),
    'green beans baton 30mm':      ('VEGCHI-23', ''),
    'green beans 10mm':            ('VEGCHI-26', ''),
    'green beans fresh 10mm':      ('VEGCHI-26', ''),
    'green pepper diced 20mm (fresh)': ('VEGCHI-24', ''),
    'carrot shredded 3 x 3 x 20mm': ('VEGCHI-04', ''),
    'carrot shredded':             ('VEGCHI-04', ''),
    'parsley curly iqf':           ('VEGFRO-35', 'IQF parsley'),
    'onion cp cut':                ('VEGCHI-48', ''),
    'onion cp cut (fresh)':        ('VEGCHI-48', ''),
    'red pepper cp cut':           ('VEGCHI-49', ''),
    'red pepper cp cut (fresh)':   ('VEGCHI-49', ''),
    'green pepper cp cut':         ('VEGCHI-50', ''),
    'green pepper cp cut (fresh)': ('VEGCHI-50', ''),
    'dice onion':                  ('VEGCHI-05', 'Assumed chopped 2-6mm'),
    # SAUCE
    'onion puree':                 ('SAUCE0-07', ''),
    'onion rtu 10mm':              ('SAUCE0-15', ''),
    'onions rtu 10mm':             ('SAUCE0-15', ''),
    'crushed tomato':              ('SAUCE0-01', ''),
    'crushed tomatoes':            ('SAUCE0-01', ''),
    'crushed tomatoes (blended)':  ('SAUCE0-01', ''),
    'crushed tomato (blended)':    ('SAUCE0-01', ''),
    'crushed tomatoes (blended) (see crushed tomatoes)': ('SAUCE0-01', ''),
    'tomato paste':                ('SAUCE0-03', ''),
    'lemon juice':                 ('SAUCE0-02', ''),
    'malt vinegar (barley)':       ('SAUCE0-12', ''),
    'malt vinegar':                ('SAUCE0-12', ''),
    'barley malt vinegar':         ('SAUCE0-12', ''),
    'mango puree':                 ('SAUCE0-16', ''),
    'mango pulp':                  ('SAUCE0-16', ''),
    'honey':                       ('SAUCE0-18', ''),
    'rice vinegar':                ('SAUCE0-26', ''),
    'tamarind concentrate':        ('SAUCE0-24', ''),
    'tamarind paste':              ('SAUCE0-24', ''),
    # DAIRY
    'coconut milk':                ('DAIRY0-01', ''),
    'coconuts milk':               ('DAIRY0-01', ''),
    'coconut milk aroy':           ('DAIRY0-01', ''),
    'coconut milk (aroy)':         ('DAIRY0-01', ''),
    'coconuts milk - (aroy-d)':    ('DAIRY0-01', ''),
    'coconut milk (aroy-d)':       ('DAIRY0-01', ''),
    'single cream (milk)':         ('DAIRY0-02', ''),
    'yoghurt (milk)':              ('DAIRY0-04', ''),
    'yogurt (milk)':               ('DAIRY0-04', ''),
    'butter (milk)':               ('DAIRY0-06', ''),
    'butter unsalted (milk)':      ('DAIRY0-06', ''),
    'paneer 15mm (milk)':          ('DAIRY0-09', 'Paneer diced 16mm - closest'),
    'paneer diced 16mm (milk)':    ('DAIRY0-09', ''),
    # INGRAD
    'rapeseed oil':                ('INGRAD-01', ''),
    'basmati rice':                ('INGRAD-07', ''),
    'jasmine thai rice':           ('INGRAD-12', ''),
    'jasmine rice':                ('INGRAD-12', ''),
    'easy cook rice':              ('INGRAD-07', 'Using basmati - confirm if correct'),
    'bamboo shoot shredded':       ('INGRAD-14', ''),
    'bamboo shoots':               ('INGRAD-14', ''),
    'corn flour':                  ('INGRAD-17', ''),
    'meritena 100 (starch)':       ('INGRAD-17', 'Meritena 100 = corn flour'),
    'rice flour':                  ('INGRAD-20', ''),
    'gram flour':                  ('INGRAD-02', ''),
    'sesame seed oil':             ('INGRAD-27', ''),
    'sesame oil (sesame)':         ('INGRAD-27', ''),
    'sesame oil':                  ('INGRAD-27', ''),
    'soaked chickpeas':            ('INGRAD-24', 'Dry chickpeas - soak in process'),
    'broad beans (defrosted)':     ('VEGFRO-29', ''),
    'soya sauce (kikkoman)':       ('SAUCE0-10', ''),
    'caramel colour e150a':        ('INGRAD-69', ''),
    # PROTEIN
    'british chicken breast diced 10-14g rta': ('PROTEIN-14', ''),
    'british diced chicken breast 18-23g':     ('PROTEIN-10', ''),
    '10-12mm halal cooked diced chicken':      ('PROTEIN-18', 'Cooked diced chicken - closest match 20-25mm'),
    'british halal minced lamb 80vl':          ('PROTEIN-19', 'BH lamb mince 80vl - using 90vl closest'),
    # SPICE
    'salt':                   ('SPICE0-02', ''),
    'sugar':                  ('SPICE0-03', ''),
    'light brown sugar':      ('SPICE0-12', ''),
    'brown sugar':            ('SPICE0-12', ''),
    'jaggery brown':          ('SPICE0-17', ''),
    'cumin seed':             ('SPICE0-04', ''),
    'cumin seeds':            ('SPICE0-04', ''),
    'cumin powder':           ('SPICE0-05', ''),
    'garam masala':           ('SPICE0-06', ''),
    'roasted garam masala':   ('SPICE0-57', ''),
    'chilli powder':          ('SPICE0-08', ''),
    'red chilli powder':      ('SPICE0-08', ''),
    'coriander powder':       ('SPICE0-09', ''),
    'turmeric powder':        ('SPICE0-11', ''),
    'turmeric':               ('SPICE0-11', ''),
    'potato flake':           ('SPICE0-14', ''),
    'whole potato flakes':    ('SPICE0-14', ''),
    'paprika powder ht':      ('SPICE0-15', ''),
    'paprika powder':         ('SPICE0-15', ''),
    'paprika ht':             ('SPICE0-15', ''),
    'paprika':                ('SPICE0-15', ''),
    'tandoori masala':        ('SPICE0-18', ''),
    'tandoori masala seasoning': ('SPICE0-18', ''),
    'tikka seasoning':        ('SPICE0-01', ''),
    'ajwain seed':            ('SPICE0-13', ''),
    'black pepper coarse ht': ('SPICE0-24', ''),
    'black pepper coarse':    ('SPICE0-24', ''),
    'citric acid':            ('SPICE0-25', ''),
    'green cardamom powder':  ('SPICE0-27', ''),
    'cardamom powder':        ('SPICE0-27', ''),
    'cardamom green powder':  ('SPICE0-27', ''),
    'fenugreek kibbled':      ('SPICE0-28', ''),
    'fenugreek leaves kibbled': ('SPICE0-28', ''),
    'kibbled fenugreek leaves': ('SPICE0-28', ''),
    'cinnamon powder':        ('SPICE0-32', ''),
    'mustard seed':           ('SPICE0-33', ''),
    'white pepper':           ('SPICE0-35', ''),
    'fennel seed':            ('SPICE0-38', ''),
    'fennel seeds':           ('SPICE0-38', ''),
    'bay leaf':               ('SPICE0-40', ''),
    'fennel powder':          ('SPICE0-41', ''),
    'clove powder':           ('SPICE0-46', ''),
    'mace powder':            ('SPICE0-53', ''),
    'crushed chillies':       ('SPICE0-54', ''),
    'madras curry powder':    ('SPICE0-23', ''),
    'almond powder (nut)':    ('INGRAD-23', ''),
    'cashewnut puree (nut)':  (None, 'ANOMALY: Cashew puree - no ERP code. Nearest: INGRAD-15 cashew nuts broken.'),
    'cashew nut puree (nut)': (None, 'ANOMALY: Cashew puree - no ERP code. Nearest: INGRAD-15 cashew nuts broken.'),
    'potato starch':          ('SPICE0-07', ''),
    'lemon grass':            ('VEGFRO-33', ''),
    'lemon grass puree':      ('VEGFRO-33', ''),
    'lemon grass milled':     ('VEGFRO-33', ''),
    'lemon grass milled (frozen)': ('VEGFRO-33', ''),
    'galangal milled':        ('VEGFRO-38', ''),
    'paprika oleoresin':      (None, 'ANOMALY: Paprika oleoresin - no ERP code. Check with QA.'),
    'paprika extract':        (None, 'ANOMALY: Paprika extract/oleoresin - no ERP code.'),
    'fish sauce':             (None, 'ANOMALY: Fish sauce - no ERP code. Check with QA.'),
    'squid fish sauce':       (None, 'ANOMALY: Fish sauce - no ERP code.'),
    'vegetable bullion':      ('SPICE0-50', ''),
    'green curry paste':      (None, 'ANOMALY: Green curry paste (Mai Siam) - no ERP code. Check with QA.'),
    'panang curry paste':     ('SAUCE0-25', 'Thai Panang paste Mae Ploy - using SAUCE0-25 Mae Ploy'),
    'thai red curry paste':   ('SAUCE0-23', ''),
    'concentrated pineapple juice': (None, 'ANOMALY: Concentrated pineapple juice - no ERP code.'),
}


def _lookup(name: str) -> tuple[str | None, str]:
    """Find ERP recipe_code for an ingredient name. Returns (code_or_None, note)."""
    key = name.lower().strip()
    # Exact match
    if key in RM_MAP:
        return RM_MAP[key]
    # Partial match — try progressively shorter prefixes
    for map_key, val in RM_MAP.items():
        if map_key in key or key in map_key:
            return val
    return None, f'ANOMALY: No ERP code found for "{name}"'


# ── Chain definitions  (spice_code, sauce_code, batch_g, location_key) ────────
# Each chain: (spice_product_code, mix_product_code, approx_batch_g)
CHAINS = {
    'GFF127R': {
        'sp_code': 'GFF127R-S', 'sp_name': 'Madras Sauce - Spices',
        'mx_code': 'GFF127R-Mx', 'batch': 92190,
        'sp_ingredients': [
            ('Tamarind Paste', 720),
            ('Red Chilli Powder', 540),
            ('Salt', 540),
            ('Madras Curry Powder', 360),
            ('Malt Vinegar (Barley)', 360),
            ('Coriander Powder', 360),
            ('Sugar', 360),
            ('Paprika Powder', 300),
            ('Cumin Powder', 180),
            ('Turmeric Powder', 150),
            ('Garam Masala', 60),
        ],
        'mx_ingredients': [
            ('Onion Puree', 21600),
            ('Crushed Tomato', 20160),
            ('Diced Onion 25 MM', 7200),
            ('Coconut Milk Aroy', 4680),
            ('Sliced Onion', 3960),
            ('Rapeseed Oil', 2880),
            ('Tomato Paste', 2160),
            ('Garlic Puree', 900),
            ('Green Chilli Puree', 840),
            ('Ginger Puree', 840),
            ('Frozen Coriander', 540),
            ('Corn Flour', 360),
            # Water (21600) + Water for Corn Flour (540) skipped
        ],
    },
    'GFF128R': {
        'sp_code': 'GFF128R-S', 'sp_name': 'Bombay Potato - Spices',
        'mx_code': 'GFF128R-Mx', 'batch': 61183,
        'sp_ingredients': [
            ('Salt', 442),
            ('Sugar', 340),
            ('Coriander Powder', 221),
            ('Paprika Powder HT', 221),
            ('Chilli Powder', 170),
            ('Mustard Seed', 119),
            ('Cumin Seed', 102),
            ('Cumin Powder', 102),
            ('Turmeric Powder', 68),
        ],
        'mx_ingredients': [
            ('Potato Diced 20mm (Steamed)', 27200),
            ('Onion Puree', 10200),
            ('Crushed Tomatoes', 6120),
            ('Onion RTU 10mm', 4080),
            ('Tomato Paste', 2040),
            ('Rapeseed Oil', 1700),
            ('Coriander Chopped (Fresh)', 340),
            ('Garlic Puree', 221),
            ('Ginger Puree', 221),
            # Water (6120) skipped
        ],
    },
    'GFF129R': {
        'sp_code': 'GFF129R-S', 'sp_name': 'Jalfrezi Sauce - Spices',
        'mx_code': 'GFF129R-Mx', 'batch': 71812,
        'sp_ingredients': [
            ('Chilli Powder', 352),
            ('Salt', 416),
            ('Coriander Powder', 208),
            ('Cumin Powder', 144),
            ('Paprika Powder', 352),
            ('Turmeric Powder', 64),
            # 80g unnamed (keep separated) - anomaly skip
            # 1408g unnamed (keep separated) - anomaly skip
        ],
        'mx_ingredients': [
            ('Rapeseed Oil', 4224),
            ('Onion Sliced', 4224),
            ('Onion Puree', 15488),
            ('Tomato Paste', 10976),
            ('Diced Onion 25mm', 9152),
            ('Garlic Puree', 1200),
            ('Ginger Puree', 1696),
            ('Fresh Chopped Coriander', 848),
            ('Green Chilli Puree', 416),
            ('Coarse Red Chilli Puree', 288),
            # Water (17600) skipped
        ],
    },
    'GFF130R': {
        'sp_code': 'GFF130R-S', 'sp_name': 'Saag Aloo Gobi - Spices',
        'mx_code': 'GFF130R-Mx', 'batch': 59360,
        'sp_ingredients': [
            ('Salt', 320),
            ('Mustard Seed', 120),
            ('Cumin Seed', 120),
            ('Sugar', 120),
            ('Cumin Powder', 120),
            ('Fenugreek Leaves Kibbled', 120),
            ('Turmeric Powder', 40),
            ('Chilli Powder', 40),
        ],
        'mx_ingredients': [
            ('Spinach Leaves', 14000),
            ('Steamed Diced Potato 20mm', 12000),
            ('Cauliflower 15/30 (Frozen)', 10000),
            ('Crushed Tomato (in bag)', 8800),
            ('Onion RTU 10mm', 6000),
            ('Coconuts Milk', 3200),
            ('Butter (Milk)', 1600),
            ('Ginger Puree', 800),
            ('Garlic Puree', 600),
            ('Chopped Coriander (Fresh)', 600),
            ('Green Chilli Puree', 120),
            # Water (600) skipped
        ],
    },
    'GFF131R': {
        'sp_code': 'GFF131R-S', 'sp_name': 'Dopiazza Sauce - Spices',
        'mx_code': 'GFF131R-Mx', 'batch': 56685,
        'sp_ingredients': [
            ('Garam Masala', 90),
            ('Cumin Seed', 90),
            ('Coriander Powder', 90),
            ('Chilli Powder', 90),
            ('Paprika Powder', 90),
            ('Cumin Powder', 120),
            ('Malt Vinegar (Barley)', 870),
            ('Salt', 435),
            ('Turmeric Powder', 135),
            ('Sugar', 660),
        ],
        'mx_ingredients': [
            ('Rapeseed Oil', 4350),
            ('Sliced Onion', 4350),
            ('Dice Onion 25MM', 8700),
            ('Onion Puree', 13050),
            ('Crushed Tomato', 6525),
            ('Corn Flour', 435),
            ('Fresh Chopped Coriander', 135),
            ('Garlic Puree', 570),
            ('Ginger Puree', 570),
            ('Green Chilli Puree', 45),
            # Water (13050 + 870) skipped
        ],
    },
    'GFF132R': {
        'sp_code': 'GFF132R-S', 'sp_name': 'Mango Masala - Spices',
        'mx_code': 'GFF132R-Mx', 'batch': 49215,
        'sp_ingredients': [
            ('Malt Vinegar (Barley)', 600),
            ('Salt', 330),
            ('Kibbled Fenugreek Leaves', 90),
            ('Red Chilli Powder', 90),
            # 90g unnamed keep separated - skip
        ],
        'mx_ingredients': [
            ('Onion Puree', 16050),
            ('Crushed Tomato', 12000),
            ('Mango Puree', 7950),
            ('Single Cream (Milk)', 6000),
            ('Tomato Paste', 1950),
            ('Rapeseed Oil', 900),
            ('Butter (Milk)', 900),
            ('Garlic Puree', 600),
            ('Ginger Puree', 600),
            ('Corn Flour', 450),
            ('Fresh Chopped Coriander', 450),
            ('Green Chilli Puree', 210),
            # Water (900) skipped
        ],
    },
    'GFF133R': {
        'sp_code': 'GFF133R-S', 'sp_name': 'Korma Sauce - Spices',
        'mx_code': 'GFF133R-Mx', 'batch': 129714,
        'sp_ingredients': [
            ('Chilli Powder', 136),
            ('Coriander Powder', 612),
            ('Cumin Powder', 612),
            ('Garam Masala', 136),
            ('Paprika Powder', 204),
            ('Salt', 680),
            ('Almond Powder (Nut)', 2448),
            ('Turmeric Powder', 170),
            ('Green Cardamom Powder', 136),
            ('Mace Powder', 68),
            ('Sugar', 3672),
        ],
        'mx_ingredients': [
            ('Onion Puree', 36720),
            ('Coconut Milk', 20808),
            ('Yoghurt (Milk)', 14688),
            ('Cashew Nut Puree (Nut)', 7344),  # anomaly - no catalogue code
            ('Single Cream (Milk)', 12240),
            ('Crushed Tomato', 9792),
            ('Ginger Puree', 1088),
            ('Rapeseed Oil', 1462),
            ('Garlic Puree', 748),
            ('Fresh Chopped Coriander', 306),
            # Water (12240) skipped
        ],
    },
    'GFF134R': {
        'sp_code': 'GFF134R-S', 'sp_name': 'Butter Sauce - Spices',
        'mx_code': 'GFF134R-Mx', 'batch': 136475,
        'sp_ingredients': [
            ('Jaggery Brown', 2280),
            ('Malt Vinegar (Barley)', 1710),
            ('Paprika Powder', 570),
            ('Tandoori Masala Seasoning', 570),
            ('Red Chilli Powder', 120),
            ('Fenugreek Leaves Kibbled', 120),
            ('Salt', 105),
        ],
        'mx_ingredients': [
            ('Crushed Tomato', 76380),
            ('Single Cream (Milk)', 19380),
            ('Onion Puree', 11400),
            ('Butter (Milk)', 5700),
            ('Tomato Paste', 5700),
            ('Garlic Puree', 2850),
            ('Ginger Puree', 2850),
            ('Rapeseed Oil', 1140),
            ('Green Chilli Puree', 150),
            # Water (5700) skipped
        ],
    },
    'GFF139R': {
        'sp_code': 'GFF139R-S', 'sp_name': 'Saag Masala - Spices',
        'mx_code': 'GFF139R-Mx', 'batch': 42960,
        'sp_ingredients': [
            ('Sugar', 450),
            ('Salt', 270),
            ('Paprika Powder HT', 150),
            ('Coriander Powder', 90),
            ('Cumin Powder', 90),
            ('Cumin Seed', 90),
            ('Fennel Seed', 30),
        ],
        'mx_ingredients': [
            ('Onion Puree', 9000),
            ('Spinach Leaves', 9000),
            ('Crushed Tomato', 9000),
            ('Onion RTU 10mm', 3600),
            ('Single Cream (Milk)', 3600),
            ('Yoghurt (Milk)', 1500),
            ('Tomato Paste', 1500),
            ('Chopped Coriander (Fresh)', 600),
            ('Ginger Puree', 300),
            ('Garlic Puree', 300),
            ('Green Chilli Puree', 150),
            ('Potato Starch', 150),
            ('Lemon Juice', 90),
            # Water (1500) skipped
        ],
    },
    'GFF141R': {
        'sp_code': 'GFF141R-S', 'sp_name': 'Tikka Masala Sauce - Spices',
        'mx_code': 'GFF141R-Mx', 'batch': 119688,
        'sp_ingredients': [
            ('Chilli Powder', 182),
            ('Coriander Powder', 260),
            ('Cumin Powder', 260),
            ('Garam Masala', 234),
            ('Paprika Powder', 234),
            ('Salt', 676),
            ('Tandoori Masala', 676),
            ('Turmeric Powder', 234),
            ('Sugar', 936),
            # 2262g unnamed keep separated - skip
        ],
        'mx_ingredients': [
            ('Onion Puree', 30160),
            ('Single Cream (Milk)', 42978),
            ('Tomato Paste', 13000),
            ('Yoghurt (Milk)', 11700),
            ('Cashewnut Puree (Nut)', 3588),  # anomaly
            ('Garlic Puree', 1768),
            ('Ginger Puree', 1768),
            ('Honey', 1898),
            ('Fresh Chopped Coriander', 1144),
            ('Butter Unsalted (Milk)', 1326),
            ('Rapeseed Oil', 3406),
        ],
    },
    'GFF142R': {
        'sp_code': 'GFF142R-S', 'sp_name': 'Chicken Tikka Marination - Spices',
        'mx_code': 'GFF142R-Mx', 'batch': 123400,
        'sp_ingredients': [
            ('Tikka Seasoning', 4000),
            ('Garam Masala', 300),
            ('Salt', 600),
            # Paprika Extract 150 - anomaly
        ],
        'mx_ingredients': [
            ('British Chicken Breast Diced 10-14g RTA', 100000),
            ('Single Cream (Milk)', 8000),
            ('Yoghurt (Milk)', 4000),
            ('Potato Starch', 1200),
            ('Garlic Puree', 1200),
            ('Ginger Puree', 1200),
            ('Lemon Juice', 2000),
            ('Frozen Coriander', 700),
        ],
    },
    'GFF146R': {
        'sp_code': 'GFF146R-S', 'sp_name': 'Sweet and Sour Sauce - Spices',
        'mx_code': 'GFF146R-Mx', 'batch': 23880,
        'sp_ingredients': [
            ('Sugar', 2765),
            ('Rice Vinegar', 2600),
            ('Salt', 115),
            ('Malt Vinegar (Barley)', 115),
            # Concentrated Pineapple Juice 650 - anomaly
            # Paprika Extract 35 - anomaly
        ],
        'mx_ingredients': [
            ('Pineapple Diced', 2600),
            ('Red Pepper 20mm', 3900),
            ('Tomato Paste', 650),
            ('Corn Flour', 650),
            ('Ginger Puree', 325),
            ('Onion Puree', 80),
            ('Garlic Puree', 15),
            # Water (8125 + 1140) skipped
        ],
    },
    'GFF148R': {
        'sp_code': 'GFF148R-S', 'sp_name': 'Thai Green Curry Sauce - Spices',
        'mx_code': 'GFF148R-Mx', 'batch': 54800,
        'sp_ingredients': [
            ('Sugar', 1600),
            ('Malt Vinegar (Barley)', 1600),
            ('Lemon Grass', 1400),
            ('Galangal Milled', 400),
            ('Sesame Oil', 400),
            ('Salt', 240),
            ('Kaffir Lime Leaves', 120),
            # Green Curry Paste (Mai Siam) 1200 - anomaly (no ERP code)
            # Fish Sauce 320 - anomaly
        ],
        'mx_ingredients': [
            ('Coconut Milk (Aroy)', 12000),
            ('Water Chestnuts', 3200),
            ('Baby Corncobs Cut (Frozen)', 3200),
            ('Onion RTU 10mm', 2400),
            ('Fresh Chopped Coriander', 2000),
            ('Rapeseed Oil', 1200),
            ('Corn Flour', 600),
            ('Chopped Spring Onion 4mm', 400),
            ('Garlic Puree', 400),
            ('Ginger Puree', 400),
            ('Basil Large (Frozen)', 120),
            # Water (20000 + 1400) skipped
        ],
    },
    'GFF153R': {
        'sp_code': 'GFF153R-S', 'sp_name': 'Egg Fried Rice - Spices',
        'mx_code': 'GFF153R-Mx', 'batch': 16516,
        'sp_ingredients': [
            ('Sesame Oil (Sesame)', 391),
            ('Salt', 30),
        ],
        'mx_ingredients': [
            ('Easy Cook Rice', 4375),
            ('Ginger Puree', 391),
            ('Fresh Spring Onion 10MM', 391),
            ('Dice Onion', 391),
            # GFF154R cooked egg sub-component (4 units) - note only, complex sub-recipe
            # Water (10938) skipped
        ],
    },
    'GFF162R': {
        'sp_code': 'GFF162R-S', 'sp_name': 'Jasmine Rice - Spices',
        'mx_code': 'GFF162R-Mx', 'batch': 126210,
        'sp_ingredients': [
            ('Lemon Grass Puree', 660),
            ('Salt', 390),
            ('Sesame Oil (Sesame)', 180),
        ],
        'mx_ingredients': [
            ('Jasmine Thai Rice', 34560),
            ('Rapeseed Oil', 2580),
            ('Chopped Spring Onion 4mm', 1440),
            # Water (86400) skipped; Spice qty referenced via sp product
        ],
    },
    'GFF199R': {
        'sp_code': 'GFF199R-S', 'sp_name': 'Panang Curry Sauce - Spices',
        'mx_code': 'GFF199R-Mx', 'batch': 63840,
        'sp_ingredients': [
            ('Panang Curry Paste', 4000),
            ('Light Brown Sugar', 4000),
            ('Malt Vinegar (Barley)', 2400),
            ('Vegetable Bullion', 160),
            # Squid Fish Sauce 960 - anomaly
            # Wheat/Soya allergen unnamed 1200g - skip
        ],
        'mx_ingredients': [
            ('Coconuts Milk - (AROY-D)', 32000),
            ('Fresh Chopped Spring Onion 10mm', 2800),
            ('Corn Flour', 1200),
            ('Fresh Chopped Coriander', 1200),
            ('Frozen Basil', 400),
            ('Kaffir Lime Leaves', 240),
            # Water (26000) skipped
        ],
    },
    'GFF201R': {
        'sp_code': 'GFF201R-S', 'sp_name': 'Goan Curry Sauce - Spices',
        'mx_code': 'GFF201R-Mx', 'batch': 16660,
        'sp_ingredients': [
            ('Brown Sugar', 250),
            ('Salt', 80),
            ('Cumin Powder', 60),
            ('Garam Masala', 50),
            ('Coriander Powder', 50),
            ('Turmeric Powder', 30),
            ('Red Chilli Powder', 30),
            ('Bay Leaf', 10),
            ('Green Cardamom Powder', 10),
        ],
        'mx_ingredients': [
            ('Onion Puree', 4300),
            ('Coconuts Milk', 4200),
            ('Crushed Tomato (Blended)', 4000),
            ('Rapeseed Oil', 500),
            ('Red Pepper Diced 10mm (Frozen)', 400),
            ('Green Beans Fresh 10mm', 400),
            ('Barley Malt Vinegar', 250),
            ('Ginger Puree', 200),
            ('Garlic Puree', 200),
            ('Green Chilli Puree', 200),
            ('Corn Flour', 200),
            ('Lemon Juice', 120),
            ('Fresh Chopped Coriander', 120),
            # Water (1000) skipped
        ],
    },
    'GFF206R': {
        'sp_code': 'GFF206R-S', 'sp_name': 'Tandoori Paneer Marination - Spices',
        'mx_code': 'GFF206R-Mx', 'batch': 5925,
        'sp_ingredients': [
            ('Tandoori Masala Seasoning', 251),
        ],
        'mx_ingredients': [
            ('Paneer 15mm (Milk)', 5000),
            ('Single Cream (Milk)', 200),
            ('Yogurt (Milk)', 200),
            ('Lemon Juice', 102),
            ('Potato Starch', 102),
            ('Coriander IQF', 19),
            ('Rapeseed Oil', 51),
        ],
    },
    'GFF210R': {
        'sp_code': 'GFF210R-S', 'sp_name': 'Red Thai Veg Curry - Spices',
        'mx_code': 'GFF210R-Mx', 'batch': 41130,
        'sp_ingredients': [
            ('Thai Red Curry Paste', 1500),
            ('Brown Sugar', 1200),
            ('Lemon Grass', 450),
            ('Galangal Puree', 180),
            ('Salt', 150),
            ('Paprika Powder HT', 60),
            ('Kaffir Lime Leaves', 60),
        ],
        'mx_ingredients': [
            ('Coconut Milk (Aroy-D)', 9000),
            ('Soya Beans (Frozen) (SOYA)', 4500),
            ('Red Pepper 20mm Diced (Fresh)', 3000),
            ('Bamboo Shoot Shredded', 1800),
            ('Baby Corncobs (IQF)', 1800),
            ('Water Chestnut Sliced', 1500),
            ('Tomato Paste', 1200),
            ('Corn Flour', 450),
            ('Rapeseed Oil', 300),
            ('Onions RTU 10mm', 270),
            ('Ginger Puree', 180),
            ('Coriander Chopped (Fresh)', 150),
            # Water (10800) skipped
            # Green Pepper 20mm (Fresh) 1800 - need to check
        ],
    },
    'GFF297R': {
        'sp_code': 'GFF297R-S', 'sp_name': 'Sweet Potato Falafel - Spices',
        'mx_code': 'GFF297R-Mx', 'batch': 12386,
        'sp_ingredients': [
            ('Rice Flour', 360),
            ('Potato Flake', 300),
            ('Cumin Powder', 90),
            ('Coriander Powder', 60),
            ('Salt', 50),
            ('Paprika HT', 48),
            ('Baking Powder', 30),
            ('White Pepper', 18),
            ('Ginger Powder', 18),
            ('Cinnamon Powder', 12),
            # Dried Apricot Chopped 300 - check code
        ],
        'mx_ingredients': [
            ('Grated Sweet Potato 3mm', 2400),
            ('Onion Chopped 2-6mm (Fresh)', 2100),
            ('Soaked Chickpeas', 1800),
            ('Broad Beans (Defrosted)', 1500),
            ('Gram Flour', 480),
            ('Carrot Shredded 3 x 3 x 20mm', 420),
            ('Lemon Juice', 390),
            ('Garlic Puree', 240),
            ('Tomato Paste', 180),
            ('Parsley Curly IQF', 150),
            ('Coriander IQF', 150),
            ('Red Chilli Puree', 30),
        ],
    },
    'GFF321R': {
        'sp_code': 'GFF321R-S', 'sp_name': 'Nawabi Curry - Spices',
        'mx_code': 'GFF321R-Mx', 'batch': 127500,
        'sp_ingredients': [
            ('Sugar', 2000),
            ('Salt', 600),
            ('Chilli Powder', 400),
            ('Paprika', 400),
            ('Cardamom Powder', 400),
            ('Turmeric', 200),
            ('Coriander Powder', 200),
            ('Fennel Powder', 100),
            ('Cinnamon Powder', 100),
            ('Clove Powder', 100),
        ],
        'mx_ingredients': [
            ('Onion Puree', 40000),
            ('Tomato Paste', 15000),
            ('Single Cream (Milk)', 15000),
            ('Onion RTU 10mm', 8000),
            ('Garlic Puree', 5000),
            ('Red Chilli Puree', 2500),
            ('Barley Malt Vinegar', 2000),
            ('Meritena 100 (Starch)', 500),
            # Cashew Puree 9000g (water + cashew dissolved) - no ERP code, logged anomaly
        ],
    },
    'GFF328R': {
        'sp_code': 'GFF328R-S', 'sp_name': 'Chicken Tikka Pakora - Spices',
        'mx_code': 'GFF328R-Mx', 'batch': 27190,
        'sp_ingredients': [
            ('Tikka Seasoning', 200),
            ('Salt', 170),
            ('Paprika HT', 100),
            ('Turmeric', 40),
            ('Black Pepper Coarse', 40),
            # Paprika Oleoresin 100 - anomaly
            # Coriander Seed Crushed 40 - check
        ],
        'mx_ingredients': [
            ('British Diced Chicken Breast 18-23g', 20000),
            ('Gram Flour', 1800),
            ('Rice Flour', 800),
            ('Lemon Juice', 600),
            ('Onion Puree', 400),
            ('Ginger Puree', 300),
            ('Garlic Puree', 300),
            ('Red Chilli Puree', 300),
            ('Coriander Chopped (Fresh)', 200),
            # Water (2600) skipped
        ],
    },
    'GFF333R': {
        'sp_code': 'GFF333R-S', 'sp_name': 'Balti Sauce - Spices',
        'mx_code': 'GFF333R-Mx', 'batch': 137850,
        'sp_ingredients': [
            ('Mustard Seed', 300),
            ('Fennel Seed', 200),
            ('Ajwain Seed', 100),
            ('Sugar', 2000),
            ('Salt', 900),
            ('Cumin Powder', 500),
            ('Paprika', 500),
        ],
        'mx_ingredients': [
            ('Crushed Tomatoes (blended)', 50000),
            ('Onion Puree', 40000),
            ('Single Cream (Milk)', 5000),
            ('Tomato Paste', 4000),
            ('Rapeseed Oil', 3000),
            ('Yogurt (Milk)', 3000),
            ('Garlic Puree', 2000),
            ('Coriander Chopped (Fresh)', 2000),
            ('Ginger Puree', 1500),
            ('Lemon Juice', 1000),
            ('Green Chilli Puree', 500),
            # Water (20000) skipped
        ],
    },
    'GFF335R': {
        'sp_code': None,  # No GFF335R-S spice sub-recipe PDF available
        'sp_name': None,
        'mx_code': 'GFF335R-Mx', 'batch': 116130,
        'sp_ingredients': [],
        'mx_ingredients': [
            ('Yogurt (Milk)', 16800),
            ('Crushed Tomatoes (Blended)', 15000),
            ('Tomato Paste', 12000),
            ('Single Cream (Milk)', 10200),
            ('Onion Puree', 9000),
            ('Butter Unsalted (Milk)', 1800),
            ('Rapeseed Oil', 1200),
            ('Garlic Puree', 1200),
            ('Ginger Puree', 900),
            # "Spices" 6030g — no GFF335R-S found; will log as anomaly
            ('Spices (no GFF335R-S PDF)', 6030),
            # Water (42000) skipped
        ],
    },
    'GFF244R': {
        'sp_code': None,  # No spice PDF
        'sp_name': None,
        'mx_code': 'GFF244R-Mx', 'batch': 22960,
        'sp_ingredients': [],
        'mx_ingredients': [
            ('Crushed Tomatoes', 5160),
            ('Onion Puree', 4800),
            ('Carrot Baton 30mm (Fresh)', 2400),
            ('Red Pepper Diced 20mm (Fresh)', 2400),
            ('Green Beans Baton 30mm (Fresh)', 1680),
            ('Rapeseed Oil', 360),
            ('Garlic Puree', 240),
            ('Lemon Juice', 120),
            ('Green Chillies Puree', 60),
            # Water (2400) skipped; 1200g "Defrosted" item unidentified
        ],
    },
    'GFF448R': {
        'sp_code': 'GFF448R-S', 'sp_name': 'Fragrant Rice - Spices',
        'mx_code': 'GFF448R-Mx', 'batch': 126210,
        'sp_ingredients': [
            ('Lemon Grass Milled', 660),
            ('Salt', 390),
            ('Sesame Oil', 180),
        ],
        'mx_ingredients': [
            ('Basmati Rice', 34560),
            ('Rapeseed Oil', 2580),
            ('Spring Onion Chopped 4mm (frozen)', 1440),
            # Water (86400) skipped
        ],
    },
    'GFF471R': {
        'sp_code': 'GFF471R-S', 'sp_name': 'Aromatic Rice - Spices',
        'mx_code': 'GFF471R-Mx', 'batch': 141345,
        'sp_ingredients': [
            ('Tandoori Masala Seasoning', 900),
            ('Cumin Seeds', 450),
            ('Salt', 450),
            ('Sugar', 450),
            ('Fennel Powder', 90),
            ('Turmeric', 90),
            ('Citric Acid', 90),
            ('Cardamom Powder', 45),
            ('Clove Powder', 45),
            ('Black Pepper Coarse HT', 45),
            ('Mace Powder', 45),
        ],
        'mx_ingredients': [
            ('Basmati Rice', 27000),
            ('Onion Diced 10mm (frozen)', 9000),
            ('Rapeseed Oil', 2250),
            ('Garlic Puree', 2250),
            ('Red Chilli Puree', 2250),
            ('Ginger Puree', 2250),
            ('Coriander (frozen)', 900),
            ('Mint (frozen)', 900),
            # Water (90000) skipped
        ],
    },
    'GFF474R': {
        'sp_code': 'GFF474R-S', 'sp_name': 'Stir Fried Noodles - Spices',
        'mx_code': 'GFF474R-Mx', 'batch': 57050,
        'sp_ingredients': [
            ('Soya Sauce (Kikkoman) (Soya, Wheat)', 1000),
            # Paprika Oleoresin 50 - anomaly
        ],
        'mx_ingredients': [
            # GFF476R Steamed Noodles - sub-recipe reference
            ('Rapeseed Oil', 1500),
            ('Garlic Puree', 1000),
            ('Red Chilli Puree', 1000),
            ('Ginger Puree', 1000),
            ('Rice Vinegar', 1000),
            ('Coriander (frozen)', 500),
            # GFF476R-Mx noodles 50000g - handled below with special logic
        ],
        'mx_extra_sub_recipes': [('GFF476R-Mx', 50000)],
    },
    'GFF475R': {
        'sp_code': 'GFF475R-S', 'sp_name': 'Stir Fried Chicken - Spices',
        'mx_code': 'GFF475R-Mx', 'batch': 62250,
        'sp_ingredients': [
            ('Soya Sauce (Kikkoman) (Soya, Wheat)', 2500),
            ('Caramel Colour E150a', 1000),
            ('Crushed Chillies', 250),
        ],
        'mx_ingredients': [
            ('10-12mm Halal Cooked Diced Chicken', 30000),
            ('Green Pepper Diced 10mm (frozen)', 7500),
            ('Red Pepper Diced 10mm (frozen)', 7500),
            ('Onion Diced 10mm (frozen)', 7500),
            ('Spring Onion Chopped 4mm (frozen)', 5000),
        ],
    },
    'GFF487R': {
        'sp_code': 'GFF487R-S', 'sp_name': 'Chat Patta Bombay Potato - Spices',
        'mx_code': 'GFF487R-Mx', 'batch': 134580,
        'sp_ingredients': [
            ('Salt', 920),
            ('Sugar', 460),
            ('Mustard Seed', 460),
            ('Curry Leaves (frozen)', 460),
            ('Cumin Seed', 460),
            ('Paprika', 460),
        ],
        'mx_ingredients': [
            ('Potato Diced 20mm (fresh)', 69000),
            ('Onion Puree', 18400),
            ('Tomatoes Crushed', 13800),
            ('Rapeseed Oil', 2300),
            ('Ginger Puree', 2300),
            ('Garlic Puree', 2300),
            ('Coriander (frozen)', 1840),
            ('Red Chilli Puree', 920),
            ('Barley Malt Vinegar', 460),
            # Water (13800 + 920) skipped
        ],
    },
    'GFF489R': {
        'sp_code': 'GFF489R-S', 'sp_name': 'Paneer Jalfrezi - Spices',
        'mx_code': 'GFF489R-Mx', 'batch': 121575,
        'sp_ingredients': [
            ('Salt', 900),
            ('Sugar', 675),
            ('Paprika', 450),
            ('Cumin Powder', 360),
            ('Coriander Powder', 360),
            ('Cumin Seed', 225),
            # 225g unnamed keep separated
            # 135g unnamed
            # 90g unnamed
            # 45g unnamed
        ],
        'mx_ingredients': [
            ('Paneer Diced 16mm (Milk)', 20000),
            ('Onion Puree', 11250),
            ('Tomatoes Crushed', 11250),
            ('Onion CP cut (fresh)', 9000),
            ('Red Pepper CP cut (fresh)', 6750),
            ('Green Pepper CP cut (fresh)', 6750),
            ('Green Beans Cut (frozen)', 6750),
            ('Baby Corncobs Cut (frozen)', 6750),   # reuse baby corncobs
            ('Carrots Half Baton 30mm (fresh)', 6750),
            ('Potato Diced 20mm (fresh)', 6750),
            ('Cauliflower florets (frozen)', 6750),
            ('Tomato Paste', 3600),
            ('Coriander (frozen)', 1800),
            ('Red Chilli Puree', 1350),
            ('Meritena 100 (Starch)', 450),
            # Water (18000) skipped
        ],
    },
    'GFF492R': {
        'sp_code': 'GFF492R-S', 'sp_name': 'Lamb Kofta - Spices',
        'mx_code': 'GFF492R-Mx', 'batch': 131250,
        'sp_ingredients': [
            ('Whole Potato Flakes', 2250),
            ('Salt', 750),
            ('Coriander Powder', 375),
            ('Cumin Powder', 375),
            ('Paprika', 375),
            ('Roasted Garam Masala', 375),
            ('Chilli Powder', 225),
        ],
        'mx_ingredients': [
            ('British Halal Minced Lamb 80vl', 75000),
            ('Onion Chopped 2-6mm (fresh)', 37500),
            ('Ginger Puree', 4500),
            ('Red Chilli Puree', 3000),
            ('Garlic Puree', 3000),
            ('Coriander (frozen)', 3000),
        ],
    },
}


class Command(BaseCommand):
    help = 'Populate recipe ingredients for all sauce/sub-recipe chain stubs from GFF PDFs.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Print plan without writing to DB.')
        parser.add_argument('--chain', type=str, default=None,
                            help='Process single chain only, e.g. GFF127R')

    def handle(self, *args, **options):
        dry = options['dry_run']
        target = options.get('chain')
        anomalies = []

        chains_to_run = {k: v for k, v in CHAINS.items()
                         if target is None or k == target}
        if not chains_to_run:
            self.stderr.write(f'Chain {target!r} not found.')
            return

        self.stdout.write(
            f'{"[DRY RUN] " if dry else ""}Processing {len(chains_to_run)} chains...'
        )

        with transaction.atomic():
            for gff, chain in chains_to_run.items():
                self.stdout.write(f'\n── {gff} ──────────────────────────')
                self._process_chain(gff, chain, dry, anomalies)

        self._print_anomalies(anomalies)
        self.stdout.write(self.style.SUCCESS(
            f'\nDone. Anomalies: {len(anomalies)}'
        ))

    def _process_chain(self, gff, chain, dry, anomalies):
        ug = U_G()
        sp_code = chain.get('sp_code')
        mx_code = chain['mx_code']
        batch = chain['batch']

        # ── 1. Spice product ─────────────────────────────────────────────────
        sp_product = None
        if sp_code:
            if not dry:
                sp_product, sp_created = make_product(
                    recipe_code=sp_code,
                    name=chain['sp_name'],
                    category=CAT_SPICE(),
                    unit=ug,
                    src_loc=LOC('spice'),
                    dst_loc=LOC('lwr'),
                    gff_code=f'{gff}-S',
                    is_sales=False,
                    remarks=f'Spice sub-recipe for {gff}. Sourced from PDF.',
                )
                action = 'NEW' if sp_created else 'exist'
                self.stdout.write(f'  {action} spice {sp_code} id={sp_product.id}')

                sp_ingr = self._build_lines(
                    sp_code, chain['sp_ingredients'], dry, anomalies, batch
                )
                if sp_ingr:
                    sp_v = get_or_create_version(sp_product, None, LOC('spice'),
                                                 f'Spice recipe for {gff}.')
                    for ln, (prod, qty) in enumerate(sp_ingr, 1):
                        add_line(sp_v, ln, prod, qty, ug)
                    activate(sp_v)
                    self.stdout.write(
                        f'  spice recipe: {len(sp_ingr)} lines → ACTIVE'
                    )
            else:
                sp_lines = chain['sp_ingredients']
                self.stdout.write(
                    f'  [DRY] spice {sp_code}: {len(sp_lines)} ingredients'
                )
        else:
            self.stdout.write(f'  (no spice sub-recipe for {gff})')

        # ── 2. Sauce/mix product ─────────────────────────────────────────────
        if not dry:
            mx = Product.objects.filter(recipe_code=mx_code).first()
            if not mx:
                self.stderr.write(
                    f'  SKIP {mx_code} — product not found in DB.'
                )
                anomalies.append(
                    (gff, mx_code, 'Product not found — run import_live_remaining_fg_batch first')
                )
                return
            self.stdout.write(f'  exist sauce {mx_code} id={mx.id}')

            mx_ingr = self._build_lines(
                mx_code, chain['mx_ingredients'], dry, anomalies, batch
            )
            # Append spice sub-recipe as final line
            if sp_product and chain['sp_ingredients']:
                sp_qty = sum(q for _, q in chain['sp_ingredients'])
                mx_ingr.append((sp_product, sp_qty))

            # Handle extra sub-recipe references
            for sub_code, sub_qty in chain.get('mx_extra_sub_recipes', []):
                sub_prod = Product.objects.filter(recipe_code=sub_code).first()
                if sub_prod:
                    mx_ingr.append((sub_prod, sub_qty))
                else:
                    anomalies.append(
                        (gff, sub_code, f'Sub-recipe {sub_code} not found')
                    )

            if mx_ingr:
                mx_v = get_or_create_version(mx, batch, LOC('lwr'),
                                             f'{gff} sauce recipe from PDF.')
                for ln, (prod, qty) in enumerate(mx_ingr, 1):
                    add_line(mx_v, ln, prod, qty, ug)
                activate(mx_v)
                self.stdout.write(
                    f'  sauce recipe: {len(mx_ingr)} lines → ACTIVE'
                )
        else:
            mx_lines = chain['mx_ingredients']
            self.stdout.write(
                f'  [DRY] sauce {mx_code}: {len(mx_lines)} ingredients'
            )

    def _build_lines(self, chain_code, ingredients, dry, anomalies, batch):
        """Map ingredient names to Product objects. Returns [(Product, qty)]."""
        result = []
        for name, qty in ingredients:
            code, note = _lookup(name)
            if code is None:
                anomalies.append((chain_code, name, note or 'No ERP code'))
                self.stdout.write(
                    self.style.WARNING(f'    ANOMALY {name!r}: {note}')
                )
                continue
            if dry:
                result.append((code, qty))  # placeholder for dry-run
                continue
            prod = Product.objects.filter(recipe_code=code).first()
            if prod is None:
                anomalies.append((chain_code, name, f'{code} not in DB'))
                self.stdout.write(
                    self.style.WARNING(
                        f'    MISSING {name!r} → {code} (not in DB)'
                    )
                )
                continue
            result.append((prod, qty))
        return result

    def _print_anomalies(self, anomalies):
        if not anomalies:
            self.stdout.write('\nNo anomalies.')
            return
        self.stdout.write(
            self.style.WARNING(f'\n══ ANOMALY REPORT ({len(anomalies)}) ══')
        )
        for chain, item, note in anomalies:
            self.stdout.write(f'  [{chain}] {item!r}: {note}')
