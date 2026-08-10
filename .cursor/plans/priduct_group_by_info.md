Product info grouped by persona

Personas (source of truth)







Persona



Owns



Does not own





Technical



All compliance data (allergens, origin, diet flags, acceptance, temp checks, nutrition, ingredient label, shelf-life / goods-in QA)



Recipes, costing, stock ops





NPD



Recipe data (recipes link, has_recipe flag context, recipe-related codes)



Buy-pack cost, warehouse stock movements





Operational



Batch qty, yield factor, production run settings, packaging counts, stock ops / stock flags / stock policy



Supplier unit cost, allergen specs





Finance



Purchasing (purchase unit/format, suppliers, buy-pack, lead time, reorder) + costing (unit cost/price, nominal code)



Recipes, compliance

Shared for everyone (read); NPD/Technical typically write: Identity (name, recipe_code, class, category/range, stock UoM, containers, label_mode, notes, active).

Purchase buyers sit under Finance for purchasing/costing (not a separate top persona unless you split later).

Target top-level tabs

flowchart LR
  identity[Identity shared]
  technical[Technical compliance]
  npd[NPD recipes]
  operational[Operational batch yield stock]
  finance[Finance purchase costing]
  timeline[Timeline]







Tab



Subsections



Pull from today





Identity



Core · Codes (alt/GFF) · Label mode + barcode print · Notes



Product + Advanced + Barcode + Notes





Technical



Shelf life / goods-in · Acceptance · Temp · Allergens · Origin/diet · Nutrition · Ingredient label



Compliance (all) + Production → shelf-life





NPD



Recipes



Recipes tab (ProductRecipesPanel)





Operational



Production · Yield · Packaging · Stock flags · Stock policy · Stock movements



Production (prod/yield/pack) + Flags (stock group) + Production stock-policy + Stock panel





Finance



Purchase guide · Suppliers / buy-pack · Costing · Purchase flags (is_purchase_item)



Purchase tab + Production → costing + Flags purchase group





Timeline



Audit



Timeline

Drop as top-level: Advanced, Barcode, Notes, Attributes, Purchase, Compliance, Production, Stock, Recipes — folded into the five persona tabs above.

Field → persona matrix (your list)

Identity (shared)
name, recipe_code, product_class, category/range, unit (stock UoM), source_container, destination_container, label_mode

Finance
purchasing_unit, purchase format/version, product_supplier (supplier, codes, outer/inner qty/unit, unit_cost, is_default_supplier, lead_time_days, reorder_level), costing fields, is_purchase_item

Technical
shelf_life_*, force_use_by / force_trace_number / force_production_date, min_acceptable_shelf_life / acceptance_note, requires_temperature_check + bounds, country_of_origin, allergens, storage/delivery state (spec side)

NPD
recipes, has_recipe, recipe-related alternate codes if you want them here later

Operational
batch / packaging quantities, yield_factor, production settings, in_stock_list + other stock flags, stock policy, stock movements UI

How we manage it





One config: [src/pages/product/productSections.ts](src/pages/product/productSections.ts) — persona + tabId + subsections + field notes + roles.read/write.



ProductDetailPage.tsx tabs = those personas; panels composed/moved, same APIs.



Create page stays Identity-only.



Later: filter tabs with canAccessSection(user, persona) when /auth/me has departments.

Out of scope now





Live RBAC



Expanding create to all satellites



Field-level locks inside a tab

Success check





Technical opens product → sees compliance (+ shelf life), not supplier cost.  



NPD → recipes.  



Operational → yield / batch / stock.  



Finance → purchasing + costing.

