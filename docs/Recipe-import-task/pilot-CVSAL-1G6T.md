# Pilot: CVSAL-1G6T Harvi cross-check

Finished pack: **Gazebo G&G Vegetable Samosa 100G × 6**  
Live-202: `CVSAL-1G6T` / Pedro id **1227** / Active  
**No DB writes.**

| Role | Source |
|------|--------|
| Live BOM | `source-of-truth/Dump20260819 - Pedro.sql` `tblproducttree` |
| Mixer sheet | `harvi/1 LR/.../GFF003R VEGETABLE SAMOSA V.17.pdf` (issue 17, 04.11.2021, Magda Shah) |
| Spice sheet | `harvi/2 SPICES/.../GFF003R-S VEGETABLE SAMOSA - SPICES V.14.pdf` (issue 14, 23.11.2021) |
| High-risk QAS | `harvi/HIGH RISK-QAS/Gazebo/Grab & Go-QAS/GFF186MC - G&G  Vegetable Samosa 100g V.6.xlsx` (issue 6, 22.07.2020 / review 03.06.2025) |
| RM catalogue | `harvi/threat_extracted_Master ERP - Raw Materials Refrence.xlsx` |
| Pack SKUs | `harvi/packging demo.xlsx` |
| Import tracker | `harvi/Copy of recipe.xlsx` — **no `CVSAL-1G6T` row** |

---

## Process chain (Pedro live)

```
spice GFF003R-S (2600)     Spice Room → Mixers
steam GFF241R-St (2822)    Steaming → Mixers     ← VEGCHI-02 potato
steam GFF242R-St (2823)    Steaming → Mixers     ← VEGFRO-04 carrot
mixer GFF003R-Mx (3071)    Mixers → Belts
belt  GFF003R-B-100 (3143) Belts → Fryers        + PASTRY-01
fry   GFF003R - F - 100 (3229)  Fryers → High Risk
HR    CVSAL (61)           High Risk → Sleeving  + tray + film   gff=GFF186MC
FG    CVSAL-1G6T (1227)    Sleeving → Dispatch   + sleeve ×6 + box
```

QAS A6 names **`CVSAL-G12T & CVSAL-1G6T`** on the same filling (`VEGETABLE SAMOSA (GFF003R) 100 g`).

---

## Mixer — PDF V.17 vs Pedro `GFF003R-Mx`

Same 8 lines. Pedro batch = **1.25 ×** PDF (PDF = **0.80 ×** Pedro). Ratios match.

| Line | PDF ingredient | PDF g | Pedro child | Pedro batch g | Pedro/PDF |
|---:|---|---:|---|---:|---:|
| 1 | Steamed Potatoes | 96000 | `GFF241R - St` (2822) | 120000 | 1.25 |
| 2 | Peas Defrosted | 48000 | `VEGFRO-01` (1996) | 60000 | 1.25 |
| 3 | Steamed Diced Carrots | 16000 | `GFF242R - St` (2823) | 20000 | 1.25 |
| 4 | Diced Onions | 6400 | `VEGFRO-02` (1997) | 8000 | 1.25 |
| 5 | Tomato Paste | 3840 | `SAUCE0-03` (1955) | 4800 | 1.25 |
| 6 | Lemon Juice | 3200 | `SAUCE0-02` (1954) | 4000 | 1.25 |
| 7 | Green Coriander | 1600 | `VEGFRO-10` (2005) | 2000 | 1.25 |
| 8 | Spices | 11040 | `GFF003R-S` (2600) | 13800 | 1.25 |
|  | **Total** | **186080** | | **232600** | **1.25** |

PDF spice trace = **GFF003R-S** — matches Pedro. Per-piece mixer fill on belt is Pedro **80.2 g** (not on PDF).

---

## Spice — PDF V.14 vs Pedro `GFF003R-S`

Same 7 lines. Same **1.25×** batch scale.

| Line | PDF ingredient | PDF g | Pedro child | Pedro batch g | Pedro/PDF |
|---:|---|---:|---|---:|---:|
| 1 | Balti Paste (KB0313) | 6400 | `SAUCE0-05` (1957) | 8000 | 1.25 |
| 2 | Salt | 1440 | `SPICE0-02` (1804) | 1800 | 1.25 |
| 3 | Sugar | 1600 | `SPICE0-03` (1805) | 2000 | 1.25 |
| 4 | Cumin Seeds | 640 | `SPICE0-04` (1806) | 800 | 1.25 |
| 5 | Chilli Powder | 320 | `SPICE0-08` (1810) | 400 | 1.25 |
| 6 | Tamarind Paste | 320 | `SAUCE0-24` (1976) TAMARIND CONCENTRATE | 400 | 1.25 |
| 7 | Garam Masala | 320 | `SPICE0-06` (1808) | 400 | 1.25 |
|  | **Total** | **11040** | | **13800** | **1.25** |

---

## Pack — QAS V.6 vs Pedro vs `packging demo.xlsx`

| Role | QAS GFF186MC | Pedro live | packging demo |
|------|--------------|------------|---------------|
| Filling | GFF003R 100 g | fry `GFF003R - F - 100` | — |
| Tray | **`PKESS001-09`** Grab & Go SQ Tray (141×141) | **`PKLEE001-13`** (2134) qty 1 unit yield 0.9900 | both exist: Enviropax `PKESS001-09` (1×900) vs Leeways `PKLEE001-13` (1×792) |
| Film | `PKKMP001-08` K peel 7G+25A/f-240mm | `PKKMP001-08` (2135) qty 0.2 m yield 0.9900 | match |
| Sleeve | not on QAS (HR spec) | `GNG000-03` (2289) qty 6 yield 0.9999 | **`PKTHE006-03`** G&G Veg Samosa 95×268mm — **code + 263 vs 268 mm drift** |
| Box | not on QAS | `PKCON001-35` (1130) qty 1 yield 0.9900 | match, CONNECT PACKAGING 280×148×134 |
| Allergen | WHEAT | pastry `PASTRY-01` | — |

QAS amendment: **Tray Product Code Changed** (previous issue 07.03.2024). Signed PDF/QAS is newer on tray than Pedro live tree.

---

## RM map (PDF / Pedro name → Sage / Harvi stock)

| PDF / stage | Pedro code (id) | RM internal name | Sage / stock | Supplier |
|---|---|---|---|---|
| Steamed potatoes | `VEGCHI-02` (2059) via `GFF241R - St` | POTATO DICED 10MM (FRESH) | `RMIHF001-03` | I.H.FOODS |
| Peas Defrosted | `VEGFRO-01` (1996) | PEAS (FROZEN) | `RMHAR002-01` | BELFIELD FARMS |
| Steamed carrots | `VEGFRO-04` (1999) via `GFF242R - St` | CARROT DICED 10MM (FROZEN) | `RMHAR002-31` | BELFIELD FARMS |
| Diced Onions | `VEGFRO-02` (1997) | ONION WHITE DICED 10MM (FROZEN) | `RMHAR002-36` | BELFIELD FARMS |
| Tomato Paste | `SAUCE0-03` (1955) | TOMATO PASTE | `RMKTC001-11` | KTC |
| Lemon Juice | `SAUCE0-02` (1954) | LEMON JUICE | `RMKIR001-03` | KIRIL MISCHEFF |
| Green Coriander | `VEGFRO-10` (2005) | CORIANDER CHOPPED (FROZEN) | `RMDAR001-01` | DAREGAL |
| Balti Paste (KB0313) | `SAUCE0-05` (1957) | BALTI PASTE | `RMWES001-01` | WEST MILL / AB WORLD FOODS |
| Salt | `SPICE0-02` (1804) | SALT | `RMUNI001-01` | UNIVAR |
| Sugar | `SPICE0-03` (1805) | SUGAR | `RMKEN003-01` | KENT FOODS |
| Cumin Seeds | `SPICE0-04` (1806) | CUMIN SEED | `RMVIR001-81` | VIRANI |
| Chilli Powder | `SPICE0-08` (1810) | CHILLI POWDER | `RMVIR001-79` | VIRANI |
| Tamarind Paste | `SAUCE0-24` (1976) | TAMARIND CONCENTRATE | `RMTOP001-11` | TOP-OP |
| Garam Masala | `SPICE0-06` (1808) | GARAM MASALA | `RMVIR001-12` | VIRANI |
| (belt, not on PDF) | `PASTRY-01` (1864) | SAMOSA PASTRY - LARGE CUT | `RMSTJ001-19` | ST.JAMES PASTRY |

All mixer/spice names resolved. No unmatched RM.

---

## Import rules (locked)

- PDF grams on spice + mixer + steam (`GFF241R` / `GFF242R`). Do **not** apply 0.80 / 1.25 as yield.
- Pedro only where there is no PDF (belt fill, fry, pack counts).
- All new recipe versions stay **draft**. Human approves later.
- Anomalies: [anomaly-CVSAL-1G6T.md](anomaly-CVSAL-1G6T.md)
- Ignore `Copy of recipe.xlsx` and `seed_demo_recipe_samosa.py`.

Units live `product_unit`: 1=`unit`, 2=`grams`, 3=`meters`, 9005=`TRAY` (see anomaly A10 / units table).
