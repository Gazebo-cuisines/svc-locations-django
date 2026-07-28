---
name: Stock Ledger Chunks
overview: "Break the Stock Ledger module into 16 sequential chunks, each independently buildable and verifiable, and implement Chunk 1: scaffolding the `stock_ledger` Django app."
todos:
  - id: scaffold-app
    content: "Create stock_ledger app package: __init__.py, apps.py (StockLedgerConfig, BigAutoField), models.py, utils.py with StockValidationError, views.py, urls.py with empty urlpatterns, migrations/__init__.py"
    status: pending
  - id: wire-settings
    content: Add 'stock_ledger' to INSTALLED_APPS in core/settings.py after 'recipe'
    status: pending
  - id: wire-urls
    content: Add path('stock/', include('stock_ledger.urls')) to core/urls.py
    status: pending
  - id: verify-scaffold
    content: Run python manage.py check and showmigrations stock_ledger; confirm clean with no DB changes
    status: pending
isProject: false
---

# Stock Ledger — chunked delivery

App name: `stock_ledger`. Table names keep the plan's `stock_*` prefix (`stock_lot`, `stock_entry`, ...). Source design: [.cursor/plans/stock_ledger_module_09916d1f.plan.md](.cursor/plans/stock_ledger_module_09916d1f.plan.md).

## Chunk list

Build one at a time. Each is gated on your approval.

- **1. Scaffold** — app dir, `apps.py`, `INSTALLED_APPS`, URL wiring, empty migrations package. *(detailed below)*
- **2. Trigger grant probe** — create one throwaway trigger on a scratch table in `DB_LOCATIONS`, confirm it works under ROW binlog, drop it. Gate for chunks 7+.
- **3. Foundation models** — `StockPeriod`, `StockChainHead`, `StockUnitConversion` + migration `0001`. No FKs into stock tables, so it applies standalone. 
- **4. Lot models** — `StockLot`, `StockLotAmendment` + migration `0002`. Depends on `product`, `recipe_version`, `product_purchase_shape_format`.
- **5. The ledger** — `StockEntry` (all 9 entry types, `prev_hash`/`entry_hash`, idempotency key) + migration `0003`.
- **6. Satellite tables** — `StockGenealogy`, `StockBalance`, `StockReservation`, `StockChainAnchor` + migration `0004`.
- **7. Triggers** — migration `0005` with `RunSQL` + `reverse_sql`: `stock_entry_bi` (hash chain, period guard, future-date guard), `stock_entry_ai` (head advance), immutability `SIGNAL` guards on `stock_entry` / `stock_genealogy` / `stock_lot_amendment`, negative-balance guard on `stock_balance`.
- **8. Unit conversion** — seed global rules (`grams` 0.001, `Kg` 1.0), product-specific rows from `product_packaging`, and a `resolve_to_kg()` that raises rather than defaulting to 1.
- **9. Write API** — `receipt`, `issue`, `transfer` pair, `production_output` / `production_consumption` with genealogy edges, `count_adjustment` as delta, `reversal`, authorised negative override. Idempotency key on every mutation; `ER_DUP_ENTRY` treated as success.
- **10. Balance projector** — synchronous upsert inside the ledger transaction, plus the scheduled drift verifier (recompute from ledger, alarm on mismatch).
- **11. Reservations** — lifecycle transitions and available-to-promise (`balance - open reservations`).
- **12. Trace API** — forward and backward recursive CTEs, mass balance and yield loss per output entry.
- **13. Verification suite** — chain continuity, balance invariant, transfer atomicity, reservation queries; each proven to fail against a deliberately corrupted row.
- **14. S3 anchoring** — head-hash publisher to Object Lock compliance mode under a write-only IAM role, plus the anchor verification job.
- **15. Replay-and-diff** — restore `Dump20260720.sql` to a scratch schema, replay 2,938 movements, gate on all 1,002 `tblstockcache.quantity` values matching `stock_balance.quantity`.
- **16. Blocked work** — shadow-run (needs live endpoint), backfill (needs `product` populated beyond 1 row), runtime credential split.

---

## Chunk 1 — Scaffold (this build)

Mirrors the `recipe` app layout exactly. No models, no tables, no migration operations — this chunk only has to leave `manage.py check` and `showmigrations` clean.

### Files created

- `stock_ledger/__init__.py` — empty
- `stock_ledger/apps.py`:

```python
from django.apps import AppConfig


class StockLedgerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'stock_ledger'
```

- `stock_ledger/models.py` — `from django.db import models` only; models land in chunks 3-6
- `stock_ledger/utils.py` — `StockValidationError(ValueError)` only, matching `RecipeValidationError` in [recipe/utils.py](recipe/utils.py)
- `stock_ledger/views.py` — empty module
- `stock_ledger/urls.py` — `urlpatterns = []`
- `stock_ledger/migrations/__init__.py` — empty

### Files edited

- [core/settings.py](core/settings.py) — add `'stock_ledger'` to `INSTALLED_APPS` after `'recipe'`
- [core/urls.py](core/urls.py) — add `path('stock/', include('stock_ledger.urls')),`

### Verification

Run `python manage.py check` and `python manage.py showmigrations stock_ledger`. Expect no errors and an empty (no unapplied) migration list. Nothing touches the database.