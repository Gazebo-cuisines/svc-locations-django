# Unit of Measure — Legacy Audit and New System Design

Scope: how units of measure are stored and used in every calculation in the legacy system, what is broken, and the target model for the new system.

Sources: [`data/Dump20260720.sql`](data/Dump20260720.sql), live `production` schema on `3.10.84.186`, `VB Access/modules/*.bas`, NotaZone knowledgebase (`notazoneguidetoaddingproductsandlocations-v10.md`, `notazoneguidetorecipes-v6.md`).

---

## 1. What `tblunits` actually is

```sql
CREATE TABLE `tblunits` (
  `id`                int NOT NULL AUTO_INCREMENT,
  `unit`              varchar(16) DEFAULT NULL,
  `weightbased`       tinyint DEFAULT NULL,      -- -1 = true (Access convention)
  `converttounit`     int DEFAULT NULL,          -- points at ONE other unit
  `convertmultiplier` decimal(10,4) DEFAULT NULL,
  `locked`            tinyint DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `idxUnitNameUNQ` (`unit`)
) ENGINE=InnoDB;
```

All seven rows, with the dimension each one really belongs to:

| id | unit | weightbased | converttounit | convertmultiplier | real dimension |
|---|---|---|---|---|---|
| 1 | unit | 0 | 1 (self) | 1.0000 | count |
| 2 | grams | -1 | 5 (Kg) | 0.0010 | mass |
| 3 | meters | 0 | **NULL** | **NULL** | length |
| 4 | seconds | 0 | **NULL** | **NULL** | time |
| 5 | Kg | -1 | 2 (grams) | 1000.0000 | mass |
| 6 | Box | 0 | 1 (unit) | 1.0000 | packaging |
| 7 | Liter | 0 | **2 (grams)** | **1000.0000** | volume -> mass |

**There is no dimension or unit-group column.** Mass, volume, count, length and time share one flat list, and each row points at exactly one other unit.

---

## 2. How conversion works — one function, one hop

`fnGetUnitConversionFactor` is the **only** object in the database that reads `convertmultiplier`:

```sql
CREATE FUNCTION `fnGetUnitConversionFactor`(prmUnitFrom INT, prmUnitTo INT)
  RETURNS decimal(10,4)
BEGIN
  DECLARE result DECIMAL(10, 4);
  SELECT tun.`convertmultiplier` INTO result
  FROM `production`.`tblunits` tun
  WHERE tun.`id` = prmUnitFrom AND tun.`converttounit` = prmUnitTo;
  IF prmUnitFrom = prmUnitTo THEN SET result = 1; END IF;
  RETURN coalesce(result, 1);
END
```

Two properties dominate everything downstream:

**Single-hop.** It requires one row where `id = from` AND `converttounit = to`. It never chases the chain. grams to Kg works (row 2 points at 5). Kg to grams works (row 5 points at 2). **Box to grams silently fails** because Box only points at `unit`.

**Failure returns 1, not an error.** `coalesce(result, 1)` means any unconvertible pair multiplies by 1 and a wrong number flows onward with no warning. Ask for meters to Kg and the answer is 1.

The VB frontend mirrors the same semantics client-side in `mdlUnits.bas`:

```vb
unitToUnitConversionFactor = Nz(DLookup("[convertmultiplier]", "[tblunits]",
  "[id]=" & unitFrom & " AND converttounit=" & unitTo), 1)
```

---

## 3. Where `unit` columns live

| Table | Column(s) | Enforced FK to `tblunits`? |
|---|---|---|
| `tblproducts` | `unit`, `purchasingunit` | Yes (`fkItemUnit`, `fkItemPurchaseUnit`) |
| `tblproducttree` | `unit` | Yes (`fkItemTreeItemUnit`) |
| `tblnpdproducttree` | `unit` | Yes (`fkNPDitemTreeItemUnit`) |
| `tblstockmovement` | `unitkey` (id), `unit` (varchar label) | Yes on `unitkey` |
| `tblstockcache` | `unit` (varchar name) | **No — joined by string** |
| `tblpoorderspodetails` | `unitordered`, `unitreceived` | **No FK, and no stored procedure reads them** |
| `tblstockmovementbatch` | `unit`, `convertedunit` | **No FK** |
| `tblpoordersordersdetails` (sales) | **none** | Sales lines carry **no unit at all** |

`tblproducts.purchaseshapeformat` points at `tblProductsMappingSupplier`, not at `tblunits`.

---

## 4. Units in stock math

Stock IN and the IN leg of a transfer are the only places quantity is scaled:

```sql
-- procSTKstockIN, only when the destination is NOT a storage container
IF ((SELECT tcn.`storage` FROM tblContainers tcn WHERE tcn.`id` = prmDestCnt) = 0) THEN
  SET prmStkIn = prmStkIn * fnSTKtransactionMultiplier(prmItem, prmShapeFormat, prmSrcCnt, prmDestCnt);
END IF;
```

```sql
-- fnSTKtransactionMultiplier returns:
shapeMultiplier * `fnGetUnitConversionFactor`(
    (SELECT purchasingunit FROM tblproducts WHERE id = prmitem),
    (SELECT unit           FROM tblproducts WHERE id = prmitem))
```

So a supplier pack becomes stock units via `tblProductsMappingSupplier.multiplier` (sample data: `'1 x 20 KG'` -> `20.000000`) times the purchase-to-stock unit factor.

- `procSTKstockOUT` applies **no** multiplier.
- `procSTKstockRECON` applies **no** multiplier.
- `procSTKstockTRANSFER` scales the IN leg only, and clears `shapeformat` when moving storage to non-storage.

**There is no global base unit.** The canonical unit is per product: `tblproducts.unit`. `tblstockcache.unit` holds the product's stock unit *name*, and real queries join it as a string:

```sql
JOIN tblunits ON tblstockcache.unit = tblunits.unit
```

Picking keeps both a raw and a converted quantity, persisted on `tblBOMaggregatePickingLists` as `stockItemRawQuantity`, `stockItemNaturalUnitsQuantity`, `stockItemMultiplier`, `stockItemUnitConversion`:

```sql
IF (isnull(tpm.`shapeFormat`), tsc.`quantity`,
    tsc.`quantity` * coalesce(tpm.`multiplier`, 1)
    * fnGetUnitConversionFactor(tpd.`purchasingunit`, tpd.`unit`)) AS `quantityNaturalUnits`
```

---

## 5. Units in recipe / BOM math

**BOM explosion never converts units.** The math is pure decimal arithmetic:

```sql
gross = parent.grossrequired * pt.`quantity`
          / coalesce(fnSTKgetItemYield(pt.`item`), 1)
          / fnSTKgetItemProcessLoss(pt.`item`)
```

`tblproducttree.unit` exists, has a real FK, and is read by **exactly one** piece of logic — the mixed-unit guard:

```sql
-- fnSTKgetItemProcessLoss
IF (SELECT count(DISTINCT tpt.`unit`) FROM tblproducttree tpt
    WHERE tpt.`parentprod` = prmItem) > 1 THEN
  RETURN 1;   -- give up on process loss
END IF;
SELECT tpv.`processLoss` INTO result FROM tblnpdproducttreeversion tpv
WHERE tpv.`active` = -1 AND tpv.`item` = prmItem;
RETURN coalesce(result, 1);
```

When a recipe mixes units the system does not convert them and does not refuse. It **switches process loss off** and carries on multiplying incompatible numbers. It detects the problem and then hides it, biasing material requirements optimistically.

Batch sizing carries no unit either:

```sql
-- fnGetBatchSizeRawForItem
SELECT grossunitaryweight FROM tblproducts WHERE id = itemid;
-- fnGetBatchSizeFinalForItem
SELECT unitaryweight FROM tblproducts WHERE id = itemid;
```

`grossunitaryweight`, `unitaryweight`, `packweight`, `unitsPerBatch` and `itemsperunit` are implicitly in `tblproducts.unit`. Nothing records or checks that.

---

## 6. The bullshit, plainly

1. **`weightbased` is dead.** It appears in the DDL and in a fully commented-out `tblunits_BEFORE_UPDATE` trigger. Zero calculations read it. The one column that could have prevented cross-dimension nonsense is decorative.
2. **`coalesce(..., 1)` on a failed conversion** is the single most dangerous line in the system. Wrong totals, no error.
3. **`Liter -> grams @ 1000` hardcodes density 1.0 g/ml.** True for water; wrong for oil, syrup, cream, batter. Density belongs on the product, never on the unit table.
4. **`Box -> unit @ 1`** asserts one box equals one item. Real box contents come from the supplier shape multiplier, so the unit row is misleading.
5. **`decimal(10,4)` on the multiplier** cannot express small factors (grams to tonnes is 0.000001, which rounds to 0.0000 and zeroes any product of it) and rounds imperial factors such as oz to g (28.3495).
6. **`meters` and `seconds` have NULL conversion**, so any attempt returns factor 1.
7. **Mass pair points at itself both ways** (grams -> Kg and Kg -> grams). Not a runtime loop, because nothing recurses, but the factor is direction-dependent and the system will not auto-invert. Pick the wrong direction and you are out by 1,000,000x.
8. **`tblstockcache.unit` joined by name string** rather than id.
9. **Sales lines have no unit column**; analytics sum `quantityOrdered` across products with different UoM.
10. **PO unit columns are orphaned** — no FK, no stored procedure reads them; receiving logic likely lives only in Access forms.
11. **`fnSTKtransactionShapeFormatForStockTuple` is broken** — its body contains `SET shapeMultiplier = (production.tblProductsMappingSupplier)`, an invalid table expression. Do not port it.

---

## 7. The new system has already lost this

Live `DB_LOCATIONS.product_unit`:

```
id    int
name  varchar(64)
```

Same seven rows, but `weightbased`, `converttounit` and `convertmultiplier` were **dropped during the product migration**. The new system currently cannot convert anything at all, and `recipe_component.unit_id` references a table with no conversion semantics.

This lands directly on the BOM engine: explosion multiplies component quantities with no way to establish that they are commensurable.

---

## 8. NotaZone's model (the right shape)

From `notazoneguidetoaddingproductsandlocations-v10.md`:

> **Unit Group:** How it's going to be stored / used (weight/liquid/count).
> **Unit:** What unit will it be recorded in (g / kg, ml / L, cans / boxes / pallets, etc.)
> **Precision:** How many places after the decimal place you want to see.

> "When a product is set to Unit Group - Count, the default unit for Count products is 'of'... You can enter a (Count) Unit to reflect what you are counting the product in (e.g. 'labels', or 'rolls' of labels)."

From `notazoneguidetorecipes-v6.md`:

> "You cannot change a unit (e.g. count to kg) in the recipe editor. If you need to change between count, weight and volume, this is done under advanced settings for that product."

Three things NotaZone gets right that the legacy schema does not: an explicit **unit group** (dimension), a **precision** per unit, and user-extensible **count units** — plus a hard rule that changing dimension is a product-level decision, never a recipe-line one.

---

## 9. Target model for the new system

```sql
CREATE TABLE unit_dimension (
  id           INT NOT NULL AUTO_INCREMENT,
  code         VARCHAR(16) NOT NULL,   -- mass | volume | count | length | time
  name         VARCHAR(32) NOT NULL,
  base_unit_id INT NULL,               -- canonical unit for this dimension
  PRIMARY KEY (id),
  UNIQUE KEY uq_unit_dimension_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE product_unit (
  id                INT NOT NULL AUTO_INCREMENT,
  code              VARCHAR(16) NOT NULL,
  name              VARCHAR(64) NOT NULL,
  unit_dimension_id INT NOT NULL,
  factor_to_base    DECIMAL(24,12) NOT NULL,  -- grams=1, Kg=1000, tonne=1000000
  display_precision TINYINT NOT NULL DEFAULT 3,
  is_active         TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  UNIQUE KEY uq_product_unit_code (code),
  KEY idx_product_unit_dimension (unit_dimension_id),
  CONSTRAINT fk_product_unit_dimension
    FOREIGN KEY (unit_dimension_id) REFERENCES unit_dimension (id),
  CONSTRAINT chk_product_unit_factor CHECK (factor_to_base > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

Seed values:

| dimension | unit | factor_to_base |
|---|---|---|
| mass (base: gram) | gram | 1 |
| mass | kilogram | 1000 |
| mass | tonne | 1000000 |
| volume (base: millilitre) | millilitre | 1 |
| volume | litre | 1000 |
| count (base: each) | each | 1 |
| count | box | (per product, via pack format) |
| length (base: metre) | metre | 1 |
| time (base: second) | second | 1 |

### The four rules that fix the specific failures above

1. **Factor to a base unit, not to a neighbour.** Any pair converts as `from.factor_to_base / to.factor_to_base`. No hops, no chains, no direction-dependent rows, no grams and Kg pointing at each other. Fixes issues 1, 7 and the Box-to-grams gap.
2. **Conversion is legal only within one dimension.** Mismatched `unit_dimension_id` raises an exception. **Never return 1 on failure.** Fixes issues 2 and 6.
3. **Volume to mass requires density on the product**, not on the unit. Add `product.density_g_per_ml DECIMAL(10,6) NULL`. Converting litres of oil to grams without it is an error the operator resolves, not a silent 1.0 assumption. Fixes issue 3.
4. **`DECIMAL(24,12)`** so tonnes and micrograms coexist and imperial factors do not round. Fixes issue 5.

### Additional rules

- **Pack quantities belong to the pack, not the unit table.** "Box" is a container, not a conversion. Keep supplier/purchase pack size on a purchase-format row (legacy `tblProductsMappingSupplier.multiplier`), expressed in the product's stock unit. Fixes issue 4.
- **Store unit ids, never names.** No string joins anywhere. Fixes issue 8.
- **Every quantity column gets a unit FK.** Including sales lines, which have none today. Fixes issue 9 and 10.
- **Persist the base-unit quantity alongside the entered quantity** on stock movements, so ledger arithmetic and aggregation never depend on runtime conversion.
- **Validate dimensional consistency at recipe save time** — reject a component whose unit dimension does not match the product's stock unit dimension. This replaces the legacy mixed-unit guard, which detected the same condition and responded by disabling process loss.
- **Batch size fields need an explicit unit.** `gross_unitary_weight` and friends are currently implicitly in the product's stock unit; make that explicit or keep them strictly base-unit.

---

## 10. Why this blocks the BOM engine

The explosion formula multiplies a parent's gross requirement by a component quantity:

```
net   = parent_gross * component_quantity / yield_factor
gross = net / process_loss
```

That product is only meaningful if the two operands are commensurable. Legacy never checked, and switched process loss off when it noticed a mismatch. If the new engine is built on a `product_unit` table with no dimension and no factor, it inherits exactly the same defect with none of the legacy warning signs.

Recommended sequencing: fix the unit model **before** applying the BOM DDL, i.e. as a Phase 1.5 between the recipe constraint fixes and the BOM tables in [`.cursor/plans/recipe_bom_working_plan_7b32ebc1.plan.md`](.cursor/plans/recipe_bom_working_plan_7b32ebc1.plan.md).

---

## 11. Migration notes

- Legacy unit ids 1-7 are referenced by real FKs on `tblproducts`, `tblproducttree` and `tblnpdproducttree`. Preserve the ids when backfilling so recipe and product backfills stay valid.
- `Liter` currently converts to grams at 1000. On migration it becomes a **volume** unit with `factor_to_base = 1000` (base millilitre). Any product that relied on the implicit density-1.0 conversion needs `density_g_per_ml` populated, or its stock unit corrected. **Expect this to surface real data problems — that is the point.**
- `Box` becomes a count unit with `factor_to_base = 1`. Actual pack contents move to the purchase-format multiplier.
- `meters` and `seconds` become length and time. Nothing in the calculation path uses them; keep them so existing product rows stay valid.
- Legacy `weightbased` is not migrated. It is superseded by `unit_dimension`.
