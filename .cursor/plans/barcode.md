# Barcode-driven physical stock control (StockUnit layer)

## Context

`stock_ledger` already has a mature, tamper-evident foundation: an immutable hash-chained
`StockEntry` table (DB-trigger-enforced, `stock_ledger/migrations/0005_triggers_chunk7.py`),
lot identity (`StockLot`), a genealogy graph for production (`StockGenealogy`) with working
recursive forward/backward trace (`stock_ledger/util/trace.py`), reservations, and synchronous
balances. What it does NOT have is any concept of a *physical labeled unit* - a specific
box/bag/tin that a person can scan. Today `StockLot.trace_number` identifies a whole batch, so
40 boxes from one batch would carry the same code and be indistinguishable from each other.

The goal: goods received from a supplier get a printed GS1 DataMatrix label per physical unit;
scanning it anywhere pulls up full stock info; scanning it to consume it auto-decrements stock
through the existing ledger; and the same mechanism carries through internal production
(spice-room batch consumed by another department), inter-site transfer (unit 2 <-> unit 11),
and finished-goods dispatch to a customer. This plan adds that missing layer on top of the
existing ledger without duplicating any of its hash-chain/balance/genealogy logic.

Frontend renders the barcode image (bwip-js `gs1datamatrix` bcid) in a separate repo - this
plan only defines the backend model, service layer, API, and the exact payload string contract
the frontend will render.

## What already exists and will be reused, not rebuilt

- **Write primitives** in `stock_ledger/util/services.py`: `receipt()`, `issue()`, `transfer()`
  (posts paired `TRANSFER_OUT`/`TRANSFER_IN` sharing a `transfer_group_id`), `production_output()`,
  `production_consume()`, `production()` (combined output+inputs, builds genealogy edges),
  `disposal()`, `count_adjustment()`, `reversal()`. All keyword-only, all take a
  client-generated `idempotency_key` and replay safely via `_existing()`.
- **Hash chain**: computed entirely by MySQL triggers (`prev_hash`/`entry_hash` on insert,
  `stock_chain_head` update, UPDATE/DELETE forbidden). Application code never touches this.
- **Balance projection**: `_project_balance()` in `services.py`, same transaction as the entry
  insert, guards against negative balance without `override_reason`+`authorised_by_user_id`.
- **Reservations**: `stock_ledger/util/reservations.py` - `reserve()`, `release()`,
  `consume(reservation, *, entry)` (attaches an already-posted `StockEntry` to a reservation).
- **Trace**: `trace_backward(lot_id=...)` / `trace_forward(lot_id=...)` already walk
  `StockGenealogy` recursively - a finished-goods unit can already be traced back to the
  supplier lot that fed it, once units are linked to entries.
- **API convention** (plain Django FBVs, no DRF anywhere in this repo): `@csrf_exempt` +
  `@require_http_methods`, manual JSON body parsing, uniform envelope via
  `locations.utils.api_response.api_success`/`api_error`. Write endpoints pull shared
  audit/source fields via `_common_write_kwargs()` (`stock_ledger/views.py:499`) -
  `actor_user_id`/`lan_username`/`source_workstation` are read from the request body (with
  opportunistic fallback to unverified bearer-token claims / `User-Agent` / `REMOTE_ADDR`).
  There is no real per-user auth anywhere in this service yet - new endpoints should follow
  this exact convention, not invent a different one.
- **Sites (unit 2 / unit 11)**: no separate "site" model exists or is needed - they're already
  plain `locations.Location` rows, used directly by name/id elsewhere (confirmed in
  `docs/planning-redesign/chunk-07-http-api/NOTES.md`, e.g. `location=Unit 11`). New endpoints
  validate against real `Location` rows the same way.
- **Product barcode field**: `product.Product.external_barcode` (`CharField(max_length=16)`)
  already exists - reuse as the GTIN source for the GS1 payload instead of adding a new field.

## New models (`stock_ledger/models.py`)

**`StockUnit`** - one row per physical printed label:
- `unit_serial` - unique, server-generated (not client/frontend-generated), short unguessable
  code (e.g. Crockford base32 of a counter/uuid). Printed both as the DataMatrix payload's
  AI(21) value and as human-readable text under the barcode, so a soiled/unscannable label can
  still be typed in by hand.
- `lot` - FK `StockLot` (PROTECT)
- `location` - FK `locations.Location` (current physical location)
- `unit` - FK `product.Unit`
- `quantity_initial`, `quantity_remaining` - `DecimalField(16,6)`, same precision as `StockEntry`
- `status` - `active | partially_consumed | consumed | void | in_transit` (TextChoices)
- `created_by_entry` - FK `StockEntry` (PROTECT) - the RECEIPT or PRODUCTION_OUTPUT entry this
  label was printed against
- `created_at`, `voided_at`, `void_reason` (nullable)
- Constraints: `0 <= quantity_remaining <= quantity_initial`; unique `unit_serial`.

**`StockUnitConsumption`** - append-only edge, same style as `StockGenealogy`:
- `stock_unit` FK, `stock_entry` FK (PROTECT), `quantity_base` - records how much of a given
  physical unit was drawn down by a given ledger entry. Multiple rows per unit (partial use
  across several production runs) are expected.
- Unique `(stock_unit, stock_entry)`.

**`StockUnitPrintEvent`** - append-only print/reprint audit log:
- `stock_unit` FK, `printed_at`, `actor_user_id`, `lan_username`, `source_workstation`, `reason`
  (`initial | reprint | relabel`). Answers "why are there 3 copies of this sticker" during
  floor disputes - a direct hit on the "employees trust the system" goal.

New migration in `stock_ledger/migrations/` following the existing chunked naming
(next free numbered chunk, e.g. `0010_stock_unit_chunk...py`).

## New service layer (`stock_ledger/util/stock_units.py`)

Thin orchestration only - every consuming action still goes through the existing
`services.*` functions so hash-chain/balance/genealogy logic is never duplicated:

- `generate_unit_serial() -> str`
- `build_gs1_payload(unit: StockUnit) -> dict` - pure function producing
  `{gtin, batch, expiry, serial, payload_string, human_readable}` from
  `unit.lot.product.external_barcode`, `unit.lot.trace_number`, `unit.lot.use_by`,
  `unit.unit_serial`. If `external_barcode` isn't populated for a product, omit AI(01) and
  encode batch+serial only - still enough for internal scan-lookup even if not full retail-GS1
  for that product yet.
- `create_units_for_entry(*, source_entry, unit_count, quantity_per_unit, idempotency_key_prefix, **audit_kwargs) -> list[StockUnit]`
  - validates `unit_count * quantity_per_unit <= source_entry.quantity` (guards against
  printing more physical labels than the ledger says exists), creates N `StockUnit` rows +
  `StockUnitPrintEvent` rows in one transaction.
- `consume_unit(*, unit_serial, entry_type, quantity, idempotency_key, **audit_and_routing_kwargs) -> {"entry": StockEntry, "unit": StockUnit}`
  - `select_for_update()`s the `StockUnit` by serial, validates status is
  `active`/`partially_consumed` and `quantity_remaining >= quantity`, dispatches to the
  matching existing `services.issue()` / `services.transfer()` / `services.disposal()` /
  `services.production_consume()` based on `entry_type`, then creates the
  `StockUnitConsumption` row, decrements `quantity_remaining`, flips `status`
  (`consumed` if it hits zero, else `partially_consumed`) - all in one transaction.
- `void_unit(*, unit_serial, reason, **audit_kwargs)`
- `reprint_unit(*, unit_serial, **audit_kwargs) -> dict` - same `unit_serial`/payload, new
  `StockUnitPrintEvent(reason='reprint')`.

## New API endpoints (`stock_ledger/views.py` + `stock_ledger/urls.py`, same FBV/envelope pattern)

| Purpose | Route | Method |
|---|---|---|
| Print label(s) for a receipt or production-output entry | `stock-units/print/` | POST |
| Scan lookup (resolve full stock info; optional `?trace=backward\|forward`) | `stock-units/<unit_serial>/` | GET |
| Scan-to-consume (issue / disposal / production consumption) | `stock-units/<unit_serial>/consume/` | POST |
| Scan-out / scan-in transfer between sites (direction inferred from current `status`) | `stock-units/<unit_serial>/transfer/` | POST |
| Void a damaged/misprinted label | `stock-units/<unit_serial>/void/` | POST |
| Reprint (same serial, new print event) | `stock-units/<unit_serial>/reprint/` | POST |

The scan-lookup GET is the direct answer to "when we scan we must pull out info required of
that stock": product, lot (trace_number, use_by, production_date, supplier_lot_code), current
location, quantity remaining/initial, status, and - for free, via the existing `trace.py` -
the full backward genealogy to the originating supplier lot.

The `transfer` endpoint enforces the two-step real-world flow: scan-out at the dispatch dock
posts `TRANSFER_OUT` and flips the unit to `in_transit`; scan-in at the receiving dock posts
`TRANSFER_IN`, updates `location`, flips back to `active`. This prevents "phantom stock" that's
marked moved but never physically arrived.

## GS1 payload contract (what the frontend's bwip-js call consumes)

Backend returns a ready-to-render string:

```
(01)<gtin from product.external_barcode>(10)<lot.trace_number>(17)<use_by YYMMDD>(21)<unit_serial>
```

passed as-is into `bwip-js` with `bcid: 'gs1datamatrix'` (not plain `'datamatrix'` -
`gs1datamatrix` is what inserts the FNC1/GS separators correctly). Structured fields are
returned alongside for the human-readable label text block (product name, qty, use-by,
produced date, trace no. - matching your existing label layout).

## Explicitly out of scope for this plan (flagged, not built)

- **No `SalesOrder` model.** `planning` only has production requirements/allocations today,
  no customer-order concept. Dispatch-to-customer will use `StockEntry`'s existing polymorphic
  `source_document_type='sales_order'` / `source_document_id` / `source_document_line` fields
  without a real backing table. Introducing an actual `SalesOrder` model is a separate future
  plan if/when needed.
- **No new auth/RBAC.** This repo has no verified per-user auth anywhere (Cognito login exists
  but nothing downstream verifies the token). New endpoints follow the existing
  body-supplied-`actor_user_id` convention, not a new one.
- **No printer/ZPL integration or frontend barcode rendering** - lives in the separate frontend
  repo; this plan defines only the payload contract.
- **Not fixing** the pre-existing broken `department_views.py:create_department` (unrelated
  `NameError` bug found during exploration) unless separately requested.

## Verification

- New tests under `stock_ledger/tests_stock_units.py`, following the existing
  `planning/tests_chain_net.py` style: cover print -> scan -> partial consume -> full consume ->
  status transitions, over-print guard (can't print more units than entry quantity),
  transfer scan-out/scan-in round trip, void + reprint audit trail.
- Manual end-to-end: goods-in receipt -> print units -> scan lookup -> production_consume by
  another department -> production_output prints a new unit for the batch -> transfer scan-out
  at unit 2 -> scan-in at unit 11 -> consume-to-dispatch -> `trace_backward` on the dispatched
  unit's lot confirms it resolves back to the original supplier receipt. Exercise via the
  existing Postman collection convention (`/postman`) or a new demo seed command modeled on
  `stock_ledger/management/commands/seed_stock_ledger_demo.py`.
