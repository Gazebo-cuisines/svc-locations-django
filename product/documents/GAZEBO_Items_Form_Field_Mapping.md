# GAZEBO — Items Form → Legacy Column → New Schema Mapping

Traces every field on the legacy Access **Items** form to its column in
`data/Dump20260720.sql`, and to its destination in the new Django service at
`gazeboo-cloud/microservice/svc-locations-django`.

Worked example throughout: **British Marinated Chicken - 164 - Steaming**
(`tblproducts.id = 2829` — the `2829` shown in the form's Search ID box).

---

## 1. Short answer

Every scalar field on the Items form is a column of one table: **`tblproducts`**
(dump line 9931, 97 columns, 2,383 rows). Nothing on the main form body is
computed or spread across tables — it is a classic god-table.

The new schema splits those 97 columns across **11 tables** in the `product` app.
Coverage is complete: all 97 columns have a destination. Five defects need
fixing before import — §5.

---

## 2. Legacy source

| | |
|---|---|
| Table | `tblproducts` |
| Dump location | `data/Dump20260720.sql` line 9931 |
| Columns | 97 |
| Rows | 2,383 |
| PK | `id` (AUTO_INCREMENT, next 3551) |
| Enforced FKs out | 8 → `tblCategories`, `tblProductsClass`, `tblContainers` ×2, `tblProductsMappingSupplier`, `tblunits` ×2, `tblrange` |

Lookups resolved for the worked example:

| Column | Value | Resolves via | To |
|---|---|---|---|
| `productclass` | 3 | `tblProductsClass` | Cooked Items |
| `range` | 14 | `tblrange` | Protein |
| `categorypatth` | 151 | `tblCategories` | `Low Risk > Steam > Chicken` |
| `srccontainer` | 223 | `tblContainers` | Steaming |
| `destcontainer` | 4 | `tblContainers` | High Risk |
| `unit` | 2 | `tblunits` | grams |

---

## 3. Complete field mapping

`Fill%` = share of the 2,383 rows where the column is non-NULL. Use it to
prioritise — a 0.0% column carries no legacy data to migrate.

### 3.1 Identity — top-left block

| Form label | Legacy column | Example value | New destination | Fill% |
|---|---|---|---|---|
| Internal – Item Name | `productname` | British Marinated Chicken - 164 - Steaming | `Product.name` | 100.0 |
| Alternate – Item Name | `alternateproductname` | British Marinated Chicken - Steaming | `Product.alternate_name` | 100.0 |
| Internal – Item Recipie code | `productreceipecode` | `GFF164R - St` | `Product.recipe_code` | 95.2 |
| Alternate – Item Recipie code | `alternateproductreceipecode` | `GFF164R-St` | `Product.alternate_recipe_code` | 91.7 |
| GFF Internal Code | `gffCode` | NULL | `Product.gff_code` | 20.7 |
| GFF Rcp | `secondaryGFFReceipe` | NULL | `Product.secondary_gff_recipe` | 2.8 |
| Item Barcode | `externalbarcode` | NULL | `Product.external_barcode` | 12.8 |
| Active (checkbox) | `active` | -1 (true) | `Product.is_active` | 100.0 |
| Remarks | `remarks` | NULL | `Product.remarks` | 0.8 |
| — (not on form) | `downtime` | 0 | `Product.is_downtime` | 100.0 |

### 3.2 Classification & routing

| Form label | Legacy column | Example | New destination | Fill% |
|---|---|---|---|---|
| Container OUT From | `srccontainer` | 223 → Steaming | `Product.source_container` | 100.0 |
| Container IN To | `destcontainer` | 4 → High Risk | `Product.destination_container` | 100.0 |
| Item Class | `productclass` | 3 → Cooked Items | `Product.product_class` | 100.0 |
| Item Range | `range` | 14 → Protein | `Product.range` | 100.0 |
| Categories tab | `categorypatth` | 151 → Low Risk > Steam > Chicken | `Product.category` | 100.0 |
| — (unused) | `subrange` | NULL | `Product.sub_range` | **0.0** |
| Unit | `unit` | 2 → grams | `Product.unit` | 100.0 |
| — (purchasing) | `purchasingunit` | NULL | `Product.purchasing_unit` | 21.7 |
| — (purchasing) | `purchaseshapeformat` | NULL | `Product.purchase_shape_format` | 19.3 |
| — (purchasing) | `purchasingversion` | NULL | `Product.purchasing_version` | 0.1 |

### 3.3 Yield

| Form label | Legacy column | Example | New destination | Fill% |
|---|---|---|---|---|
| Yield | `productyield` | 0.8500 | `ProductYield.yield_factor` | 99.0 |
| Auto Yield (checkbox) | `genautoyield` | 0 | `ProductFlags.auto_yield` | 100.0 |
| Auto Yield (value) | `productyieldauto` | NULL | `ProductYield.yield_factor_auto` | 4.7 |
| "Place Holder - Dtl - 000" | `factorChillingLoss` | NULL | `ProductYield.chilling_loss_factor` | **0.0** |
| — | `genHasChillingLoss` | 0 | `ProductFlags.has_chilling_loss` | 100.0 |

### 3.4 Batch & pack

| Form label | Legacy column | Example | New destination | Fill% |
|---|---|---|---|---|
| Pack Size / Weight | `packweight` | NULL | `ProductPackaging.pack_weight` | 31.9 |
| Case Size | `casesizedescription` | NULL | `ProductCosting.case_size_description` | 20.3 |
| **Net Batch Size** | `unitaryweight` | 131107.383 | `ProductPackaging.unitary_weight` | 18.6 |
| **Gross Batch Size** | `grossunitaryweight` | 155802.000 | `ProductPackaging.gross_unitary_weight` | 15.2 |
| Items Per Unit | `itemsperunit` | 1 | `ProductPackaging.items_per_unit` | 79.7 |
| Component Count | `ingredientcount` | 1 | `Product.ingredient_count` | 40.2 |
| Units Per Tray | `unitsPerTray` | NULL | `ProductPackaging.units_per_tray` | **0.0** |
| Units Per Batch | `unitsPerBatch` | NULL | `ProductPackaging.units_per_batch` | **0.0** |
| Tray / Box / Vessel - Floor Name | `containerVessel` | NULL | `ProductPackaging.container_vessel` | 13.3 |
| "Place Holder - 000" | `tray` | NULL | `ProductPackaging.tray` | 13.4 |
| "Place Holder - 000" | `box` | NULL | `ProductPackaging.box` | **0.0** |
| — | `gasflush` | 0 | `ProductPackaging.is_gas_flush` | 100.0 |
| — | `unitaryweightalign` | 0 | `ProductPackaging.align_unitary_weight` | 100.0 |
| "Place Holder" | `packagingType` | NULL | `ProductPackaging.packaging_type` | **0.0** |
| "Place Holder" | `physicalState` | NULL | `ProductPackaging.physical_state` | **0.0** |
| "Place Holder" | `deliveryState` | NULL | `ProductPackaging.delivery_state` | **0.0** |

`131107.383 = 155802.000 × 0.8415` — gross × tree-line yield = net. Note the
0.8415 comes from the **recipe tree line**, not from `productyield` (0.8500). See §5.5.

### 3.5 Pricing

| Form label | Legacy column | Example | New destination | Fill% |
|---|---|---|---|---|
| Unit Price | `unitprice` | 0.0000 | `ProductCosting.unit_price` | 63.4 |
| Unit Cost | `unitcost` | 0.0000 | `ProductCosting.unit_cost` | 98.1 |
| Nominal Code | `nominalcode` | NULL | `ProductCosting.nominal_code` | 15.9 |

### 3.6 Shelf life

| Form label | Legacy column | Example | New destination | Fill% |
|---|---|---|---|---|
| Internal Shelf Life - Days | `shelflife` | 3 | `ProductShelfLife.shelf_life_days` | 71.3 |
| Intrinsic Shelf Life - Days | `shelflifeintrinsic` | NULL | `ProductShelfLife.shelf_life_intrinsic_days` | **0.0** |
| Depot Shelf Life - Days | `shelflifedepot` | NULL | `ProductShelfLife.shelf_life_depot_days` | 13.8 |
| Minimum Shelf Life - Days | `absoluteminimumshelflife` | NULL | `ProductShelfLife.absolute_min_shelf_life_days` | 2.1 |
| — | `forceproductiondate` | -1 | `ProductShelfLife.force_production_date` | 100.0 |
| — | `forcetracenumber` | 0 | `ProductShelfLife.force_trace_number` | 100.0 |
| — | `forceuseby` | -1 | `ProductShelfLife.force_use_by` | 100.0 |

### 3.7 Stock policy — bottom row of the form

| Form label | Legacy column | Example | New destination | Fill% |
|---|---|---|---|---|
| Stock Reorder Time - Days | `leadtime` | NULL | `ProductCosting.lead_time_days` ⚠️ | 0.8 |
| Reorder Level - Units | `reorderlevel` | NULL | `ProductStockPolicy.reorder_level` | 0.8 |
| Minimum Stock Level - Units | `minstock` | NULL | `ProductStockPolicy.min_stock` | **0.0** |
| Maximum Stock Level - Units | `maxstock` | NULL | `ProductStockPolicy.max_stock` | 0.8 |
| Clear Stock Residuals Level - Units | `clearstocklevel` | NULL | `ProductStockPolicy.clear_stock_level` | **0.0** |

⚠️ `leadtime` sits in `ProductCosting` but the form groups it with the stock
block. Consider moving to `ProductStockPolicy` — see §5.4.

### 3.8 Production & planning

| Form label | Legacy column | Example | New destination | Fill% |
|---|---|---|---|---|
| Production Gap Time - Minutes | `unitarygaptime` | 0 | `ProductProduction.unitary_gap_time` | 88.3 |
| Production Dwell Time - Minutes | `unitarydwelltime` | 0 | `ProductProduction.unitary_dwell_time` | 85.4 |
| **Default Execution Time - Minutes** | `defaultlength` | 0 | `ProductPackaging.default_length` ⚠️ | 98.1 |
| Relative Position in Plan | `relativePlanPosition` | NULL | `ProductProduction.relative_plan_position` | **0.0** |
| Default Production Resource | `genDefaultResource` | NULL | `ProductProduction.default_resource_id` | 25.5 |
| Average Run Size (header) | `rateavgrunsize` | 56850.0000 | `ProductProduction.avg_run_size` | 39.7 |
| Average Rate (header) | `rateavgminutes` | 25.0000 | `ProductProduction.avg_minutes` | 31.6 |
| Rates tab | `rateavgproduct` | 2274.0000 | `ProductProduction.avg_rate_product` | 24.3 |
| Rates tab | `averagerate` | 2274.0000 | `ProductProduction.average_rate` | 55.1 |
| Rates tab | `rateavgstaffminunit` | NULL | `ProductProduction.avg_staff_min_per_unit` | 7.1 |
| Rates tab | `rateavgstaffminute` | NULL | `ProductProduction.avg_staff_per_minute` | 7.1 |
| Rates tab | `rateavgrange` | NULL | `ProductProduction.avg_rate_range` | **0.0** |

⚠️ `defaultlength` is misfiled under packaging — see §5.3.

The header box labelled **"Average Rate"** is bound to `rateavgminutes`
(25.00), not to `averagerate` (2274.00). The label is misleading in the legacy
form; `56850 / 25 = 2274` confirms the binding. Do not carry the label over.

### 3.9 Item Flags - Attributes tab — all 18 `gen*` booleans

All are 100% populated and land in `ProductFlags`:

| Legacy | New | Legacy | New |
|---|---|---|---|
| `genstocklist` | `in_stock_list` | `genimplicit` | `is_implicit` |
| `genautoyield` | `auto_yield` | `genIncludeInProjections` | `include_in_projections` |
| `gengotplan` | `has_plan` | `genrecordflag` | `record_flag` |
| `genautorate` | `auto_rate` | `genHasChillingLoss` | `has_chilling_loss` |
| `genautoclearstock` | `auto_clear_stock` | `genUseBatchQuantity` | `use_batch_quantity` |
| `genconsiderstockinplan` | `consider_stock_in_plan` | `genFreezerNoDeduct` | `freezer_no_deduct` |
| `genhasreceipe` | `has_recipe` | `genPurchaseItem` | `is_purchase_item` |
| `genfullbatches` | `full_batches_only` | `genSalesItem` | `is_sales_item` |
| `genautotrends` | `auto_trends` | `genIsDispatchSupport` | `is_dispatch_support` |

### 3.10 Technical & audit

| Legacy column | New destination | Fill% |
|---|---|---|
| `technicalGMOFree` | `ProductTechnical.is_gmo_free` | 100.0 |
| `technicalSpecSignOffDate` | `ProductTechnical.spec_sign_off_date` | **0.0** |
| `technicalNextReviewDate` | `ProductTechnical.next_review_date` | **0.0** |
| `technicalrequirestemperaturecheck` | `ProductTechnical.requires_temperature_check` | 100.0 |
| `technicallowerboundtemperaturecheck` | `ProductTechnical.temp_check_lower_bound` | 100.0 |
| `technicalupperboundtemperaturecheck` | `ProductTechnical.temp_check_upper_bound` | 100.0 |
| `user` | `ProductAudit.created_by_user_id` | 0.8 |
| `lanusername` | `ProductAudit.lan_username` | 0.8 |
| `srcworkstation` | `ProductAudit.source_workstation` | 0.8 |
| `srcworkstationip` | `ProductAudit.source_workstation_ip` | 0.8 |
| `inserttime` | `Product.created_at` ⚠️ | 100.0 |
| `updatetime` | `Product.updated_at` ⚠️ | 100.0 |

⚠️ Both timestamp columns are 100% populated but currently unimportable — §5.2.

---

## 4. The "Place Holder" boxes are real fields

The form shows several boxes labelled *Place Holder*, *Place Holder - 000*,
*Place Holder - Dtl - 00n*. Reading the control names out of
`GAZEBO - 133 - HAERP00.accdb` shows **six of them are bound to actual
columns** — someone relabelled the captions when the fields fell out of use:

| Form caption | Access control | Actual column | Fill% |
|---|---|---|---|
| Place Holder | `physicalState_Label` | `physicalState` | 0.0 |
| Place Holder | `DeliveryState_Label` | `deliveryState` | 0.0 |
| Place Holder | `PackagingType_Label` | `packagingType` | 0.0 |
| Place Holder - 000 | `tray_Label` | `tray` | **13.4** |
| Place Holder - 000 | `box_Label` | `box` | 0.0 |
| Place Holder - Dtl - 000 | `factorChillingLoss_Label` | `factorChillingLoss` | 0.0 |

`tray` is the one that matters — 320 rows carry data behind a caption that
says the field is unused.

The remaining captions — `PlaceHolderDtl001`–`009`, `PlaceHolder003`/`004`,
`PlaceHolderAttr001`/`002`, `placeHolderOne` — are genuinely unbound spare
controls with no backing column. Nothing to migrate.

---

## 5. Defects to fix before import

### 5.1 `tray` / `container_vessel` / `box` point at the wrong table — data corruption risk

`ProductPackaging` currently declares all three as FKs to `locations.Location`
([product/models.py:322-342](../../gazeboo-cloud/microservice/svc-locations-django/product/models.py#L322-L342)).
That is not what the legacy columns hold:

- `containerVessel` and `tray` hold IDs in **1..40, 25 distinct** — matching
  `tblProductsContainerVessels` (`id`, `container`, `floorName`, next id 46).
  They are *not* `tblContainers` IDs, which run past 223.
- `box` maps to `tblBoxes` (`id`, `box`, next id 8) — 100% NULL today.
- `tray` and `containerVessel` hold the same value in 318 of 320 rows.

Because Location IDs 1–40 **do exist**, an import would not fail — it would
silently bind 320 products to the wrong locations. This is the highest-risk
item in the list.

Fix: add two lookup models and repoint.

```python
class ContainerVessel(models.Model):
    """Legacy tblProductsContainerVessels — the form's 'Tray / Box / Vessel - Floor Name'."""
    id = models.IntegerField(primary_key=True)
    container = models.ForeignKey('locations.Location', on_delete=models.PROTECT,
                                  null=True, blank=True, related_name='vessels')
    floor_name = models.CharField(max_length=64, null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'product_container_vessel'


class Box(models.Model):
    """Legacy tblBoxes."""
    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=32)

    class Meta:
        db_table = 'product_box'
```

Then in `ProductPackaging`: `container_vessel` and `tray` → `ContainerVessel`,
`box` → `Box`.

### 5.2 Legacy timestamps cannot be imported

`Product.created_at` uses `auto_now_add=True` and `updated_at` uses
`auto_now=True` ([product/models.py:166-167](../../gazeboo-cloud/microservice/svc-locations-django/product/models.py#L166-L167)).
Django overwrites both on save, so `inserttime` / `updatetime` — 100%
populated, going back to 2025-02-12 for the worked example — are lost. For an
audit-trail requirement that is a compliance failure.

Fix: drop `auto_now_add` / `auto_now`, set the values explicitly in the
importer, and keep them current via `save()` or a service-layer hook. Same
applies to `RecipeVersion` / `RecipeComponent`, which import from
`tblproducttree.inserttime` / `updatetime`.

### 5.3 `defaultlength` is misfiled

It is the form's **"Default Execution Time - Minutes"** — a planning field,
98.1% populated. It currently lands in `ProductPackaging.default_length`.
Move to `ProductProduction.default_execution_minutes` alongside
`unitary_gap_time` and `unitary_dwell_time`.

Safe to move: `defaultlength` appears exactly **once** in the entire dump — in
the `CREATE TABLE` statement. No procedure, view, function or trigger reads
it, so nothing downstream breaks.

### 5.4 `leadtime` placement

Form label is "Stock Reorder Time - Days" and it sits in the reorder/min/max
block. Currently `ProductCosting.lead_time_days`. Moving it to
`ProductStockPolicy` matches the form and the domain. Low impact — only 0.8%
populated — but worth doing while the schema is still pre-production.

### 5.5 Recipe tree line yield has nowhere to go

`RecipeComponent` has no yield field — the docstring says "Yield/cost SoT stays
on product_* tables". That assumption does not hold. For the worked example:

| | Value |
|---|---|
| `tblproducttree.id 32681`, `productyield` | **0.8415** |
| Parent product 2829 `productyield` | 0.8500 |
| Child product 3099 `productyield` | 1.0000 |

0.8415 exists nowhere except on the tree line, and it is the number the form
displays and uses: `155802.000 × 0.8415 = 131107.383`, the Net Batch Size.
Dropping it means recipe net-quantity math silently changes on every migrated
product.

This also interacts with `tblproducts_AFTER_UPDATE`, which cascades
`productyield` into `tblproducttree.productyield` only when
`fnItemYieldCountAcrossTrees(id) = 1` — so the two values are deliberately
allowed to diverge.

Fix: add to `RecipeComponent`

```python
yield_factor = models.DecimalField(max_digits=10, decimal_places=4,
                                   default=Decimal('1.0000'))
item_cost = models.DecimalField(max_digits=16, decimal_places=6,
                                null=True, blank=True)   # tblproducttree.itemcost
line_cost = models.DecimalField(max_digits=16, decimal_places=6,
                                null=True, blank=True)   # tblproducttree.linecost
```

---

## 6. Import notes

**Booleans are Access-style.** Every flag uses `-1` for TRUE and `0` for FALSE,
not `1`/`0`. Verified across the data — e.g. `active`: 1,754 rows at `-1`,
629 at `0`; `gasflush`: 243 at `-1`, 2,140 at `0`.

```python
def to_bool(v):
    return v not in (None, 0, '0', 'NULL')   # -1 and 1 both mean True
```

A comparison like `v == 1` silently sets every flag on every row to False.

**Widening is safe.** Several targets are wider than the source
(`gff_code` 16→32, `case_size_description` 16→64, `items_per_unit` /
`units_per_tray` / `units_per_batch` / `default_length` int→Decimal(16,6)).
No truncation risk in that direction.

**Fifteen columns are 100% NULL** and carry nothing to migrate:
`factorChillingLoss`, `subrange`, `minstock`, `clearstocklevel`,
`shelflifeintrinsic`, `box`, `relativePlanPosition`, `unitsPerTray`,
`unitsPerBatch`, `rateavgrange`, `technicalSpecSignOffDate`,
`technicalNextReviewDate`, `packagingType`, `physicalState`, `deliveryState`.

Keep the columns — the form exposes them and the business may start using
them — but expect empty data and do not build validation that requires them.

---

## 7. Form regions that are not `tblproducts`

The header strip and the lower tabs read from elsewhere. None of these belong
in the `product` tables:

| Form element | Example | Source |
|---|---|---|
| Actual Total Stock | 727508.24 grams | `tblstockcache` → `stock_ledger` |
| Recorded Transactions | 136 | count over `tblstockmovement` |
| Allergen String | None | `tblAllergensProductsCache` → `ProductAllergen` |
| Item Tree tab | 1 component line | `tblproducttree` → `recipe.RecipeComponent` |
| Yields tab | — | `tblproductsyields` (per trace/batch actuals) |
| Versions tab | — | `tblProductsVersions` |
| Customers tab | — | `tblProductsMappingCustomer` |
| Categories tab | Low Risk > Steam > Chicken | `tblCategories` |
| Stock In / Out / Transfer / Reconciliation | 99 Stock Out rows | `tblstockmovement` → `stock_ledger` |

`tblproductsyields` (`item`, `container`, `tracenumber`, `batchsequence`,
`itemyield`) has **no destination in the new schema yet**. It records realised
yield per production trace, as opposed to the planned yield on
`ProductYield`. If yield variance reporting is in scope, it needs a home.
