# GAZEBO — `tblproducts` Normalization Plan

Target: split the 97-column `tblproducts` god-table into a normalized, industry-standard schema ready for Django ORM, without breaking the ~330 places in the database that reference it.

All findings below come from direct inspection of `data/Dump20260720.sql`.

---

## 1. Dependency inventory — what actually touches `tblproducts`

| Dependency type | Count | Notes |
|---|---|---|
| Total references to `tblproducts` in the dump | 663 | Every mention |
| `FROM` / `JOIN` / `UPDATE` statements against it | 330 | The real query surface |
| Triggers **on** `tblproducts` | 5 | 2 active, 3 mostly commented out (see §1.1) |
| Enforced FKs **out of** `tblproducts` | 8 | To Categories, Class, Containers ×2, MappingSupplier, Units ×2, Range |
| Enforced FKs **into** `tblproducts.id` | 10 (across 8 tables) | ProductsMappingCustomer, ProductsVersions, npdproducttree ×2, npdproducttreeversion, npdreceipejournal, poordersForecastsDetails, producttree ×2, stockmovement |
| Tables referencing it **implicitly (no FK)** | ~30 | Via `item` / `childitem` / `parentprod` columns — the real risk |
| Views in the schema | 86 | Many join `tblproducts` |
| Single-column accessor functions | 12+ | Listed in §1.2 — these are the cheapest thing to redirect |

### 1.1 Triggers on `tblproducts`

| Trigger | Status | What it does |
|---|---|---|
| `tblproducts_AFTER_INSERT` | **ACTIVE** | Audit log via `procSYSlogActivity` |
| `tblproducts_AFTER_DELETE` | **ACTIVE** | Audit log via `procSYSlogActivity` |
| `tblproducts_AFTER_UPDATE` | **ACTIVE — critical** | Two denormalization cascades (see below) |
| `tblproducts_BEFORE_UPDATE` | Body fully commented out | Was: block renaming an item that has stock transactions |
| `tblproducts_BEFORE_DELETE` | Body fully commented out | Was: block deleting an item that has transactions |

**`tblproducts_AFTER_UPDATE` is the single most important object in this whole exercise.** It does two things:

1. **Yield cascade** — if `productyield` changes AND `fnItemYieldCountAcrossTrees(id) = 1`, it pushes the new yield down into `tblproducttree.productyield` and `tblnpdproducttree.productyield`. This directly feeds the BOM engine's gross/net requirement math (Manual 2). Any normalization that moves `productyield` must preserve this behaviour exactly, or recipe calculations silently drift.
2. **Name cascade** — if `productname` changes, it rewrites the denormalized `itemname`/`productname` copies held in **5 other tables**: `tblstockcache`, `tblstockcachearchive`, `tblstockmovement`, `tblplanmasterbomdetails`, `tblplanmasterbomaggregate`.

> The equivalent `productreceipecode` cascade exists in the file but is **commented out** — meaning recipe-code copies in those same 5 tables can currently drift out of sync with the master record. Worth confirming with the business whether that was deliberate; it's a latent data-quality issue independent of this migration.

### 1.2 Single-column accessor functions (direct column dependencies)

Each of these is a one-line `SELECT <column> FROM tblproducts WHERE id = ?`. When a column moves to a satellite table, only these need redirecting — cheap, mechanical, and a good first migration step:

| Function | Column read | Moves to |
|---|---|---|
| `fnGetProductName` | `productname` | `product` |
| `fnGetProductReceipeCode` | `productreceipecode` | `product` |
| `fnGetProductRange` | `range` | `product` |
| `fnGetItemYield` | `productyield` | `product_yield` |
| `fnGetItemCost` | `unitcost` | `product_costing` |
| `fnGetItemPrice` | `unitprice` | `product_costing` |
| `fnGetBatchSizeFinalForItem` | `unitaryweight` | `product_packaging` |
| `fnGetBatchSizeRawForItem` | `grossunitaryweight` | `product_packaging` |
| `fnGetItemMinimumShelfLife` | `absoluteminimumshelflife` | `product_shelf_life` |
| `fnGetSrcContainerIDForItem` | `srccontainer` | `product` |
| `fnGetDestContainerForItem` / `...IDForItem` | `destcontainer` | `product` |
| `fnGetIsItemDispatchSupport` | `genIsDispatchSupport` | `product_flags` |
| `fnGetItemDefaultResource` | `genDefaultResource` | `product_production` |
| `fnItemYieldCountAcrossTrees` | (recipe trees) | unchanged |

Heavier consumers that read many columns at once: `procItemFormItemListing`, `procUpdateProductsRates` (writes 6 rate columns), `procCalculateItemYield`, `procSTKstockIN/OUT/RECON`, the whole `BOMrecursive*` pipeline, `procSTKplanPickingListWithStock*`, `fnSTKtransactionMultiplier`.

---

## 2. Target schema — naming conventions

Adopt these consistently (they're what Django and the wider industry expect):

- `snake_case`, no `tbl` prefix, **singular** table names (Django convention: model `Product` → table `product`).
- Primary key: always `id`.
- Foreign keys: `<referenced_table>_id` (e.g. `product_id`, `unit_id`) — Django generates exactly this.
- Booleans: `is_` / `has_` prefix, real `BOOLEAN`, not `tinyint` with `-1`/`0` semantics.
- Timestamps: `created_at` / `updated_at`.
- Every FK **declared and enforced** — no more implicit conventions.
- Fix legacy misspellings: `receipe` → `recipe`, `categorypatth` → `category`.

### 2.1 Target tables

| New table | Purpose | Approx. columns |
|---|---|---|
| `product` | Core identity + classification + the FK-heavy relationships | ~20 |
| `product_costing` | Cost, price, nominal code, lead time | ~6 |
| `product_stock_policy` | Reorder level, min/max/clear stock levels | ~5 |
| `product_shelf_life` | All shelf-life variants + date-forcing rules | ~8 |
| `product_packaging` | Weights, tray/box/vessel, items per unit, gas flush | ~14 |
| `product_production` | Rates, dwell/gap times, default resource, batch sizing | ~12 |
| `product_yield` | Yield, auto-yield, chilling loss factor | ~4 |
| `product_flags` | The ~18 `gen*` behavioural booleans | ~18 |
| `product_technical` | Temperature checks, GMO, spec sign-off/review dates | ~7 |
| `product_audit` | `user`, `lanusername`, `srcworkstation`, `srcworkstationip` | ~4 |

Everything below `product` is a **1:1 satellite** keyed on `product_id` — so a query needing only core fields never pays for the wide row, and each concern can evolve independently.

---

## 3. Column-by-column mapping (all 97)

### → `product` (core)
`id`, `productname`→`name`, `alternateproductname`→`alternate_name`, `productreceipecode`→`recipe_code`, `alternateproductreceipecode`→`alternate_recipe_code`, `gffCode`→`gff_code`, `secondaryGFFReceipe`→`secondary_gff_recipe`, `externalbarcode`→`external_barcode`, `active`→`is_active`, `downtime`→`is_downtime`, `productclass`→`product_class_id`, `range`→`range_id`, `subrange`→`sub_range_id`, `categorypatth`→`category_id`, `srccontainer`→`source_container_id`, `destcontainer`→`destination_container_id`, `unit`→`unit_id`, `purchasingunit`→`purchasing_unit_id`, `purchaseshapeformat`→`purchase_shape_format_id`, `purchasingversion`→`purchasing_version`, `ingredientcount`→`ingredient_count`, `remarks`, `inserttime`→`created_at`, `updatetime`→`updated_at`

### → `product_costing`
`unitcost`→`unit_cost`, `unitprice`→`unit_price`, `nominalcode`→`nominal_code`, `casesizedescription`→`case_size_description`, `leadtime`→`lead_time_days`

### → `product_stock_policy`
`reorderlevel`→`reorder_level`, `minstock`→`min_stock`, `maxstock`→`max_stock`, `clearstocklevel`→`clear_stock_level`

### → `product_shelf_life`
`shelflife`→`shelf_life_days`, `shelflifeintrinsic`→`shelf_life_intrinsic_days`, `shelflifedepot`→`shelf_life_depot_days`, `absoluteminimumshelflife`→`absolute_min_shelf_life_days`, `forceproductiondate`→`force_production_date`, `forcetracenumber`→`force_trace_number`, `forceuseby`→`force_use_by`

### → `product_packaging`
`packweight`→`pack_weight`, `unitaryweight`→`unitary_weight`, `grossunitaryweight`→`gross_unitary_weight`, `unitaryweightalign`→`align_unitary_weight`, `defaultlength`→`default_length`, `itemsperunit`→`items_per_unit`, `unitsPerTray`→`units_per_tray`, `unitsPerBatch`→`units_per_batch`, `containerVessel`→`container_vessel_id`, `tray`→`tray_id`, `box`→`box_id`, `gasflush`→`is_gas_flush`, `packagingType`→`packaging_type_id`, `physicalState`→`physical_state_id`, `deliveryState`→`delivery_state_id`

### → `product_production`
`rateavgrunsize`→`avg_run_size`, `rateavgminutes`→`avg_minutes`, `rateavgproduct`→`avg_rate_product`, `rateavgstaffminunit`→`avg_staff_min_per_unit`, `rateavgstaffminute`→`avg_staff_per_minute`, `rateavgrange`→`avg_rate_range`, `averagerate`→`average_rate`, `unitarygaptime`→`unitary_gap_time`, `unitarydwelltime`→`unitary_dwell_time`, `relativePlanPosition`→`relative_plan_position`, `genDefaultResource`→`default_resource_id`

### → `product_yield`
`productyield`→`yield_factor`, `productyieldauto`→`yield_factor_auto`, `factorChillingLoss`→`chilling_loss_factor`

### → `product_flags` (all → `BOOLEAN`)
`genstocklist`→`in_stock_list`, `genautoyield`→`auto_yield`, `gengotplan`→`has_plan`, `genautorate`→`auto_rate`, `genautoclearstock`→`auto_clear_stock`, `genconsiderstockinplan`→`consider_stock_in_plan`, `genhasreceipe`→`has_recipe`, `genfullbatches`→`full_batches_only`, `genautotrends`→`auto_trends`, `genimplicit`→`is_implicit`, `genIncludeInProjections`→`include_in_projections`, `genrecordflag`→`record_flag`, `genHasChillingLoss`→`has_chilling_loss`, `genUseBatchQuantity`→`use_batch_quantity`, `genFreezerNoDeduct`→`freezer_no_deduct`, `genPurchaseItem`→`is_purchase_item`, `genSalesItem`→`is_sales_item`, `genIsDispatchSupport`→`is_dispatch_support`

### → `product_technical`
`technicalGMOFree`→`is_gmo_free`, `technicalSpecSignOffDate`→`spec_sign_off_date`, `technicalNextReviewDate`→`next_review_date`, `technicalrequirestemperaturecheck`→`requires_temperature_check`, `technicallowerboundtemperaturecheck`→`temp_check_lower_bound`, `technicalupperboundtemperaturecheck`→`temp_check_upper_bound`

### → `product_audit`
`user`→`created_by_user_id`, `lanusername`→`lan_username`, `srcworkstation`→`source_workstation`, `srcworkstationip`→`source_workstation_ip`

> Note: `product_audit` is a transitional table. Per Manual 8, workstation-IP tracking should ultimately be replaced by proper device identity (`device_id`); keep this table during migration so nothing is lost, then retire it.

---

## 4. Migration strategy — the compatibility view

The safest sequence, and the reason this can be done without a big-bang cutover:

**Step 1 — Build alongside.** Create all 10 new tables. Do not touch `tblproducts`.

**Step 2 — Backfill.** Copy data across in one transaction. Verify row counts and checksum a sample of columns against the source.

**Step 3 — Compatibility view.** Rename `tblproducts` → `tblproducts_legacy`, then create a **view** named `tblproducts` that reconstructs the original 97-column shape by joining the new tables. **All 330 existing queries, 86 views, and every stored procedure keep working untouched.** This is what makes the migration incremental instead of all-or-nothing.

**Step 4 — Redirect writers.** Views are only updatable under narrow conditions and a 10-table join view will not be. So every routine that **writes** to `tblproducts` must be redirected to the new tables in this step — principally `procUpdateProductsRates`, plus the trigger cascades. Readers (the large majority) need no change.

**Step 5 — Recreate triggers.** Move the two active cascades onto the new tables: the yield cascade onto `product_yield`, the name cascade onto `product`. Audit-log triggers move onto `product`.

**Step 6 — Migrate readers gradually.** Point routines at the real tables module by module, following the build sequence (catalog first). The compatibility view stays until the last reader is migrated.

**Step 7 — Drop the view and `tblproducts_legacy`** once nothing references them.

---

## 5. Django ORM models

```python
class Product(models.Model):
    name = models.CharField(max_length=128, unique=True)
    recipe_code = models.CharField(max_length=32, unique=True, null=True)
    alternate_name = models.CharField(max_length=128, null=True, blank=True)
    external_barcode = models.CharField(max_length=16, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_downtime = models.BooleanField(default=False)
    product_class = models.ForeignKey('ProductClass', on_delete=models.PROTECT)
    category = models.ForeignKey('Category', on_delete=models.PROTECT)
    range = models.ForeignKey('Range', on_delete=models.PROTECT)
    sub_range = models.ForeignKey('SubRange', on_delete=models.PROTECT, null=True)
    unit = models.ForeignKey('Unit', on_delete=models.PROTECT, related_name='products')
    purchasing_unit = models.ForeignKey('Unit', on_delete=models.PROTECT,
                                        null=True, related_name='purchased_products')
    source_container = models.ForeignKey('Container', on_delete=models.PROTECT,
                                         related_name='source_products')
    destination_container = models.ForeignKey('Container', on_delete=models.PROTECT,
                                              related_name='destination_products')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'product'


class ProductYield(models.Model):
    product = models.OneToOneField(Product, on_delete=models.CASCADE,
                                   primary_key=True, related_name='yield_data')
    yield_factor = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal('1.0000'))
    yield_factor_auto = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal('1.0000'))
    chilling_loss_factor = models.DecimalField(max_digits=10, decimal_places=4, null=True)

    class Meta:
        db_table = 'product_yield'


class ProductCosting(models.Model):
    product = models.OneToOneField(Product, on_delete=models.CASCADE,
                                   primary_key=True, related_name='costing')
    unit_cost = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal('0'))
    unit_price = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal('0'))
    nominal_code = models.CharField(max_length=4, null=True, blank=True)
    lead_time_days = models.IntegerField(null=True)

    class Meta:
        db_table = 'product_costing'
```

Same `OneToOneField(primary_key=True)` pattern for the remaining satellites. Two rules that matter:

- **Always `DecimalField`, never `FloatField`** — matches the legacy `DECIMAL(16,6)` / `DECIMAL(10,4)` precision. This is the same non-negotiable rule from the main migration plan; a `FloatField` here silently corrupts stock and BOM math.
- Use `select_related()` when a query needs satellite data, so the 1:1 splits don't turn into N+1 queries.

---

## 6. Execution plan

| Phase | Work | Verification |
|---|---|---|
| 0 | Log which routines actually run in production (the Phase 0 instrumentation from the main plan) | Know the real reader/writer set before changing anything |
| 1 | Create the 10 tables + Django models | Migrations apply cleanly on a copy |
| 2 | Backfill + reconcile | Row counts match; sampled columns match source exactly |
| 3 | Compatibility view | All 86 views and every stored procedure still return identical results |
| 4 | Redirect writers (`procUpdateProductsRates` + triggers) | Rate recalculation produces identical values to legacy |
| 5 | Recreate trigger cascades | Change a yield → confirm it still propagates to both recipe trees; change a name → confirm all 5 denormalized copies update |
| 6 | Add the ~30 missing FK constraints | Each `ALTER TABLE ... ADD CONSTRAINT` succeeds — **any failure is a pre-existing orphan record that must be cleaned first** |
| 7 | Migrate readers module by module | Per-module regression against legacy output |
| 8 | Drop view + `tblproducts_legacy` | Nothing references them |

**Phase 6 deserves emphasis:** adding those FKs is the step that converts ~30 implicit, unenforced relationships into real database-guaranteed integrity. Expect some to fail on first attempt — that failure *is* the finding, and it's better discovered now than after go-live.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| A 10-table join view is not updatable — writes break silently if missed | Phase 4 explicitly inventories and redirects every writer before the view goes live |
| Yield cascade trigger lost during the move → BOM math drifts | Phase 5 verification: change a yield, confirm both recipe trees update |
| Name cascade lost → 5 denormalized tables drift | Same phase, explicit test on all 5 tables |
| Adding FKs fails on existing orphan data | Expected — run as a discovery step, clean orphans first |
| Performance regression from 1:1 joins | Satellites are keyed on PK (fast); use `select_related()`; only core fields load by default |
| The commented-out `productreceipecode` cascade | Confirm with the business whether recipe-code drift is intentional before replicating current behaviour |

