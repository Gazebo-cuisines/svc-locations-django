"""Register all remaining live-202 FG products as empty drafts.

This command creates stub FG and sub-recipe products for all remaining
202 products not yet imported. All recipes are left empty (draft).
Notes indicate which PDFs to use for each sub-recipe.

Sub-recipe stubs created:
  GFF127R-Mx (Madras Sauce), GFF128R-Mx (Bombay Potato), GFF129R-Mx (Jalfrezi Sauce),
  GFF130R-Mx (Saag Aloo Gobi), GFF131R-Mx (Dopiazza Sauce), GFF132R-Mx (Mango Masala),
  GFF133R-Mx (Korma Sauce), GFF134R-Mx (Butter Sauce), GFF139R-Mx (Saag Masala Sauce),
  GFF141R-Mx (Tikka Masala Sauce), GFF142R-Mx (Chicken Tikka), GFF146R-Mx (Sweet & Sour Sauce),
  GFF148R-Mx (Thai Green Curry Sauce), GFF162R-Mx (Jasmine Rice), GFF153R-Mx (Egg Fried Rice),
  GFF448R-Mx (Fragrant Rice), GFF199R-Mx (Panang Sauce), GFF201R-Mx (Goan Curry Sauce),
  GFF244R-Mx (Veg Balti), GFF210R-Mx (Red Thai Veg Curry), GFF282R-Mx (Duck Spring Roll),
  GFF321R-Mx (Nawabi Lamb Kofta), GFF335R-Mx (Dum Gravy Kashmiri).

  python manage.py import_live_remaining_fg_batch
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Product, ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    DOCS, U_UNIT, U_G, LOC,
    make_product, make_draft, add_line, clear_lines, require_products,
)

CAT_SAUCE = 164
CAT_PACKED = 76
CAT_FG = 127

CHILLED = True
FROZEN = False


def _make_stub(code, name, cat, gff, note):
    p, c = make_product(code, name, cat, U_G, LOC['steam'], LOC['hr'], gff, False, note)
    make_draft(p, note)
    sync_has_recipe(p.id)
    return p, c


def _fg(stock_code, name, pack_g, items, chilled, note, lines, needed):
    fg, created = make_product(
        stock_code, name, CAT_FG, U_UNIT, LOC['sleeve'], LOC['dispatch'], None, chilled, note,
    )
    ProductPackaging.objects.update_or_create(
        product_id=fg.id,
        defaults={'items_per_unit': Decimal(str(items)),
                  'pack_weight': Decimal(str(pack_g))},
    )
    ProductShelfLife.objects.get_or_create(
        product_id=fg.id, defaults={'shelf_life_days': 14 if chilled else 365},
    )
    flags, _ = ProductFlags.objects.get_or_create(product_id=fg.id)
    if not flags.is_sales_item:
        flags.is_sales_item = True; flags.has_plan = True
        flags.save(update_fields=['is_sales_item', 'has_plan'])
    v = make_draft(fg, note)
    clear_lines(v)
    for i, (child, qty, unit_id) in enumerate(lines, start=1):
        add_line(v, i, child, qty, unit_id)
    sync_has_recipe(fg.id)
    return fg, created


class Command(BaseCommand):
    help = 'Register remaining 202 FG products as empty drafts.'

    def handle(self, *args, **options):
        results = []

        with transaction.atomic():
            needed = require_products('OC001', 'OC002', 'OC003', 'OC007', 'OC008', 'OC0016', 'OC0010')

            # ── Sauce / Sub-recipe stubs ───────────────────────────────────────
            stubs = {
                'GFF127R-Mx': ('GFF127R-Mx', 'Madras Sauce - 127 - Mix', CAT_SAUCE, 'GFF127R',
                               'Source PDF: GFF127R - MADRAS SAUCE V.3.pdf. Spice PDF: GFF127R-S. Please import from PDF.'),
                'GFF128R-Mx': ('GFF128R-Mx', 'Bombay Potato - 128 - Mix', CAT_SAUCE, 'GFF128R',
                               'Source PDF: GFF128R - BOMBAY POTATO - V.3.pdf. Please import from PDF.'),
                'GFF129R-Mx': ('GFF129R-Mx', 'Jalfrezi Sauce - 129 - Mix', CAT_SAUCE, 'GFF129R',
                               'Source PDF: GFF129R - JALFREZI SAUCE V.3.pdf. Spice PDF: GFF129R-S.'),
                'GFF130R-Mx': ('GFF130R-Mx', 'Saag Aloo Gobi Booths - 130 - Mix', CAT_SAUCE, 'GFF130R',
                               'Source PDF: GFF130R Saag Aloo Gobi Booths - V.3.pdf.'),
                'GFF131R-Mx': ('GFF131R-Mx', 'Dopiazza Sauce - 131 - Mix', CAT_SAUCE, 'GFF131R',
                               'Source PDF: GFF131R Dopiazza Sauce Booths V.3.pdf.'),
                'GFF132R-Mx': ('GFF132R-Mx', 'Mango Masala Sauce - 132 - Mix', CAT_SAUCE, 'GFF132R',
                               'Source PDF: GFF132R Mango Masala Sauce V.4.pdf.'),
                'GFF133R-Mx': ('GFF133R-Mx', 'Korma Sauce - 133 - Mix', CAT_SAUCE, 'GFF133R',
                               'Source PDF: GFF133R Korma Sauce - V.4.pdf.'),
                'GFF134R-Mx': ('GFF134R-Mx', 'Butter Sauce - 134 - Mix', CAT_SAUCE, 'GFF134R',
                               'Source PDF: GFF134R Butter Sauce V.4.pdf.'),
                'GFF139R-Mx': ('GFF139R-Mx', 'Saag Masala Sauce - 139 - Mix', CAT_SAUCE, 'GFF139R',
                               'Source PDF: GFF139R Saag Masala Sauce - v.4.pdf.'),
                'GFF141R-Mx': ('GFF141R-Mx', 'Tikka Masala Sauce - 141 - Mix', CAT_SAUCE, 'GFF141R',
                               'Source PDF: GFF141R Tikka Masala Sauce V.3.pdf.'),
                'GFF142R-Mx': ('GFF142R-Mx', 'Chicken Tikka - 142 - Marination', CAT_SAUCE, 'GFF142R',
                               'Source PDF: GFF142R Chicken Tikka v6.pdf.'),
                'GFF146R-Mx': ('GFF146R-Mx', 'Sweet And Sour Sauce - 146 - Mix', CAT_SAUCE, 'GFF146R',
                               'Source PDF: GFF146R - Sweet and Sour Sauce, v.4.pdf.'),
                'GFF148R-Mx': ('GFF148R-Mx', 'Thai Green Curry Sauce - 148 - Mix', CAT_SAUCE, 'GFF148R',
                               'Source PDF: GFF148R Thai Green Curry Sauce - v.3.pdf.'),
                'GFF162R-Mx': ('GFF162R-Mx', 'Jasmine Rice - 162 - Cook', CAT_SAUCE, 'GFF162R',
                               'Source PDF: GFF162R Jasmine Rice V.4.pdf.'),
                'GFF153R-Mx': ('GFF153R-Mx', 'Egg Fried Rice - 153 - Cook', CAT_SAUCE, 'GFF153R',
                               'Source PDF: GFF153R Egg Fried Rice V.3.pdf.'),
                'GFF448R-Mx': ('GFF448R-Mx', 'Fragrant Rice - 448 - Cook', CAT_SAUCE, 'GFF448R',
                               'Source PDF: GFF448R - Fragrant Rice - v.1.pdf.'),
                'GFF471R-Mx': ('GFF471R-Mx', 'Aromatic Rice - 471 - Cook', CAT_SAUCE, 'GFF471R',
                               'Source PDF: GFF471R- Aromatic Rice v.1.pdf.'),
                'GFF199R-Mx': ('GFF199R-Mx', 'Panang Curry Sauce - 199 - Mix', CAT_SAUCE, 'GFF199R',
                               'Source PDF: GFF199R - Panang Curry Sauce V.3.pdf.'),
                'GFF201R-Mx': ('GFF201R-Mx', 'Goan Curry Sauce - 201 - Mix', CAT_SAUCE, 'GFF201R',
                               'Source PDF: GFF201R - Goan Curry Sauce v.4.pdf.'),
                'GFF244R-Mx': ('GFF244R-Mx', 'Vegetable Balti - 244 - Mix', CAT_SAUCE, 'GFF244R',
                               'Source PDF: GFF244R - Vegetable Balti V.4.pdf.'),
                'GFF210R-Mx': ('GFF210R-Mx', 'Red Thai Vegetable Curry - 210 - Mix', CAT_SAUCE, 'GFF210R',
                               'Source PDF: GFF210R - Red Thai Vegetable Curry - V.3.pdf.'),
                'GFF321R-Mx': ('GFF321R-Mx', 'Nawabi Lamb Kofta Curry - 321 - Mix', CAT_SAUCE, 'GFF321R',
                               'Source PDF: GFF321R - Nawabi Curry V.4.pdf.'),
                'GFF335R-Mx': ('GFF335R-Mx', 'Dum Gravy Kashmiri - 335 - Mix', CAT_SAUCE, 'GFF335R',
                               'Source PDF: GFF335R - Dum Gravy Kashmiri V.2.pdf.'),
                'GFF333R-Mx': ('GFF333R-Mx', 'Balti Sauce - 333 - Mix', CAT_SAUCE, 'GFF333R',
                               'Source PDF: GFF333R - Balti Sauce V.2.pdf.'),
                'GFF206R-Mx': ('GFF206R-Mx', 'Tandoori Paneer Marination - 206 - Mix', CAT_SAUCE, 'GFF206R',
                               'Source PDF: GFF206R - Tandoori Paneer Marination V.4.pdf.'),
                'GFF474R-Mx': ('GFF474R-Mx', 'Stir Fried Noodles - 474 - Mix', CAT_SAUCE, 'GFF474R',
                               'Source PDF: GFF474R-Stir Fried Noodles - v.1.pdf.'),
                'GFF475R-Mx': ('GFF475R-Mx', 'Stir Fried Chicken - 475 - Mix', CAT_SAUCE, 'GFF475R',
                               'Source PDF: GFF475R-Stir Fried Chicken - v.1.pdf.'),
                'GFF487R-Mx': ('GFF487R-Mx', 'Chat Patta Bombay Potato - 487 - Mix', CAT_SAUCE, 'GFF487R',
                               'Source PDF: GFF487R - Chat Patta Bombay Potato.pdf.'),
                'GFF489R-Mx': ('GFF489R-Mx', 'Paneer Jalfrezi - 489 - Mix', CAT_SAUCE, 'GFF489R',
                               'Source PDF: GFF489R - Paneer Jalfrezi.pdf.'),
                'GFF492R-Mx': ('GFF492R-Mx', 'Lamb Kofta - 492 - Mix', CAT_SAUCE, 'GFF492R',
                               'Source PDF: GFF492R - Lamb Kofta.pdf.'),
                'GFF297R-Mx': ('GFF297R-Mx', 'Sweet Potato Falafel - 297 - Mix', CAT_SAUCE, 'GFF297R',
                               'Source PDF: GFF297R - Sweet Potato Falafel v.3.pdf.'),
                'GFF328R-Mx': ('GFF328R-Mx', 'Chicken Tikka Pakora - 328 - Mix', CAT_SAUCE, 'GFF328R',
                               'Source PDF: GFF328R - Chicken Tikka Pakora, v.4.pdf.'),
                'GFF152R-Mx': ('GFF152R-Mx', 'Batter Chicken Bites - 152 - Mix', CAT_SAUCE, 'GFF152R',
                               'Source PDF: GFF152R Batter Chicken Bites V.7.pdf.'),
            }

            stub_products = {}
            for key, (code, name, cat, gff, note) in stubs.items():
                p, c = _make_stub(code, name, cat, gff, note)
                stub_products[key] = p
                results.append(f"{'NEW' if c else 'reuse'} {code} id={p.id}")

            # ── Indian Selection stubs ────────────────────────────────────────
            cinsb_note = 'Indian Selection bite (13g). GFF169SD chain. Please build recipe from GFF169SD QAS.'
            cinsb, c = make_product('CINSB', 'GB Indian Selection Bite 13g',
                                    CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], 'GFF169SD', False, cinsb_note)
            make_draft(cinsb, cinsb_note)
            sync_has_recipe(cinsb.id)
            results.append(f"{'NEW' if c else 'reuse'} CINSB id={cinsb.id}")

            cinsc_note = 'Indian Selection bite (large). GFF169SD chain. Please build recipe from GFF169SD QAS.'
            cinsc, c = make_product('CINSC', 'GC Indian Selection Large (39g)',
                                    CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, False, cinsc_note)
            make_draft(cinsc, cinsc_note)
            sync_has_recipe(cinsc.id)
            results.append(f"{'NEW' if c else 'reuse'} CINSC id={cinsc.id}")

            # ── FG products ───────────────────────────────────────────────────
            # Helper aliases
            mdx = stub_products['GFF127R-Mx']
            jlf = stub_products['GFF129R-Mx']
            sag = stub_products['GFF130R-Mx']
            dop = stub_products['GFF131R-Mx']
            mng = stub_products['GFF132R-Mx']
            kor = stub_products['GFF133R-Mx']
            btr = stub_products['GFF134R-Mx']
            sgm = stub_products['GFF139R-Mx']
            tkm = stub_products['GFF141R-Mx']
            ckt = stub_products['GFF142R-Mx']
            sns = stub_products['GFF146R-Mx']
            thg = stub_products['GFF148R-Mx']
            jsr = stub_products['GFF162R-Mx']
            efr = stub_products['GFF153R-Mx']
            frr = stub_products['GFF448R-Mx']
            arr = stub_products['GFF471R-Mx']
            pan = stub_products['GFF199R-Mx']
            goa = stub_products['GFF201R-Mx']
            vbl = stub_products['GFF244R-Mx']
            rth = stub_products['GFF210R-Mx']
            blt = stub_products['GFF333R-Mx']
            tpr = stub_products['GFF206R-Mx']
            noo = stub_products['GFF474R-Mx']
            ckn = stub_products['GFF475R-Mx']
            bpo = stub_products['GFF128R-Mx']
            pjl = stub_products['GFF489R-Mx']
            nwb = stub_products['GFF321R-Mx']
            kfr = stub_products['GFF492R-Mx']
            fal = stub_products['GFF297R-Mx']
            cpk = stub_products['GFF328R-Mx']
            bch = stub_products['GFF152R-Mx']

            fg_list = [
                # ── Novel snack items ────────────────────────────────────────
                ('CBBCC-R1TEB', 'Booths - Bang Bang Cauliflower with Tamarind | 200G x 1', 200, 1, CHILLED,
                 'Booths bang bang cauliflower snack. GFF394R chain. '
                 'Source PDF: GFF394R - Bang Bang Cauliflower V.3. Pedro id 2461.',
                 []),

                ('CCBTB-R1TEB', 'Booths - Breaded Chicken Bites W/BBQ Dip x 8 | 240G x 1', 240, 1, CHILLED,
                 'Booths 8×30g breaded chicken bites + BBQ dip. GFF152R batter chain. Pedro id 2451.',
                 [(bch, 1, U_UNIT)]),

                ('CCGOUB-R1TEB', 'Booths - Gochujang Kebabs With Spicy Korean Inspired Dip x 8 | 240G x 1',
                 240, 1, CHILLED,
                 'Booths gochujang kebabs + dip. Novel product. GFF499R Xmas chain may apply. Pedro id 2448.',
                 []),

                ('CCHKSS-1R4TEB', 'Booths - Crispy Battered Chicken With Sweet & Sour Sauce | 320G X 4', 320, 4, CHILLED,
                 'Booths crispy battered chicken w/sweet & sour sauce ×4. GFF152R batter + GFF146R sauce. Pedro id 438.',
                 [(bch, 1, U_UNIT), (sns, 1, U_UNIT)]),

                ('CCTKEB-R1TEB', 'Booths - Chicken Tikka Kebabs With Mango Dip x 8 | 240G x 1', 240, 1, CHILLED,
                 'Booths 8 chicken tikka kebabs + mango dip. Novel. Pedro id 2447.',
                 []),

                ('CCTPKB-1R6TEB', 'Booths - Chicken Tikka Pakora With Mint Raita Dip | 180G X 6', 180, 6, CHILLED,
                 'Booths 30g chicken tikka pakora ×6 + mint raita dip. GFF328R chain. Pedro id 1251. '
                 'Sleeve S666-04, box OC002. '
                 'Source PDF: GFF328R - Chicken Tikka Pakora, v.4.pdf.',
                 [(cpk, 6, U_UNIT)]),

                ('CCPOB-R6TTA', 'TA - Chicken Popcorn | 280G X 6', 280, 6, CHILLED,
                 'TA 280g chicken popcorn ×6. Novel. Pedro id 3433.',
                 []),

                ('CORSLB2-R1TEB', 'Booths - Oriental Selection | 230G x 1', 230, 1, CHILLED,
                 'Booths oriental selection snack box. Novel composite product. Pedro id 2238.',
                 []),

                ('CPGHB-R1TEB', 'Booths - Garlic & Herb Butterfly King Prawn | 120G x 1', 120, 1, CHILLED,
                 'Booths garlic & herb butterfly king prawn. Novel. Pedro id 2457.',
                 []),

                ('CPPLA-B8TEB', 'Booths - Party Platter | 1655G x 1', 1655, 1, CHILLED,
                 'Booths party platter composite product. Pedro id 2239. '
                 'Components TBC — likely samosas + bhajis + spring rolls.',
                 []),

                ('CPSLB4-R1TEB', 'Booths - King Prawn Selection x 10 | 150G x 1', 150, 1, CHILLED,
                 'Booths 10 king prawn selection. Novel. Pedro id 2273.',
                 []),

                ('CVBCMB-R1TEB', 'Booths - Brie De Meux & Cranberry Parcels x 10 | 200G x 1', 200, 1, CHILLED,
                 'Booths brie & cranberry parcels. GFF173R chain. Pedro id 2276. '
                 'Source PDF: GFF173R Brie De Meaux & Cranberry Baskets – 16g V.2.pdf.',
                 []),

                ('CVCMRP-R6TEB', 'Booths - Mushroom - Ricotta & Spinach Parcels x 2 | 300G x 6', 300, 6, CHILLED,
                 'Booths 2×mushroom ricotta & spinach parcels ×6. GFF222R chain. Pedro id 2317. '
                 'Source PDF: GFF222R - Mushrooms Spinach and Ricotta Cheese Parcel V.4.pdf.',
                 []),

                ('CVLCPJP-R1TEB', 'XMAS - Booths - Lancashire Cheese & Plum Jelly Parcels x 12 | 240G x 1',
                 240, 1, CHILLED,
                 'Booths Lancashire cheese & plum jelly parcels. GFF372R / GFF393R chain. Pedro id 1255. '
                 'Source PDF: GFF393R - Lancashire Cheese and Plum Parcel V.2.pdf.',
                 []),

                ('CVLSRB-R1TEB', 'Booths - Lemongrass Spring Rolls x 8 | 165G x 1', 165, 1, CHILLED,
                 'Booths lemongrass spring rolls. GFF405R chain. Pedro id 2282. '
                 'Source PDF: GFF405R - Lemongrass & Vegetables Spring Roll V.2.pdf.',
                 []),

                ('CVSFPC-R1TEB', 'Booths - Spinach & Feta Parcels 25G x 8 | 200G x 1', 200, 1, CHILLED,
                 'Booths 8×25g spinach & feta parcels. GFF402R chain. Pedro id 2460. '
                 'Source PDF: GFF402R - Spinach & Feta Parcel (XMAS) V.2.pdf.',
                 []),

                ('CDSL1-R1TEB', 'Booths - Duck Selection With Hoisin Dip x 10 | 240G X 1', 240, 1, CHILLED,
                 'Booths duck selection with hoisin dip. GFF282R-GFF284R duck chain. Pedro id 2234. '
                 'Source PDFs: GFF282R Duck Parcel V.3, GFF284R Duck Money Bag v.4.',
                 []),

                ('CMDFLB1-G6TSP', 'SP - Sweet Potato Falafel With Houmous Dip x 4 | 120G X 6', 120, 6, CHILLED,
                 'SP 4×30g sweet potato falafel + houmous dip ×6. GFF297R chain. Pedro id 1497. '
                 'Source PDF: GFF297R - Sweet Potato Falafel v.3.pdf.',
                 [(fal, 6, U_UNIT)]),

                ('CINS1C-R1TEB', 'Booths - Indian Vegetable Selection | 260G x 1', 260, 1, CHILLED,
                 'Booths Indian veg selection. GFF169SD chain. Pedro id 2235. '
                 'Please build recipe from GFF169SD QAS.',
                 [(cinsc, 1, U_UNIT)]),

                # ── Booths ready meals (sauce-based) ─────────────────────────
                ('CCBU-R4TEB', 'Booths - Butter Chicken | 350G X 4', 350, 4, CHILLED,
                 'Booths butter chicken ×4. GFF046SD / GFF134R butter sauce + GFF142R chicken. Pedro id 409.',
                 [(btr, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('CCDO-R4TEB', 'Booths - Chicken Dopiaza | 350G X 4', 350, 4, CHILLED,
                 'Booths chicken dopiaza ×4. GFF047SD / GFF131R dopiazza sauce. Pedro id 421.',
                 [(dop, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('CCJA1-R4TEB', 'Booths - Chicken Jalfrezi | 350G X 4', 350, 4, CHILLED,
                 'Booths chicken jalfrezi ×4. GFF049SD / GFF129R jalfrezi sauce. Pedro id 422.',
                 [(jlf, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('CCKO1-R4TEB', 'Booths - Chicken Korma | 350G X 4', 350, 4, CHILLED,
                 'Booths chicken korma ×4. GFF133R korma sauce + GFF142R chicken. Pedro id 426.',
                 [(kor, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('CCMA1-R4TEB', 'Booths - Chicken Madras | 350G X 4', 350, 4, CHILLED,
                 'Booths chicken madras ×4. GFF043SD / GFF127R madras sauce. Pedro id 424.',
                 [(mdx, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('CCSAG-R4TEB', 'Booths - Chicken Saag Masala | 350G X 4', 350, 4, CHILLED,
                 'Booths chicken saag masala ×4. GFF139R saag masala sauce. Pedro id 429.',
                 [(sgm, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('CCTI1-R4TEB', 'Booths - Chicken Tikka Masala | 350G X 4', 350, 4, CHILLED,
                 'Booths chicken tikka masala ×4. GFF044SD / GFF141R tikka masala sauce. Pedro id 425.',
                 [(tkm, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('CCJAR1-R4TEB', 'Booths - Chicken Jalfrezi With Pilau Rice | 400G X 4', 400, 4, CHILLED,
                 'Booths chicken jalfrezi + pilau rice ×4. GFF052SD / GFF129R jalfrezi + GFF162R jasmine rice. Pedro id 399.',
                 [(jlf, 1, U_UNIT), (ckt, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CCKOR1-R4TEB', 'Booths - Chicken Korma With Pilau Rice With Fried Onions | 400G X 4', 400, 4, CHILLED,
                 'Booths chicken korma + pilau rice ×4. GFF051SD / GFF133R korma + GFF162R rice. Pedro id 403.',
                 [(kor, 1, U_UNIT), (ckt, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CCTIR1-R4TEB', 'Booths - Chicken Tikka Masala With Pilau Rice With Fried Onions | 400G X 4',
                 400, 4, CHILLED,
                 'Booths TKM + pilau rice ×4. GFF050SD / GFF141R TKM sauce + GFF162R rice. Pedro id 402.',
                 [(tkm, 1, U_UNIT), (ckt, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CCBAR1-R4TEB', 'Booths - Balti Chicken With Pilau Rice | 400G X 4', 400, 4, CHILLED,
                 'Booths balti chicken + pilau rice ×4. GFF232SD / GFF333R balti sauce + GFF162R rice. Pedro id 401.',
                 [(blt, 1, U_UNIT), (ckt, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CCTGJR-R4TEB', 'Booths - Thai Green Curry Chicken With Jasmine Rice | 400G X 4', 400, 4, CHILLED,
                 'Booths Thai green chicken curry + jasmine rice ×4. GFF188SD / GFF148R TGC sauce. Pedro id 404.',
                 [(thg, 1, U_UNIT), (ckt, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CCSSR1-R4TEB', 'Booths - Sweet & Sour Chicken With Egg Fried Rice | 400G X 4', 400, 4, CHILLED,
                 'Booths sweet & sour chicken + EFR ×4. GFF189SD / GFF146R S&S sauce + GFF153R EFR. Pedro id 418.',
                 [(sns, 1, U_UNIT), (ckt, 1, U_UNIT), (efr, 1, U_UNIT)]),

                ('CCBAR1-R4TCC', 'Gazebo - Balti Chicken With Pilau Rice | 400G X 4', 400, 4, CHILLED,
                 'Gazebo balti chicken + pilau rice ×4. GFF333R balti sauce. Pedro id 1314.',
                 [(blt, 1, U_UNIT), (ckt, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CCBUR-R4TCC', 'Gazebo - Butter Chicken With Pilau Rice | 400G X 4', 400, 4, CHILLED,
                 'Gazebo butter chicken + pilau rice ×4. GFF134R butter sauce. Pedro id 717.',
                 [(btr, 1, U_UNIT), (ckt, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CCMAR-R4T', 'Gazebo - Chicken Madras & Pilau Rice | 400G X 4', 400, 4, CHILLED,
                 'Gazebo chicken madras + pilau rice ×4. GFF127R madras sauce. Pedro id 2150.',
                 [(mdx, 1, U_UNIT), (ckt, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CCTIKR5-R8T', 'Gazebo - Chicken Tikka Masala W/Aromatic Rice | 350G x 8', 350, 8, CHILLED,
                 'Gazebo TKM + aromatic rice ×8. GFF141R TKM sauce + GFF471R aromatic rice. Pedro id 2257.',
                 [(tkm, 1, U_UNIT), (ckt, 1, U_UNIT), (arr, 1, U_UNIT)]),

                ('CCPANCR-R4TCC', 'Gazebo - Panang Chicken & Fragrant Rice | 400G X 4', 400, 4, CHILLED,
                 'Gazebo panang chicken + fragrant rice ×4. GFF199R panang sauce + GFF448R fragrant rice. Pedro id 450.',
                 [(pan, 1, U_UNIT), (ckt, 1, U_UNIT), (frr, 1, U_UNIT)]),

                ('CCTGJR-R4TCC', 'Gazebo - Thai Green Chicken Curry & Fragrant Rice | 400G X 4', 400, 4, CHILLED,
                 'Gazebo TGC + fragrant rice ×4. GFF148R TGC sauce + GFF448R fragrant rice. Pedro id 451.',
                 [(thg, 1, U_UNIT), (ckt, 1, U_UNIT), (frr, 1, U_UNIT)]),

                ('CCTRCR-R4T', 'Gazebo - Red Thai Chicken Curry & Fragrant Rice| 400G X 4', 400, 4, CHILLED,
                 'Gazebo red Thai chicken curry + fragrant rice ×4. GFF210R (red thai veg) + GFF448R. Pedro id 2176.',
                 [(rth, 1, U_UNIT), (ckt, 1, U_UNIT), (frr, 1, U_UNIT)]),

                ('CPPENCR-R4TEB', 'Booths - Hot Spicy Chicken Panang With Jasmine Rice | 400G X 4', 400, 4, CHILLED,
                 'Booths hot spicy chicken panang + jasmine rice ×4. GFF199R panang sauce + GFF162R jasmine rice. Pedro id 405.',
                 [(pan, 1, U_UNIT), (ckt, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CPGOCR-R4TEB', 'Booths - Hot & Spicy Prawn Goan Curry With Pilau Rice | 400G X 4', 400, 4, CHILLED,
                 'Booths hot & spicy prawn goan curry + pilau rice ×4. GFF191SD / GFF201R goan sauce. Pedro id 406.',
                 [(goa, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CPMA-R4TEB', 'Booths - Prawn Mango Masala | 350G X 4', 350, 4, CHILLED,
                 'Booths prawn mango masala ×4. GFF054SD / GFF132R mango masala sauce. Pedro id 414.',
                 [(mng, 1, U_UNIT)]),

                ('CPLR-R4TEB', 'Booths - Pilau Rice With Crispy Onions | 300G X 4', 300, 4, CHILLED,
                 'Booths pilau rice with crispy onions ×4. GFF192SD / GFF162R jasmine rice. Pedro id 420.',
                 [(jsr, 1, U_UNIT)]),

                ('CVALG-R4TEB', 'Booths - Saag Aloo Gobi | 300G X 4', 300, 4, CHILLED,
                 'Booths saag aloo gobi ×4. GFF041SD / GFF130R saag aloo gobi. Pedro id 423.',
                 [(sag, 1, U_UNIT)]),

                ('CVBAR-R4TEB', 'Booths - Vegetable Balti With Pilau Rice | 400G X 4', 400, 4, CHILLED,
                 'Booths veg balti + pilau rice ×4. GFF244R veg balti. Pedro id 431.',
                 [(vbl, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CVMPR-R4TEB', 'Booths - Mattar Paneer With Pilau Rice | 400G x 4', 400, 4, CHILLED,
                 'Booths mattar paneer + pilau rice ×4. GFF004SD / GFF206R tandoori paneer. Pedro id 407.',
                 [(tpr, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CVMPR-R4TCC', 'Gazebo - Tandoori Paneer & Pilau Rice | 400G X 4', 400, 4, CHILLED,
                 'Gazebo tandoori paneer + pilau rice ×4. GFF206R paneer marination. Pedro id 2149.',
                 [(tpr, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CVJLR-R4T', 'Gazebo - Vegetable Jalfrezi & Pilau Rice | 400G X 4', 400, 4, CHILLED,
                 'Gazebo veg jalfrezi + pilau rice ×4. GFF129R jalfrezi sauce. Pedro id 2155.',
                 [(jlf, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('CVTHCR-R4TEB', 'Booths - Red Thai Vegetable Curry With Jasmine Rice | 400G X 4', 400, 4, CHILLED,
                 'Booths red Thai veg curry + jasmine rice ×4. GFF005SD / GFF210R red thai veg curry. Pedro id 419.',
                 [(rth, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                # ── Ready meal combo / takeaway ───────────────────────────────
                ('CINDML1-R2TCC', 'Gazebo - Indian Takeaway For 1 | 650G X 2', 650, 2, CHILLED,
                 'Gazebo Indian takeaway for 1 ×2. Composite: curry + rice + samosa. Pedro id 1253.',
                 []),

                ('CINDML5-R2TEB', 'Booths - Indian Takeaway For 2 - TK/MA x 2 | 1200G X 2', 1200, 2, CHILLED,
                 'Booths Indian takeaway for 2 (TKM+Madras) ×2. Composite: 2 curries + naan + samosas. Pedro id 1256.',
                 []),

                ('CTHML-R2TEB', 'Booths - Thai Takeaway For 2 - TG/PC x 2 | 1154G X 2', 1154, 2, CHILLED,
                 'Booths Thai takeaway for 2 ×2. Composite: TGC + Panang + jasmine rice. Pedro id 1257.',
                 []),

                # ── Noodles ───────────────────────────────────────────────────
                ('CCNO-R8T', 'Gazebo - Spicy Stir Fried Chicken Noodles | 300G x 8', 300, 8, CHILLED,
                 'Gazebo spicy stir fried chicken noodles ×8. GFF474R noodles + GFF475R chicken. Pedro id 2260.',
                 [(ckn, 1, U_UNIT), (noo, 1, U_UNIT)]),

                ('CVEGN-R6T', 'Gazebo - Noodles | 200G X 6', 200, 6, CHILLED,
                 'Gazebo vegetable noodles ×6. GFF474R stir fried noodles. Pedro id 2139.',
                 [(noo, 1, U_UNIT)]),

                # ── Deli Gz Deli 1.25kg tubs ─────────────────────────────────
                ('CCBAL-B1B', 'Gz Deli - Balti Chicken | 1250G x 1', 1250, 1, CHILLED,
                 'Gz Deli balti chicken 1.25kg. GFF333R balti sauce + GFF142R chicken tikka. Pedro id 2407.',
                 [(blt, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('CCCHET2-B1B', 'Gz Deli - Chicken Chettinad | 1250G x 1', 1250, 1, CHILLED,
                 'Gz Deli chicken chettinad 1.25kg. GFF320R chettinad sauce. Pedro id 2410. '
                 'Source PDF: GFF320R - Chettinad Sauce V.3.pdf.',
                 []),

                ('CCSAA-B1B', 'Gz Deli - Saag Chicken | 1250G x 1', 1250, 1, CHILLED,
                 'Gz Deli saag chicken 1.25kg. GFF139R saag masala sauce + GFF142R chicken. Pedro id 2409.',
                 [(sgm, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('CCSTIM-B1B', 'Gz Deli - Shahi Chicken Tikka Masala | 1250G x 1', 1250, 1, CHILLED,
                 'Gz Deli shahi chicken tikka masala 1.25kg. GFF141R TKM sauce + GFF142R chicken. Pedro id 2408.',
                 [(tkm, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('CLNKO-B1B', 'Gz Deli - Nawabi Lamb Kofta Curry | 1250G x 1', 1250, 1, CHILLED,
                 'Gz Deli nawabi lamb kofta curry 1.25kg. GFF321R nawabi curry + GFF492R lamb kofta. Pedro id 2411.',
                 [(nwb, 1, U_UNIT), (kfr, 1, U_UNIT)]),

                ('CPOBO1-B1B', 'GZ Deli - Chat Patta Bombay Potato | 1250G x 1', 1250, 1, CHILLED,
                 'Gz Deli chat patta bombay potato 1.25kg. GFF487R Bombay Potato. Pedro id 2404.',
                 [(bpo, 1, U_UNIT)]),

                ('CRIPL-B1B', 'Gz Deli - Pilau Rice | 1250G x 1', 1250, 1, CHILLED,
                 'Gz Deli pilau rice 1.25kg. GFF162R jasmine rice base. Pedro id 2412.',
                 [(jsr, 1, U_UNIT)]),

                ('CVPJAL-B1B', 'Gz Deli - Paneer Jalfrezi | 1250G x 1', 1250, 1, CHILLED,
                 'Gz Deli paneer jalfrezi 1.25kg. GFF489R paneer jalfrezi. Pedro id 2406.',
                 [(pjl, 1, U_UNIT)]),

                # ── Indian Selection ──────────────────────────────────────────
                ('CINSB-B45X1T', 'Gc - Indian Selection x 45 | 585G X 1', 585, 1, CHILLED,
                 'Gc 45 Indian selection bites (13g) bulk tray. GFF169SD chain. Pedro id 1493.',
                 [(cinsb, 45, U_UNIT)]),

                ('CINSB-B45X4T', 'BOOKER - Gc - Indian Selection x 45 | 585G X 4', 585, 4, CHILLED,
                 'Booker 45 Indian selection bites ×4. GFF169SD chain. Box OC0016. Pedro id 1277.',
                 [(cinsb, 180, U_UNIT), (needed['OC0016'], 1, U_UNIT)]),

                ('CINSB3-G6T', 'Gazebo - GB - Indian Selection With Tamarind Dip | 84G X 6', 84, 6, CHILLED,
                 'Gazebo GB Indian selection + tamarind dip ×6. GFF169SD chain. Box OC007. Pedro id 1238.',
                 [(cinsb, 6, U_UNIT), (needed['OC007'], 1, U_UNIT)]),

                ('CINSB3-G18T', 'Gazebo - GB - Indian Selection With Tamarind Dip | 84G X 18', 84, 18, CHILLED,
                 'Gazebo GB Indian selection + tamarind dip ×18. GFF169-2SD chain. Box OC001. Pedro id 2190.',
                 [(cinsb, 18, U_UNIT), (needed['OC001'], 1, U_UNIT)]),

                ('CINSC2-B26X8T', 'COSTCO - Gc - Indian Selection x 26 | 1020G X 8', 1020, 8, CHILLED,
                 'Costco 26 Indian selection ×8. GFF169SD chain. Box OC001. Pedro id 1295.',
                 [(cinsc, 208, U_UNIT), (needed['OC001'], 1, U_UNIT)]),

                ('CINSC3-R6T', 'Gazebo - Indian Selection | 200G X 6', 200, 6, CHILLED,
                 'Gazebo Indian selection ×6. GFF169SD chain. Pedro id 1293.',
                 [(cinsc, 6, U_UNIT)]),

                ('CINSC3-R10T', 'Gazebo - Indian Selection | 200G X 10', 200, 10, CHILLED,
                 'Gazebo Indian selection ×10. GFF169SD chain. Pedro id 2183.',
                 [(cinsc, 10, U_UNIT)]),

                ('CKASG-B2T', 'Gc - Kashmiri Dum Gravy | 3000G X 2', 3000, 2, CHILLED,
                 'Gc Kashmiri dum gravy 3kg ×2. GFF335R dum gravy. Pedro id 1501. '
                 'Source PDF: GFF335R - Dum Gravy Kashmiri V.2.pdf.',
                 []),

                # ── Samosa variants ───────────────────────────────────────────
                ('CCSAC2-B25X4TTA', 'TA - Chicken Samosa 35G with Tamarind Dip x 25 | 995G x 4', 995, 4, CHILLED,
                 'TA 25×35g chicken samosa + tamarind dip ×4. GFF004R chain. Pedro id 2563. '
                 'Tamarind dip not mapped — please add and update recipe.',
                 []),

                ('CCSAC3-B24X8T', 'COSTCO - GC - 24 Chicken Samosa 40G | 960G X 8', 960, 8, CHILLED,
                 'Costco 24×40g chicken samosa ×8. GFF004R / GFF511R 40g chain. Pedro id 3432. '
                 'Source PDF: GFF511R - Chicken Samosa 40g Frying.pdf.',
                 []),

                # ── Lidl mixed cases ──────────────────────────────────────────
                ('CCSVSC-MC10TLI', 'LIDL - DELUXE - Mixed Chicken & Veg Samosa Case | 2400G', 2400, 1, CHILLED,
                 'Lidl Deluxe mixed chicken & veg samosa case. Composite product. Pedro id 2497.',
                 []),

                ('CCSVSL-MC8TLI', 'LIDL - Mixed FTG Chicken & Veg Samosa Case | 800G', 800, 1, CHILLED,
                 'Lidl Mixed FTG chicken & veg samosa case. Composite product. Pedro id 2114.',
                 []),

                # ── Frozen frozen ready meals ─────────────────────────────────
                ('FBCCR-R8T', 'GZFR - Chilli Con Carne W/Rice | 350G X 8', 350, 8, FROZEN,
                 'Frozen Gazebo chilli con carne + rice ×8. GFF419R chilli con carne. Pedro id 3310. '
                 'Source PDF: GFF419R - Chilli Con Carne (Takul).pdf.',
                 []),

                ('FBCUR-R8TOA', 'OA - Beef Curry With Pilau Rice | 400G X 8', 400, 8, FROZEN,
                 'OA beef curry + pilau rice ×8. GFF525R beef curry sauce (OA). Pedro id 3358. '
                 'Source PDF: GFF525R-Beef Curry Sauce -Oakhouse-V.1.pdf.',
                 []),

                ('FCBIR3-R8T', 'GZFR - Chicken Biryani | 350G X 8', 350, 8, FROZEN,
                 'Frozen Gazebo chicken biryani ×8. Pedro id 3305. Biryani rice + chicken tikka assembly.',
                 [(ckt, 1, U_UNIT)]),

                ('FCBUR-R4TDA', 'GCDA - Butter Chicken With Pilau Rice | 400G X 4', 400, 4, FROZEN,
                 'GCDA frozen butter chicken + pilau rice ×4. GFF134R butter sauce + GFF142R chicken. Pedro id 3406.',
                 [(btr, 1, U_UNIT), (ckt, 1, U_UNIT), (jsr, 1, U_UNIT)]),

                ('FCBUR1-R8T', 'GZFR - Butter Chicken W/Aromatic Rice | 350G X 8', 350, 8, FROZEN,
                 'Frozen Gazebo butter chicken + aromatic rice ×8. GFF134R + GFF471R aromatic rice. Pedro id 3308.',
                 [(btr, 1, U_UNIT), (ckt, 1, U_UNIT), (arr, 1, U_UNIT)]),

                ('FCBUR1-R8TOA', 'OA - Butter Chicken With Pilau Rice | 400G X 8', 400, 8, FROZEN,
                 'OA butter chicken + pilau rice ×8. GFF052SD / GFF522R OA butter sauce. Pedro id 3415. '
                 'Source PDF: GFF522R- Butter Sauce -Oakhouse-V.1.pdf.',
                 []),

                ('FCCUR-R8TOA', 'OA - Chicken Curry With Pilau Rice | 400G X 8', 400, 8, FROZEN,
                 'OA chicken curry + pilau rice ×8. GFF527R OA curry sauce. Pedro id 3359. '
                 'Source PDF: GFF527R- Curry Sauce -Oakhouse-V.1.pdf.',
                 []),

                ('FCFRR-1R10TOA', 'OA - Mini Fruity Chicken Curry With Steam Rice | 240G X 10', 240, 10, FROZEN,
                 'OA mini fruity chicken curry + steam rice ×10. GFF524R fruity sauce (OA). Pedro id 3423. '
                 'Source PDF: GFF524R- Fruity Sauce -Oakhouse-V.1.pdf.',
                 []),

                ('FCFRR-R8TOA', 'OA - Fruity Chicken Curry With Steam Rice | 400G X 8', 400, 8, FROZEN,
                 'OA fruity chicken curry + steam rice ×8. GFF524R fruity sauce + GFF420R steamed rice. Pedro id 3362.',
                 []),

                ('FCJAR2-R8TOA', 'OA - Chicken Jalfrezi With Pilau Rice | 400G X 8', 400, 8, FROZEN,
                 'OA chicken jalfrezi + pilau rice ×8. GFF523R OA jalfrezi sauce. Pedro id 3361. '
                 'Source PDF: GFF523R- Jalfrezi Sauce -Oakhouse-V.1.pdf.',
                 []),

                ('FCNO-R8T', 'GZFR - Spicy Stir Fried Chicken Noodles | 300G X 8', 300, 8, FROZEN,
                 'Frozen Gazebo spicy stir fried chicken noodles ×8. GFF474R + GFF475R. Pedro id 3306.',
                 [(ckn, 1, U_UNIT), (noo, 1, U_UNIT)]),

                ('FCPANCR-R4TDA', 'GCDA - Panang Chicken & Fragrant Rice | 400G X 4', 400, 4, FROZEN,
                 'GCDA panang chicken + fragrant rice ×4. GFF199R panang + GFF448R fragrant rice. Pedro id 3408.',
                 [(pan, 1, U_UNIT), (ckt, 1, U_UNIT), (frr, 1, U_UNIT)]),

                ('FCPANR1-R8TMC', 'MC - Panang Chicken Curry with Black Rice | 400G X 8', 400, 8, FROZEN,
                 'MC panang chicken + black rice ×8. GFF199R panang sauce + GFF438R black rice. Pedro id 1258.',
                 [(pan, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('FCTGBR1-R8TMC', 'MC - Thai Green Chicken Curry with Black Rice | 400G X 8', 400, 8, FROZEN,
                 'MC Thai green chicken + black rice ×8. GFF376R MC TGC sauce + GFF438R black rice. Pedro id 1269.',
                 [(thg, 1, U_UNIT), (ckt, 1, U_UNIT)]),

                ('FCTGJR-R4TDA', 'GCDA - Thai Green Chicken Curry & Fragrant Rice | 400G X 4', 400, 4, FROZEN,
                 'GCDA TGC + fragrant rice ×4. GFF148R TGC sauce + GFF448R fragrant rice. Pedro id 3409.',
                 [(thg, 1, U_UNIT), (ckt, 1, U_UNIT), (frr, 1, U_UNIT)]),

                ('FCTIKR5-R8T', 'GZFR - Chicken Tikka Masala W/Aromatic Rice | 350G X 8', 350, 8, FROZEN,
                 'Frozen Gazebo TKM + aromatic rice ×8. GFF141R + GFF471R aromatic rice. Pedro id 3307.',
                 [(tkm, 1, U_UNIT), (ckt, 1, U_UNIT), (arr, 1, U_UNIT)]),

                ('FCTIR2-1R10TOA', 'OA - Mini Chicken Tikka Masala With Pilau Rice | 240G X 10', 240, 10, FROZEN,
                 'OA mini chicken tikka masala + pilau rice ×10. GFF533R OA TKM sauce. Pedro id 3422. '
                 'Source PDF: GFF533R-Tikka Masala Sauce -Oakhouse-V.1.pdf.',
                 []),

                ('FCTRCR-R4TDA', 'GCDA - Red Thai Chicken Curry & Fragrant Rice| 400G X 4', 400, 4, FROZEN,
                 'GCDA red Thai chicken curry + fragrant rice ×4. GFF210R red thai veg + GFF448R fragrant. Pedro id 3407.',
                 [(rth, 1, U_UNIT), (ckt, 1, U_UNIT), (frr, 1, U_UNIT)]),

                ('FFKCR1-R8TMC', 'MC - Keralan Cod Curry with Black Rice | 400G X 8', 400, 8, FROZEN,
                 'MC Keralan cod curry + black rice ×8. GFF362R MC keralan sauce + GFF438R black rice. Pedro id 1265. '
                 'Source PDF: GFF362R - Keralan Curry Sauce (MC) -v.3.pdf.',
                 []),

                ('FLBHR-R8TOA', 'OA - Lamb Bhuna With Pilau Rice | 400G X 8', 400, 8, FROZEN,
                 'OA lamb bhuna + pilau rice ×8. GFF526R OA bhuna sauce + GFF546R cooked lamb diced. Pedro id 3425. '
                 'Source PDFs: GFF526R-Bhuna Sauce-Oakhouse-V.1, GFF546R Cooked Lamb Diced-Oakhouse.',
                 []),

                ('FVTIR-R8TOA', 'OA - Vegetable Tikka Masala With Pilau Rice | 400G X 8', 400, 8, FROZEN,
                 'OA veg tikka masala + pilau rice ×8. GFF528R OA veg TKM sauce. Pedro id 3360. '
                 'Source PDF: GFF528R-Vegetable Tikka Masala Sauce-Oakhouse-V.1.pdf.',
                 []),

                ('FVDBSR2-R8TMC', 'MC - Broccoli and Butternut Squash Dhal with Brown Rice | 400G X 8', 400, 8, FROZEN,
                 'MC broccoli & butternut squash dhal + brown rice ×8. GFF440R spinach dhal. Pedro id 1267.',
                 []),

                ('FVSMBSR-R8TMC', 'MC - Saag Chana Masala W/Butternut Squash & Brown Rice | 400G X 8', 400, 8, FROZEN,
                 'MC saag chana masala + brown rice ×8. GFF413R saag chana butternut sauce. Pedro id 1272.',
                 []),

                ('FPSINDR-R8TMC', 'MC - South Indian King Prawn Curry W/Brown Rice | 400G X 8', 400, 8, FROZEN,
                 'MC South Indian king prawn curry + brown rice ×8. GFF418R south indian sauce. Pedro id 1264. '
                 'Source PDF: GFF418R - South Indian Sauce (MC).pdf.',
                 []),

                # ── Frozen novel snacks ───────────────────────────────────────
                ('FVCFRC1-R8TCO', 'Cook - Thai Style Corn Fritters x 6 | 150G X 8', 150, 8, FROZEN,
                 'Cook 6×25g Thai style corn fritters ×8. GFF459R / GFF517R corn fritter. Pedro id 3313. '
                 'Source PDF: GFF517R- Thai Style Corn Fritters - V.02.pdf.',
                 []),

                ('FVGYOC-R8TCO', 'Cook - Vegetable Gyoza | 120G X 8', 120, 8, FROZEN,
                 'Cook 6×20g vegetable gyoza ×8. Novel product. Pedro id 3351.',
                 []),

                ('FVPARA-R8T', 'GZFR - Penne Arrabbiata | 350G X 8', 350, 8, FROZEN,
                 'Frozen Gazebo penne arrabbiata ×8. GFF514R arrabbiata sauce. Pedro id 3309. '
                 'Source PDF: GFF514R- Arrabbiata Sauce.pdf.',
                 []),

                ('FMXBL-B25X2T', 'Gc - Mexican Burger 100G x 25 | 2500G X 2', 2500, 2, FROZEN,
                 'Frozen Gc 25×100g Mexican burger ×2. Novel product. Pedro id 1534. '
                 'Source PDF: GFF024R - MEXICAN VEGETABLE BURGER (Delisted — verify current recipe).',
                 []),

                # ── Frozen Indian selection ───────────────────────────────────
                ('FINSB3-G6T', 'GZFR - Indian Selection W/Tamarind Dip | 84G X 6 | Frozen', 84, 6, FROZEN,
                 'Frozen Gazebo GB Indian selection + tamarind dip ×6. GFF169SD chain. Pedro id 3317.',
                 [(cinsb, 6, U_UNIT)]),

                ('FINSC-B60B', 'BRAKES - Gc - Indian Celebration Selection x 60 | 1700G', 1700, 1, FROZEN,
                 'Brakes 60 Indian selection frozen bag. GFF169SD chain. Pedro id 1507.',
                 [(cinsc, 60, U_UNIT)]),

                ('FINSC2-B26X4T', 'GC - Indian Selection x 26 | 1020G X 4', 1020, 4, FROZEN,
                 'GC frozen 26 Indian selection ×4. GFF169SD chain. Pedro id 2463.',
                 [(cinsc, 104, U_UNIT)]),

                ('FINSC2-B26X4TDA', 'GCDA - Indian Selection X 26 | 1020G X 4', 1020, 4, FROZEN,
                 'GCDA frozen 26 Indian selection ×4. GFF169SD chain. Pedro id 3354.',
                 [(cinsc, 104, U_UNIT)]),

                ('FINSC2-B26X8T', 'COSTCO - GC - Indian Selection x 26 | 1020G X 8 | FRANCE', 1020, 8, FROZEN,
                 'Costco France 26 Indian selection ×8. GFF169SD chain. Pedro id 2480.',
                 [(cinsc, 208, U_UNIT)]),

                ('FINSC2-1B26X8T', 'COSTCO - GC - Indian Selection x 26 | 1020G X 8 | SPAIN', 1020, 8, FROZEN,
                 'Costco Spain 26 Indian selection ×8. GFF169SD chain. Pedro id 2481.',
                 [(cinsc, 208, U_UNIT)]),
            ]

            for item in fg_list:
                stock_code, name, pack_g, items, chilled, note, lines = item
                fg, created = _fg(stock_code, name, pack_g, items, chilled, note, lines, needed)
                results.append(f"{'NEW' if created else 'reuse'} {stock_code} id={fg.id}")

        for r in results:
            self.stdout.write(r)
        self.stdout.write(self.style.SUCCESS(f'Done — {len(results)} lines'))
