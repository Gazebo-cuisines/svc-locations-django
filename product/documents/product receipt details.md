Here's the full picture. All the checkboxes in your screenshot are legacy VB Access UI for a single row in tblproducts (yes, one flat table). The good news: your teammates already designed the new schema for every one of these fields in svc-locations-django/product/models.py — but nobody has written the ETL script to actually move the data over yet (only locations has an import command; product only has a demo seeder).

Where each field lives
Screenshot label	Legacy column (tblproducts)	New model.field
Downtime Item	downtime	Product.is_downtime
Gas Flush Item	gasflush	ProductPackaging.is_gas_flush
Has Intrinsic Process Loss	genHasChillingLoss (+ factorChillingLoss)	ProductFlags.has_chilling_loss (+ ProductYield.chilling_loss_factor)
Exempt 2 Day Deduction When Controlled Defrost	genFreezerNoDeduct	ProductFlags.freezer_no_deduct
Implicit Stock Auto Consumption	genimplicit	ProductFlags.is_implicit
Force Production Date in Stock Transactions	forceproductiondate	ProductShelfLife.force_production_date
Force Use By in Stock Transactions	forceuseby	ProductShelfLife.force_use_by
Force Trace Number in Stock Transactions	forcetracenumber	ProductShelfLife.force_trace_number
Automatically Calculate Yield	genautoyield	ProductFlags.auto_yield
Automatically Calculate Rate	genautorate	ProductFlags.auto_rate
Automatically Calculate Trend	genautotrends	ProductFlags.auto_trends
Automatically Clear Stock Residuals	genautoclearstock	ProductFlags.auto_clear_stock
Include Item in Sales Projections and Planner	genIncludeInProjections	ProductFlags.include_in_projections
Consider Item Stock in Plan / Deduct From Requirements	genconsiderstockinplan	ProductFlags.consider_stock_in_plan
Include Item in Global Stock List	genstocklist	ProductFlags.in_stock_list
Internal Process - Use Batch Quantity for Stock	genUseBatchQuantity	ProductFlags.use_batch_quantity
Has Recipe - Detailed	genhasreceipe	ProductFlags.has_recipe → gates whether a recipe.Recipe/RecipeVersion row should exist
Purchase Unit / Format / Version	purchasingunit / purchaseshapeformat / purchasingversion	Product.purchasing_unit / .purchase_shape_format / .purchasing_version
Goods IN Force Temp Check / Lower / Upper Limit	technicalrequirestemperaturecheck / technicallowerbound... / technicalupperbound...	ProductTechnical.requires_temperature_check / .temp_check_lower_bound / .temp_check_upper_bound
Purchase Item / Sales Item / Dispatch Support	genPurchaseItem / genSalesItem / genIsDispatchSupport	ProductFlags.is_purchase_item / .is_sales_item / .is_dispatch_support
Two spots I can't map with confidence — flagging rather than guessing, since this is going into a compliance record:

"Generate Split Batches on Planner" — best candidate is gengotplan (Django names it ProductFlags.has_plan), paired right before "Generate Full Batch from Remainder" (genfullbatches → full_batches_only), but the column name doesn't confirm the "split batches" semantics on its own.
"Not In Use - 001" and "Place Holder - 002 / 003" (the red cells) — these look like reserved UI slots. record_flag (genrecordflag) may back "Not In Use - 001", but Place Holder 002/003 appear to have no backing column in tblproducts at all — i.e., nothing to migrate for those two, which is itself worth telling your manager explicitly (so the compliance sign-off says "N/A, no legacy data" rather than looking like a missed field).
I'd get those two confirmed by whoever built the original Access form before they go in a compliance doc.

What's actually missing
The schema is ready; the legacy→new import command for products doesn't exist yet. There's a working template at import_legacy_locations.py (reads tblcontainers* via MySQLdb, maps via a legacy_import.py helper module, writes inside transaction.atomic(), does a parity count check at the end) — the same pattern would apply to tblproducts → product_flags, product_shelf_life, product_packaging, product_technical, product_yield, product_costing, product_stock_policy.

Want me to write import_legacy_products following that same pattern once you've confirmed the two ambiguous fields with your manager?