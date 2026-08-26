---
name: Purchase costing report API
overview: Add a purchase-side report endpoint that lists raw-material and packaging `product_supplier` rows (one row per supplier mapping) with cost and pack fields so purchasing can compare suppliers and feed costing.
todos:
  - id: dict-and-qs
    content: Add purchase_costing_row_dict + report queryset with product class/category/goods_in_type
    status: completed
  - id: view-url
    content: Add GET purchase_costing_report_api and url before <int:pk> routes
    status: completed
  - id: tests
    content: "Tests: RM/pack only, multi-supplier rows, query filters"
    status: completed
isProject: false
---

# Purchase costing report API

## Goal

Expose one read-only report for purchasing: every **raw material** and **packaging** product that has supplier pack mappings, with cost/kg-style fields, so e.g. salt with two suppliers appears as two rows for cost analysis.

## Approach (defaults)

- **Include:** products where `goods_in_type` is `raw_material` or `packaging` (from [`ProductGoodsInType`](product/models.py)).
- **Shape:** one flat row per `product_supplier` (reuses [`supplier_product_dict`](product/views/supplier_product_views.py)).
- **Also include:** products with no supplier yet as empty? **No** — only rows that exist in `product_supplier` (report of what you can buy/cost today). Add query flag later if needed.
- **Auth:** same as existing supplier list (no new decorator).

## Endpoint

`GET /product/purchase-costing-report/`

Query params (all optional):

| Param | Effect |
|---|---|
| `goods_in_type` | `raw_material` \| `packaging` (default: both) |
| `supplier_id` | filter one supplier |
| `product_id` | filter one product |
| `is_default` | `true`/`false` — default pack only if set |
| `is_active` | default `true` on mapping |
| `has_cost` | `true` = only rows with non-null `cost` |

## Response `data[]` fields

Reuse `supplier_product_dict` and add report-only fields from the product:

- existing: `id`, `product_id`, `product_name`, `supplier_*`, `cost`, `cost_per_unit`, `moq`, outer/inner pack, `multiplier`, `shape_format_label`, `is_default`, …
- add: `goods_in_type`, `product_class_id`, `product_class_name`, `category_id`, `category_name`, `recipe_code`, `base_unit_id` (unit already exposed as `base_unit_name`)

`cost_per_unit` is already `cost / multiplier` — that is the main “cost per base unit (e.g. kg)” figure for costing.

## Implementation

1. Extend select_related in a report queryset: `product__product_class`, `product__category` (on top of existing `_base_qs`).
2. Add a small `purchase_costing_row_dict(row)` that merges `supplier_product_dict(row)` + product classification fields.
3. New view `purchase_costing_report_api` in [`product/views/supplier_product_views.py`](product/views/supplier_product_views.py) (or thin wrapper file if you prefer separation — keep in same module to reuse helpers).
4. Wire in [`product/urls.py`](product/urls.py) **before** `<int:pk>/` routes:

   `path('purchase-costing-report/', …)`

5. Tests in [`product/tests_supplier_product.py`](product/tests_supplier_product.py): RM + packaging included, finished goods excluded, two suppliers for same product → two rows, `has_cost` / `goods_in_type` filters.

## Out of scope

- Excel export / pagination (add when FE asks)
- Aggregates (min/max/avg cost) — FE can compute from flat rows
- Writing/updating costs (existing POST/PATCH on `/product/{id}/suppliers/`)
