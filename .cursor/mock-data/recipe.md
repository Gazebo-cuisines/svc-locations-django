## FG — Gazebo - G&G - Potato & Pea Samosa | 100G X 6
**Code:** CVSAL-1G6T (new ERP ID) — mirrors FG 1230 structure exactly.

### The chain
| # | Container (src → dest) | Recipe code |
|---|---|---|
| 1 | Spice Room → Mixers | GFF005R-S |
| 2 | Mixers → Belts | GFF005R-Mx |
| 3 | Belts → Fryers | GFF005R-B-100 |
| 4 | Fryers → High Risk | GFF005R-F-100 |
| 5 | High Risk → Sleeving | CVSAL |
| 6 | Sleeving → Dispatch | CVSAL-1G6T |

### 1. Spice Room — GFF005R-S (batch 13,106 g)
| Ingredient | Qty | Fraction |
|---|---|---|
| SUGAR | 92.477 g | 0.092477 |
| CUMIN SEED | 38.532 g | 0.038532 |
| CHILLI POWDER | 28.918 g | 0.028918 |
| VEG SAMOSA SEASONING | 840.073 g | 0.840073 |

### 2. Mixers — GFF005R-Mx
| Ingredient | Fraction | Batch qty |
|---|---|---|
| POTATO DICED 10MM (FROZEN) | 0.231904 | 50,000 |
| POTATO MASH / FLAKE | 0.115952 | 25,000 |
| ONION WHITE DICED 10MM (FROZEN) | 0.231904 | 50,000 |
| PEAS (FROZEN) | 0.231904 | 50,000 |
| Water - Step 1 | 0.092762 | 20,000 |
| LEMON JUICE | 0.034786 | 7,500 |
| Veg Samosa - 005 - Spice | 0.060787 | 13,106 |

Sums to 1.0. Total 215,606.

### 3. Belts — GFF005R-B-100
| Ingredient | Qty |
|---|---|
| Veg Samosa - 005 - Mixer | 69.5503 |
| SAMOSA PASTRY - LARGE CUT | 1.0 |

### 4. Fryers — GFF005R-F-100
| Ingredient | Qty |
|---|---|
| Veg Samosa - 100g - 005 - Belt | 1.0 |

### 5. High Risk — CVSAL
| Ingredient | Qty | Yield |
|---|---|---|
| Veg Samosa - 100g - 005 - Frying | 1.0 | 1.0 |
| Tray - Grab & Go - Square Tray | 1.0 | 0.99 |
| Film - K peel 7G - 25mu - f240mm | 0.2 | 1.0 |

### 6. Sleeving — CVSAL-1G6T
| Ingredient | Qty | Yield |
|---|---|---|
| GG - 1x Veg Samosa - 005R - 100g SQ Tray | 6.0 | 1.0 |
| G&G Potato & Pea Samosa sleeve label (95x263mm) | 6.0 | 0.9999 |
| Box - Grab and Go x 6 Tubs - 280x148x134 | 1.0 | 0.99 |

---

### Products used

**New products to create (7):**
- Veg Samosa - 005 - Spice (GFF005R-S)
- Veg Samosa - 005 - Mixer (GFF005R-Mx)
- Veg Samosa - 100g - 005 - Belt (GFF005R-B-100)
- Veg Samosa - 100g - 005 - Frying (GFF005R-F-100)
- GG - 1x Veg Samosa - 005R - 100g SQ Tray (CVSAL)
- Gazebo - G&G - Potato & Pea Samosa | 100G X 6 (CVSAL-1G6T)
- G&G Potato & Pea Samosa sleeve label (95mm x 263mm) — new artwork

**New raw materials (check if already in system):**
- POTATO DICED 10MM (FROZEN)
- POTATO MASH / FLAKE
- VEG SAMOSA SEASONING

**Reused from FG 1230 (no change):**
SUGAR · CUMIN SEED · CHILLI POWDER · ONION WHITE DICED 10MM (FROZEN) · PEAS (FROZEN) · LEMON JUICE · Water · SAMOSA PASTRY - LARGE CUT · Tray - Grab & Go - Square Tray · Film - K peel 7G - 25mu - f240mm · Box - Grab and Go x 6 Tubs - 280x148x134

Want this as a Word/Excel doc for uploading into Notazone?

---

## Gazebo supplier map + goods-in + production E2E

Prerequisite: samosa demo products/locations/recipes already seeded.

```bash
python manage.py seed_demo_recipe_samosa
python manage.py seed_samosa_supplier_stock
python manage.py seed_samosa_supplier_stock --production-e2e
```

Flags: `--map-only`, `--goods-in-only`, `--skip-map`, `--packs 10`, `--production-e2e`.

### Data rules

- Supplier: existing location with role `supplier` and name containing `Gazebo` (not created).
- Products: `910101`–`910115` only (includes tray / film / sleeve / box).
- Shape: `1 Box × 10 Kg` for mass (`g`); `1 Box × 10 Each` for packaging/pastry; Liter pack if product unit is Liter.
- Units: lookup only — no new `product_unit` rows.
- Goods-in: 10 packs at location `910200`; stock qty = packs × multiplier (g products: ×1000 from Kg).
- Production E2E: one unit per stage `910001`→`910006` (transfer components → consume → output). Needs an active `planning.Resource`.

### Verify

- `GET /product/910101/suppliers/` — default Gazebo mapping, shape label
- `GET /stock/balances/?location_id=910200` — RM + packaging on hand
- `GET /stock/production/` — output entries for intermediates/FG