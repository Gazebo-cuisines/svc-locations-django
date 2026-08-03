---
name: Legacy Downtime Findings
overview: Legacy downtime → POST/GET /stock/downtime/ (qty 0 ledger + production_run; no balance update).
todos: []
isProject: false
---

# Legacy Downtime — Findings

**Yes.** Legacy Access/MySQL had downtime, and your type list matches the dump exactly.

## New stack

| Endpoint | Role |
|---|---|
| `GET /stock/downtime/` | Active `is_downtime` products (picker) |
| `POST /stock/downtime/` | Time row: `entry_type=downtime`, qty 0, no `stock_balance`; + `production_run` |

`POST /stock/production/` rejects downtime products. Migration `0008_downtime_entry` allows qty 0 only for `downtime`.

## What it is

Downtime types are **pseudo-products** in `tblproducts` with:

- `downtime = -1` (true)
- `range = 9` → range name **Downtime** in `tblrange`

| ID | Type |
|---|---|
| 108 | BREAK |
| 166 | BREAKDOWN |
| 697 | CLEANING |
| 184 | DAY END |
| 175 | DAY START |
| 11 | DIE CHANGE |
| 129 | LINE ASSIST |
| 454 | MEETING |
| 188 | REPACK |
| 192 | SAMPLES |
| 149 | SHIFT GAP |

Helper: `fnItemIsDowntime(item)` → reads `tblproducts.downtime`.

## How “Record Downtime” worked

Same path as Production Data — a row in `tblstockmovement`, picking a downtime item instead of a real product.

Typical fields: date, shift, resource, **start/end**. **No** qty / stock-cache update.

## Flag mapping

[`Product.is_downtime`](product/models.py) maps from legacy `downtime`.
