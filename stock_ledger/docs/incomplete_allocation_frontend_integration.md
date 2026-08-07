# Incomplete allocation hold — frontend integration

MADE stock can land at Dispatch immediately, but **Dispatch stock screens hide
lots from incomplete BOM allocate** until every recipe component is consumed.
Sleeving still sees Incomplete / Complete on the production grid.

## 1. Production list (Sleeving)

```
GET /stock/production/?from_location_id=5&date=2026-08-06&allocation_status=incomplete
```

`allocation_status`: `incomplete` | `complete` | `all` (default).

Each row includes:

| Field | Use |
|---|---|
| `allocation_status` | Badge Incomplete / Complete (`no_recipe` → treat as Complete) |
| `incomplete_reasons` | Tooltip / subtitle, e.g. "CUSAL base still needs 12.5 kg" |
| `remaining_component_count` | Count for filters |

Complete = every BOM line has `remaining_quantity == 0` (floor allocate via
`POST /stock/production/<id>/consume/`).

## 2. Why stopped (management drill-down)

```
GET /stock/production/<entry_id>/allocation-status/?location_id=5
```

Returns status, `incomplete_reasons`, `remaining_lines`, and full `components`
(needed / consumed / remaining + on-hand balances when `location_id` set).

## 3. Dispatch stock (balances)

```
GET /stock/balances/?location_id=<Dispatch>
```

Incomplete MADE lots are **hidden** when that location’s
`stock_profile.show_incomplete_stock` is `false` (default).

After the last allocate, the lot appears with **no transfer**.

| Override | Effect |
|---|---|
| `?include_incomplete=1` | Show held lots (admin/support) |
| Purchase / opening lots | Always visible |

Scan FIFO at a location uses the same gate:
`GET /stock/scan/?code=P7&location_id=<Dispatch>`.

## 4. Department admin toggle

```
PATCH /container/locations/<dispatch_id>/
{ "stock_profile": { "show_incomplete_stock": true } }
```

Returned on location detail under `stock_profile.show_incomplete_stock`.

| Flag | Dispatch balances |
|---|---|
| `false` (default) | Hide incomplete MADE lots |
| `true` | Show them |

Sleeving Incomplete work uses the production list, not this flag.

## Rules

- Printing / labels do not gate completeness (BOM only).
- Ledger qty at Dispatch is real even while hidden; visibility is the gate.
- No new Complete button — completeness is derived from allocate.
