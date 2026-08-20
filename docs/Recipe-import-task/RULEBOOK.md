# Recipe import rulebook (live-202)

Locked from two pilots: **CVSAL-1G6T** (veg G&G 100 g × 6) and **CCSAL-1G6T** (chicken tikka G&G 100 g × 6).

List: [live-202-product.md](live-202-product.md). Tracker: [tracker-live-202.xlsx](tracker-live-202.xlsx). Verify prompt: [VERIFY.md](VERIFY.md).

**Accuracy over speed. One SKU. PDF grams 100%. Then another agent verifies. Then the next code.**

---

## Do not

- Import more than one stock code in one run.
- Guess mix grams, round them, or put `1` on Qty and the real amount in net batch.
- Apply Pedro 1.25× / PDF 0.80× as yield.
- Use files under `Obsolete`, `NEW OBSOLETE`, `1.Obsolete`, or `~$`.
- Activate a recipe. Copy pack yield 0.99 onto `process_loss`.
- Substitute K-peel 240 mm with 275 mm K-peel or 240 mm PET weld.
- Use new-DB `SAUCE0-24` for tamarind (it is Thai Panang). Tamarind paste on veg spice stayed `SPICE0-66`.
- Write notes like: `Source: signed GFF PDF grams… Pedro 1.25x… Anomalies: docs/…`
- Trust `harvi/Copy of recipe.xlsx` or `seed_demo_recipe_samosa.py`.
- Start the next SKU before verify lists **PASS**.
- Create a new raw material or packing material. Omit the line, log it, ask the user to add the catalogue code.

---

## Process (every SKU, top to bottom)

### 1. Pick the next row

From the tracker: first `pending` stock code. Read Pedro id, name, `productreceipecode`, `gffCode` from live-202.

### 2. Walk the factory tree

Pedro dump: `source-of-truth/Dump20260819 - Pedro.sql` `tblproducttree` + `tblproducts`.

Start at the finished-pack id. Walk every child. Record for each node: recipe code, name, gff, src/dest container, unit, line qty, batch qty, yield.

Typical snack chain (pilots):

```
spice → (steam if used) → mixer → belt + pastry → fry → packed item (tray ± film) → finished pack (sleeve × n + box)
```

Ready meals will differ (cook / rice / sauce). Still walk the real tree. Do not copy the samosa chain onto a curry.

### 3. Match Harvi files to GFF codes

| Stage | Where | How |
|-------|--------|-----|
| Mixer / steam / cook | `harvi/1 LR/Signed copy-Print from here/` | Filename starts with that GFF (e.g. `GFF004R`, `GFF241R`). Highest issue in the **current** folder. |
| Spice | `harvi/2 SPICES/Signed copy SPICE/` | `GFF004R-S`, `GFF003R-S`, … |
| Packed / HR | `harvi/HIGH RISK-QAS/` current customer folder | `GFF185MC`, `GFF186MC`, … matching product `gffCode` |
| Sleeve / tray names | `harvi/packging demo.xlsx` | Only to confirm nearest new-DB code |

If a stage has a GFF and **no** current file: log anomaly, use Pedro for that stage, do not invent a sheet.

### 4. Read the PDF (must be 100%)

`pypdf.PdfReader` → full text. Pull:

- Document No, issue, dates, author
- Every ingredient **Amount per Mix (g)**
- Total
- Spice trace code on mixer sheets (e.g. `GFF004R-S`)

**Pass rule:** `sum(ingredient grams) == Total` on the sheet. If not, stop and log. Do not import.

Write Qty = PDF grams. Leave net batch and gross batch empty.

If Pedro batch ≠ PDF grams (veg was 1.25×): still write PDF. Log as scale anomaly. Do not “fix” it with yield.

If Pedro batch = PDF grams (chicken): still write PDF grams on Qty, not the Pedro fraction (0.115952).

### 5. Read the QAS

Current `.xlsx` only. Pull SKU codes, filling GFF + weight, tray, film, allergens. QAS is pack spec, not mix grams.

### 6. Map into the new catalogue

For each child: `product.recipe_code`. Reuse existing RM/pack rows. Do not duplicate sugar/chicken/peas.

Missing **raw material or packing material** (PDF name or Pedro/QAS code not in the new catalogue): **do not create it**. Omit the recipe line. Log it on the anomaly sheet. Put a plain-English note on that stage: please add this line on the recipe with the catalogue code, name, and grams. Then continue the rest of the tree.

Missing **pack code**:

| Case | Action |
|------|--------|
| Film wrong width or wrong type | Omit. Log. |
| Sleeve code missing, same artwork exists with mm drift | Use nearest. Log 263 vs 268. |
| G&G square tray QAS vs Pedro | Pedro `PKLEE001-13` unless the user picks otherwise (locked on both pilots). |

Tamarind: never Pedro `SAUCE0-24` in the new DB.

### 7. Write drafts

Same shape as `import_pilot_ccsal_1g6t.py`:

- New process SKUs as needed (spice / mixer / belt / fry / packed / FG)
- Categories from pilots where the stage matches: spice 6, mix-of-samosa 164, belt 167, frying 173, packed 76, cased 127
- Units: Pedro 1=`unit`, 2=`grams`, 3=`meters`, tray lines `9005` TRAY
- Belt/fry/pack counts from Pedro
- `process_loss` = 1.0000
- Status **draft**. Never activate
- Attach the matching PDF/QAS on **that** version
- Notes: plain English (see CCSAL notes, not CVSAL’s old paragraph)
- Timeline: `actor_name` = `System Admin`

### 8. Anomaly workbook

`docs/Recipe-import-task/anomaly-<STOCKCODE>.xlsx`

Sheets:

1. **Anomalies** — ID, Stage, Code, Issue, Factory/PDF/QAS, New catalogue, What we did
2. **Recipe tree** — every line written
3. **PDF vs factory** — every PDF gram vs Pedro batch, Match? Yes/No

Every omit, swap, missing RM, unit clash, SKU named on QAS but not this pack, belt fill not on PDF — one row.

### 9. Self-check (importer, before stop)

- [ ] Each mapped PDF gram = DB Qty (not net batch)
- [ ] PDF total still equals the sheet Total (even if omitted RM means DB sum is short)
- [ ] Every omitted RM/pack is on the anomaly sheet with “please add catalogue code”
- [ ] Every current GFF file is attached on the matching stage
- [ ] No Obsolete file used
- [ ] Film not silently swapped
- [ ] Notes are plain English
- [ ] Timeline says System Admin
- [ ] Tracker row = `imported-awaiting-verify`

### 10. Verify agent, then next

Give the other agent [VERIFY.md](VERIFY.md) + the stock code. They re-read the PDF themselves. **PASS** required.

Only then: tracker `verified`, start the next `pending` code. Submit a combined anomalies pack when the user asks — do not wait for all 202 unless asked.

---

## Units (`product_unit`)

1 unit · 2 grams · 3 meters · 5 Kg · 6 Box · 7 Liter · 9001 Each · 9002 Case · 9003 Bag · 9004 Bottle · 9005 TRAY

---

## Pilots (do not re-import unless asked)

| Stock code | Mix GFF | Spice GFF | QAS | Mix total PDF | vs Pedro |
|------------|---------|-----------|-----|---------------|----------|
| CVSAL-1G6T | GFF003R V.17 | GFF003R-S V.14 | GFF186MC V.6 | 186080 g | Pedro 1.25× — PDF written |
| CCSAL-1G6T | GFF004R V.20 | GFF004R-S V.16 | GFF185MC V.6 | 215606 g | Match — PDF written |
