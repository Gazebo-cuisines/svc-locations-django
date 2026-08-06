# Supplier Product Shape/Format Mapping — Legacy vs New

## Short answer

Legacy fused **supplier + pack shape + multiplier + cost** into one row (`tblProductsMappingSupplier`). Product default and every stock movement pointed at that row.

New system now has the same idea on **`product_supplier`**: structured **Shape Format** with auto multiplier.

```
2 Bag × 5 KG = 10 KG
→ multiplier = 10
→ shape_format_label = "2BAG x 5KG = 10KG"
```

`PurchaseShapeFormat` remains a name-only lookup (optional tag). It is **not** the conversion source of truth.

Cost is optional — skip in UI for now.

---

## Shape Format (current API)

On each `product_supplier` row:

| Field | Role |
|---|---|
| `outer_qty` + `outer_unit` | Left: how many packs (e.g. 2 Bag) |
| `inner_qty` + `inner_unit` | Right: content per pack (e.g. 5 KG) |
| `multiplier` | **Server:** `outer_qty × inner_qty` |
| `shape_format_label` | **Server:** display string |
| `is_default` | Preferred pack for this product |
| `cost` | Optional / nullable |

**POST** `/product/{id}/suppliers/` body:

```json
{
  "supplier_id": 115,
  "supplier_code": "CST-RICE-2X5",
  "supplier_product_name": "Basmati 2x5kg",
  "outer_qty": "2",
  "outer_unit_id": 12,
  "inner_qty": "5",
  "inner_unit_id": 3,
  "is_default": true
}
```

Goods-in later: `stock_qty = received_count × multiplier` (in `inner_unit`).

---

## Gap vs legacy (still open)

| Legacy | New | Status |
|---|---|---|
| Mapping row with multiplier | `product_supplier` + Shape Format | **Done** |
| Product default → mapping id | `is_default` on mapping | **Done** (API-enforced; MySQL has no partial unique) |
| Stock movement → mapping id | `product_supplier_id` on receipt → `stock_entry.counterparty_location_id` = mapping.supplier | **Done** (receipt) |
| Multiplier used in stock-in | `multiplier` stored, unused by stock yet | **Later** |

---

## Why manager saw a bad example

Row like “label product / Pack unit Kg / Conversion 5 / Cost÷base 3” mixed:

1. Wrong pack semantics (single conversion number, no `2×5=10` story)
2. Cost columns when mapping clarity was the goal

Shape Format UI + auto multiplier fixes (1). Skipping cost fixes (2).

---

## Out of scope for now

- Separate global Shape Format master API
- `StockLot` → `product_supplier` FK
- Live goods-in using `multiplier`
