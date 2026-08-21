# Unit 2 Food Raw Material — MVP Stock Control Test Runbook

Scope: warehouse inventory only (stock in / stock out / transfer / reconcile) for Unit 2
ambient, chilled and frozen raw material, across stock units **Kg, Liter, unit, grams**.

Read Part 0 before you run a single test. It lists defects found by reading the ledger
source, not by guessing. Four of them can silently corrupt on-hand quantities, and on
MySQL the ledger is append-only — **a bad entry cannot be edited or deleted afterwards**.

---

## Part 0 — Known defects. Decide on each before go-live.

Severity key: **P0** = can silently produce a wrong on-hand number. **P1** = can stop the
warehouse or lose a field. **P2** = annoyance.

### D1 (P0) — A quantity posted in a different unit is added raw, with no conversion

`views.py:683 _optional_unit_id()` accepts any `unit_id` from the request body and does
nothing but `int(raw)`. It flows to `services.py _resolve_unit_id()`, which returns it
unchanged, into `_insert_entry(quantity=quantity, unit_id=unit_id)`. Then
`_project_balance()` does:

```python
new_qty = entry.quantity if balance is None else balance.quantity + entry.quantity
```

There is no check that `entry.unit_id == lot.product.unit_id`, and no conversion.
`to_product_unit()` exists in `util/conversions.py` and does the right thing — but it is
only ever reached through `packs_to_stock()`, i.e. the `product_supplier_id` goods-in path.
Plain receipt / issue / transfer / count-adjustment never call it.

Consequence: receive **10** on a Kg product, then issue **500** with `unit_id` = grams,
and `stock_balance.quantity` becomes **-490**. The number is unitless nonsense. Both
entries pass every DB constraint.

**This is not repairable after the fact.** `management/commands/fix_ledger_product_unit.py`
was written to correct exactly this, and its own docstring says:

> `MySQL stock_entry_bu blocks UPDATE; --apply is SQLite/tests only unless that trigger is dropped.`

It refuses to apply on MySQL. The only remedy on a live MySQL ledger is a compensating
`count_adjustment`, which leaves the wrong entries permanently in the audit chain.

**Mitigation you must apply before go-live, since we are not changing code:**

1. The warehouse UI must never send `unit_id`. Omit the field entirely — then
   `_resolve_unit_id()` falls back to `lot.product.unit_id`, which is always correct.
2. If any client does send it, it must send `unit_id` equal to the product's stock unit.
3. Add a nightly detector (query in Part 3, check C4). Run it from day one.

### D2 (P0) — Reversing one leg of a transfer invents stock

`POST /stock/reversal/` takes a single `entry_id`. `services.reversal()` (services.py:1140)
reverses that one entry at that one location. It copies `transfer_group_id` onto the
reversal but never looks up the paired leg.

So reversing the `transfer_out` puts the quantity back at the source **while the
`transfer_in` at the destination stays live**. Total stock across Unit 2 goes up by the
transfer quantity, out of thin air.

The pairing logic exists elsewhere — `util/entry_posting.py:152` correctly finds the
matching `transfer_in` by `transfer_group_id` when posting a queued transfer. It is simply
absent from the reversal path.

**Mitigation:** reversing a transfer is a **two-call operation**. Get both entry IDs from
the transfer response (`data.out.id` and `data.in.id`) and reverse both, or reverse
neither. Test REV-2 below proves the failure. Until this is fixed, do not expose a
one-click "undo transfer" button.

Good news: `verify_stock_ledger`'s `transfer_atomicity` check *does* catch this after the
fact, because the reversal copies `transfer_group_id` and the group stops summing to zero.
Schedule that command nightly (check C0).

### D3 (P0) — The KG column gets wiped to NULL by one entry

`_project_balance()`:

```python
new_base = None
if entry.quantity_base is not None:
    prev_base = ... ; new_base = prev_base + entry.quantity_base
...
balance.quantity_base = new_base
```

`_mass_fields()` returns `(None, None)` whenever `resolve_to_kg()` raises — i.e. whenever
there is no `stock_unit_conversion` row for that unit/product. So a single entry posted in
a unit that has no kg conversion sets `stock_balance.quantity_base` to NULL and **discards
the accumulated kg total** for that lot/location. It is not recomputed from the ledger
afterwards.

**Mitigation:** guarantee a conversion row exists for every product you go live with —
check S5 in Part 1. Treat a NULL `quantity_base` on a lot that previously had one as an
incident, not a display quirk.

### D4 (P0) — Two simultaneous stock counts double-apply

`services.count_adjustment()` with `counted_quantity` reads the current balance like this:

```python
balance = StockBalance.objects.filter(...).only('quantity').first()
current = balance.quantity if balance else Decimal('0')
quantity_delta = counted_quantity - current
```

No `select_for_update()`, and the read is outside the transaction that later writes the
entry. Two counters reconciling the same lot/location at the same moment both compute
their delta against the same starting quantity, and both deltas get applied.

Example: on-hand 100. Counter A counts 90 → delta -10. Counter B counts 90 → delta -10.
Final balance **80**, not 90.

**Mitigation:** stock count for a given location is a single-operator job, or use
`quantity_delta` instead of `counted_quantity` (delta is additive, so it is safe under
concurrency). Test RC-6 reproduces this.

### D5 (P1) — Any client can push stock negative

`_common_write_kwargs()` (views.py:822) copies `override_reason` and
`authorised_by_user_id` straight out of the request body. `_project_balance()` permits a
negative balance whenever both are present. There is no check that
`authorised_by_user_id` is a real user, or that the user has authority to approve a
negative.

**Mitigation:** the UI must not offer these fields to floor staff. Monitor
`stock_balance.negative_authorised_by_entry_id IS NOT NULL` daily (check C2).

### D6 (P1) — The balance row is deleted when it reaches zero

`_project_balance()` calls `balance.delete()` when `new_qty == 0`. That drops
`last_count_entry_id` and `negative_authorised_by_entry_id` with it. It also means
`GET /stock/balances/?include_zero=true` returns **nothing** for a lot that has been fully
consumed — the row does not exist, so there is no zero to include. The ledger still has
the full history; the read model does not.

### D7 (P1) — `unit`, `Box` and `Liter` all receive the same kg factor

`conversions.py sync_product_unit_conversions_from_packaging()` loops over
`PRODUCT_SPECIFIC_UNIT_NAMES = {'unit', 'Box', 'Liter'}` and writes
`packaging.unitary_weight` as `to_kg` for **all three**. So for a given product, 1 Box
converts to the same kg as 1 unit, and 1 Liter converts as if it were 1 unit.

`ProductPackaging` is a `OneToOneField` on product, so there is no duplicate-row ambiguity
— but there is only one weight to go round, and it is being reused for three different
physical meanings.

**Mitigation:** for MVP, only go live with products whose stock unit is **Kg, grams, or
Liter**, and for Liter products verify the factor by hand (check S5). Do not use `Box` as
a stock unit. Test UOM-4 covers this.

### D8 — stock_period (removed)

Month-end close guard was dropped. Entries post without an open period. Re-add when finance actually closes months.

### D9 (P2) — Transfer's friendly "not enough stock" message only appears on the queued path

In `services.transfer()` the readable error
(`Only 5 Kg on this lot (need 20). Transfer 5 or less.`) sits inside `if defer_balance:`.
A direct-post transfer instead gets the raw
`stock_balance: negative without authorised override`. Same protection, worse message.

### D10 (P1) — The drift alarm cries wolf on every queued entry

`util/balance.py find_balance_drift()` compares `stock_balance.quantity` against
`SUM(stock_entry.quantity)` per lot/location with **no filter on posting status**.

Queued entries (`queue_stock: true`, or any receipt with `label_format`) are written to
`stock_entry` with `project_balance=False` by design — they are meant to stay out of the
balance until the label is verified and the entry is posted. Cancelled entries stay out
forever. Both therefore appear as drift.

So `verify_stock_ledger` will report `balance_invariant: FAIL` during normal operation,
every day, for reasons that are not failures. The one alarm that would tell you the
on-hand numbers have been corrupted is the one you will learn to ignore.

**Mitigation:** use the corrected query in Part 3 check C1 as the real alarm, and treat
`verify_stock_ledger`'s balance check as informational until the filter is added.

---

## Part 1 — Test environment setup

**Database:** dev (`3.10.84.186` / `DB_LOCATIONS`), using dedicated throwaway products and
locations so junk entries are quarantined.

Nothing you post here can be deleted. Prefix everything `ZZTEST_` so it sorts to the
bottom of every list and is trivial to exclude from reports.

### S1 — Create test locations under Unit 2

| Name | Role | Purpose |
|---|---|---|
| `ZZTEST_AMBIENT` | storage | ambient raw material |
| `ZZTEST_CHILLED` | storage | chilled raw material |
| `ZZTEST_FROZEN` | storage | frozen raw material |
| `ZZTEST_SUPPLIER` | supplier | goods-in counterparty |

`warehouse_remaining_api` only returns locations with `LocationRole.STORAGE`, so the three
storage locations must carry that role or they will be invisible in the remaining-stock
screen.

Record the IDs:

```
AMBIENT_LOC   = ____
CHILLED_LOC   = ____
FROZEN_LOC    = ____
SUPPLIER_LOC  = ____
```

### S2 — Create test products, one per unit type

| Product | Stock unit | Storage | Why |
|---|---|---|---|
| `ZZTEST_FLOUR` | Kg | ambient | the common case |
| `ZZTEST_SPICE` | grams | ambient | small quantities, global conversion |
| `ZZTEST_MILK` | Liter | chilled | product-specific conversion (D7 risk) |
| `ZZTEST_PATTY` | unit | frozen | countable items, product-specific conversion |

Every one must have `product.unit_id` set and `is_active = true`.
`resolve_lot()` rejects inactive or missing products outright.

```
FLOUR_ID = ____   SPICE_ID = ____   MILK_ID = ____   PATTY_ID = ____
UNIT_KG = ____  UNIT_G = ____  UNIT_L = ____  UNIT_EA = ____   (product_unit IDs)
```

### S3 — stock_period (removed)

Skip. Movements no longer require an open period.

### S4 — Confirm the global unit conversions exist

```sql
SELECT u.name, c.to_kg, c.source FROM stock_unit_conversion c
JOIN product_unit u ON u.id = c.unit_id WHERE c.product_id IS NULL;
```

**Expect:** `grams → 0.001000`, `Kg → 1.000000`.
Missing → run `seed_global_unit_conversions()`.

### S5 — Confirm product-specific conversions, and sanity-check them by hand

```sql
SELECT p.name, u.name AS unit, c.to_kg, c.source
FROM stock_unit_conversion c
JOIN product_unit u ON u.id = c.unit_id
JOIN product p ON p.id = c.product_id
WHERE p.name LIKE 'ZZTEST%' ORDER BY p.name, u.name;
```

**Expect:** three rows per product (`unit`, `Box`, `Liter`) all carrying the **same**
`to_kg` — that is D7. Verify the number is physically right for the unit you actually
intend to use:

- `ZZTEST_MILK` in Liter → `to_kg` should be roughly **1.03** (milk density), not the
  weight of one bottle.
- `ZZTEST_PATTY` in unit → `to_kg` should be the weight of **one patty**.

If `to_kg` is wrong here, every kg figure on every report for that product is wrong.
This is the single most valuable 10 minutes in the whole runbook.

### S6 — Auth

Warehouse write endpoints are gated (`gate_warehouse_write`, `gate_floor_write` in
`users_rbac/permissions.py`). Receipt requires `goods_in`, issue and disposal require
`goods_out`, transfer and count-adjustment require any warehouse access, reversal requires
floor write.

Use a test operator with Unit 2 access. Send `Authorization: Bearer <token>` on every call.
Test AUTH-1 checks the gates actually bite.

### Conventions used below

- `{{base}}` = service base URL. All stock routes are under `/stock/`.
- Every response is `{"success": bool, "message": str, "data": {...}}`.
- `idempotency_key` must be unique per logical action. Reusing one returns the **original**
  entry rather than posting a new one — that is the designed behaviour, and IDEM-1 tests it.
- After every test, verify with:
  `GET {{base}}/stock/balances/?product_id=<id>&location_id=<id>&include_zero=true`
- Omit `unit_id` from every request body unless the test explicitly says to send it (D1).

---

## Part 2 — Test cases

Each group uses its own `trace_number`, so each group creates a fresh lot and the expected
balances are absolute. You can run groups in any order, but run every case inside a group
in sequence.

Result columns: record **PASS** (matches expected), **FAIL** (does not), or **BUG**
(behaves as the defect predicts — expected today, must be fixed later).

---

### Group A — Stock In (receipt)

`POST {{base}}/stock/receipt/` · requires `goods_in` · trace `ZZTA`

#### RI-1 · Receive Kg, ambient

```json
{
  "idempotency_key": "ZZT-RI-1",
  "product_id": FLOUR_ID,
  "location_id": AMBIENT_LOC,
  "supplier_id": SUPPLIER_LOC,
  "trace_number": "ZZTA",
  "use_by": "2026-12-31",
  "quantity": "100"
}
```

**Expect** `201`, `message: "Receipt posted."`, and in `data`:

| field | expected |
|---|---|
| `entry_type` | `receipt` |
| `quantity` | `100.000000` |
| `unit_name` | `Kg` |
| `base_unit_factor` | `1.000000` |
| `quantity_base` | `100.000000` |
| `display_kg` | `100.000000` |
| `to_location_id` | AMBIENT_LOC |
| `supplier_id` | SUPPLIER_LOC |
| `entry_hash` | 64 hex chars, non-empty |

**Then** `GET {{base}}/stock/balances/?product_id=FLOUR_ID&location_id=AMBIENT_LOC`
→ exactly one row, `quantity 100.000000`, `quantity_base 100.000000`.

Record `ENTRY_RI1 = data.id` and `LOT_A = data.lot_id`.

#### RI-2 · Receive grams, ambient

Same shape, `product_id: SPICE_ID`, `quantity: "2500"`, key `ZZT-RI-2`, trace `ZZTA`.

**Expect** `quantity 2500.000000`, `unit_name grams`, `base_unit_factor 0.001000`,
`quantity_base 2.500000`, `display_kg 2.500000`.
**Balance** 2500.000000 grams.

*If `quantity_base` is null → the global grams conversion is missing (S4).*

#### RI-3 · Receive Liter, chilled

`product_id: MILK_ID`, `location_id: CHILLED_LOC`, `quantity: "50"`, key `ZZT-RI-3`.

**Expect** `unit_name Liter`, `quantity 50.000000`,
`quantity_base = 50 × to_kg(Liter, MILK_ID)` from S5.
With a correct density of 1.03 that is `51.500000`.

**Check by hand.** If `quantity_base` comes back as 50 × *bottle weight* instead of
50 × 1.03, you have hit D7 and every kg report for milk is wrong. Mark **BUG**.

#### RI-4 · Receive unit (each), frozen

`product_id: PATTY_ID`, `location_id: FROZEN_LOC`, `quantity: "200"`, key `ZZT-RI-4`.

**Expect** `unit_name unit`, `quantity 200.000000`,
`quantity_base = 200 × to_kg(unit, PATTY_ID)`.
200 patties at 0.113 kg → `22.600000`.

#### RI-5 · Zero quantity

`quantity: "0"`, key `ZZT-RI-5`.
**Expect** `400`, `success: false`, message contains
`receipt quantity must be positive`. No entry, no balance change.

#### RI-6 · Negative quantity

`quantity: "-10"`, key `ZZT-RI-6`.
**Expect** `400`, `receipt quantity must be positive`.

#### RI-7 · PO number sent to the wrong endpoint

Add `"po_number": "12345"` to a valid body, key `ZZT-RI-7`.
**Expect** `400`, message:
`PO goods-in must use POST /purchasing/pos/<po_id>/receive/. Do not pass po_number or source_document_type=po to /stock/receipt/.`

This guard matters: it is what stops PO receipts bypassing the PO quantity reconciliation.

#### RI-8 · Purchase lot with no supplier

Valid body, key `ZZT-RI-8`, but omit both `supplier_id` and `product_supplier_id`.
**Expect** `400`, `supplier_id or product_supplier_id is required for purchase goods-in.`

#### RI-9 · Inactive product

Set `ZZTEST_FLOUR.is_active = false`, post key `ZZT-RI-9`, then set it back.
**Expect** `400`, `product_id=<id> is inactive or missing`.

#### RI-10 · Sub-precision quantity

`quantity: "0.0000004"` on FLOUR, key `ZZT-RI-10`.

The column is `DECIMAL(16,6)`, so the value is rounded on write. Record exactly what
happens — either a `400`, or an entry with `quantity 0.000000` that slipped past the
`chk_stock_entry_qty_nonzero` check constraint. Then repeat with `"0.0000006"`.

**Why this matters:** a zero-quantity entry is unreversible dead weight in the hash chain,
and if `0.0000004` stores as `0.000000` the non-zero guard is not doing its job. Record the
actual behaviour and decide whether the UI needs to cap input at 6 decimals.

#### RI-11 · Split into box labels — check the rounding remainder

```json
{
  "idempotency_key": "ZZT-RI-11",
  "product_id": FLOUR_ID, "location_id": AMBIENT_LOC,
  "supplier_id": SUPPLIER_LOC, "trace_number": "ZZTA-11",
  "quantity": "100", "label_format": "box", "label_count": 3
}
```

**Expect** `201`, `data.transaction_count = 3`, and `data.transactions[*].quantity`:

| # | expected |
|---|---|
| 1 | `33.333333` |
| 2 | `33.333333` |
| 3 | `33.333334` |

**Sum must be exactly `100.000000`.** The last part absorbs the remainder
(`total - sum(parts)`), which is the correct pattern — confirm it, do not assume it.

**Balance must NOT move yet.** `label_format` forces `queue_stock = true`, so all three
entries are queued with `project_balance = false`. Balance stays at its RI-1 value.
Each transaction carries `posting.status = "queued"`.

#### RI-12 · Post a queued receipt

Take entry #1 from RI-11.

1. `POST {{base}}/stock/entries/<id>/labels/print/`
2. `POST {{base}}/stock/entries/<id>/labels/verify/` — repeat until
   `verified_count == label_count`
3. `POST {{base}}/stock/entries/<id>/post/`

**Expect** post returns `status: "posted"`, `already_live: false`, and only now does the
balance rise by `33.333333`.

Try `POST .../post/` **before** verifying: expect `400`,
`label status=...; verify all copies before posting stock (0/1)`.
Try posting the same entry twice: expect `already_live: true`, and **the balance must not
move a second time**.

#### RI-13 · Cancel a queued receipt

Queue a receipt (`queue_stock: true`), then `POST {{base}}/stock/entries/<id>/cancel/`.
**Expect** `status: cancelled`, balance unchanged. Then try `/post/` on it:
**expect** `400`, `posting is cancelled.`

**Now note the trap (D10):** the cancelled entry is still in `stock_entry` with a non-zero
quantity, but will never reach `stock_balance`. See Part 3 check C1.

---

### Group B — Stock Out (issue)

`POST {{base}}/stock/issue/` · requires `goods_out` · trace `ZZTB`

Set up: receive 100 Kg FLOUR into AMBIENT with trace `ZZTB`, key `ZZT-B-SETUP`.
Record `LOT_B`.

#### IO-1 · Normal issue

```json
{
  "idempotency_key": "ZZT-IO-1",
  "lot_id": LOT_B, "location_id": AMBIENT_LOC, "quantity": "30"
}
```

**Expect** `201`, `entry_type issue`, **`quantity -30.000000`** (the ledger stores issues
negative), `quantity_base -30.000000`, `display_kg 30.000000` (absolute value).
**Balance** `70.000000`.

#### IO-2 · Issue more than on hand

`quantity: "1000"`, key `ZZT-IO-2`.
**Expect** `400`, `stock_balance: negative without authorised override`.
**Balance unchanged at 70.000000** — and confirm no `stock_entry` row was created for this
key. The rollback must be complete, not partial.

#### IO-3 · Issue the exact remaining quantity

`quantity: "70"`, key `ZZT-IO-3`.
**Expect** `201`, balance reaches zero.

Then `GET {{base}}/stock/balances/?lot_id=LOT_B&include_zero=true`
**Expect an empty list** — not a row showing `0`. `_project_balance()` deletes the row at
zero (D6). Confirm the ledger still shows the full history:
`GET {{base}}/stock/audit/timeline/` filtered to this lot returns all three entries.

#### IO-4 · Forced negative issue

```json
{
  "idempotency_key": "ZZT-IO-4",
  "lot_id": LOT_B, "location_id": AMBIENT_LOC, "quantity": "5",
  "override_reason": "runbook D5 test",
  "authorised_by_user_id": 999999
}
```

**Expect** `201` and a balance of **`-5.000000`**, with
`negative_authorised_by_entry_id` set to this entry.

Note `authorised_by_user_id: 999999` does not need to exist. That is D5 — the field is
taken from the request body and never validated. Mark **BUG** and confirm the warehouse UI
never exposes these two fields.

Clean up: `count_adjustment` back to 0 before moving on.

#### IO-5 · Zero and negative quantity

`quantity: "0"` then `"-5"`, keys `ZZT-IO-5a` / `ZZT-IO-5b`.
**Expect** both `400`, `issue quantity must be positive`.

#### IO-6 · Issue from a location holding no stock

`location_id: FROZEN_LOC` against `LOT_B`, key `ZZT-IO-6`.
**Expect** `400`, `stock_balance: negative without authorised override`.
No balance row is created at FROZEN_LOC.

#### IO-7 · Issue against a receipt entry code

`{"idempotency_key": "ZZT-IO-7", "source_entry_code": "E<id>", "quantity": "1"}`
using the RI-1 entry code.
**Expect** `201`, lot and location inherited from the source receipt,
`data.source_entry_id` and `data.source_entry_code` echoed back.
Then try a `source_entry_code` pointing at an **issue** entry:
**expect** `400` from `require_receipt_entry`.

---

### Group C — Transfer

`POST {{base}}/stock/transfer/` · trace `ZZTC`

Set up: receive 100 Kg FLOUR into AMBIENT, trace `ZZTC`, key `ZZT-C-SETUP`. Record `LOT_C`.

#### TR-1 · Ambient → chilled

```json
{
  "idempotency_key": "ZZT-TR-1",
  "lot_id": LOT_C,
  "from_location_id": AMBIENT_LOC,
  "to_location_id": CHILLED_LOC,
  "quantity": "20"
}
```

**Expect** `201`, `message: "Transfer posted."`, and:

| path | expected |
|---|---|
| `data.out.entry_type` | `transfer_out` |
| `data.out.quantity` | `-20.000000` |
| `data.out.location_id` | AMBIENT_LOC |
| `data.out.counterparty_location_id` | CHILLED_LOC |
| `data.in.entry_type` | `transfer_in` |
| `data.in.quantity` | `20.000000` |
| `data.in.location_id` | CHILLED_LOC |
| `data.out.transfer_group_id` | **equal to** `data.in.transfer_group_id`, non-null |

**Balances:** AMBIENT `80.000000`, CHILLED `20.000000`.
**Total across both locations must still be `100.000000`.** This conservation check is the
whole point of the test — write both numbers down and add them.

Record `ENTRY_OUT_C = data.out.id`, `ENTRY_IN_C = data.in.id`.

#### TR-2 · Same source and destination

`from_location_id == to_location_id`, key `ZZT-TR-2`.
**Expect** `400`, `transfer locations must differ`.

#### TR-3 · More than available

`quantity: "500"`, key `ZZT-TR-3`.
**Expect** `400`. Message will be
`stock_balance: negative without authorised override` on the direct path (D9), not the
friendly "Only 80 Kg on this lot" wording — that only appears when `queue_stock: true`.

**Critical:** confirm **both** legs rolled back. Query
`SELECT id, entry_type, quantity, location_id FROM stock_entry WHERE idempotency_key LIKE 'ZZT-TR-3%';`
→ **expect zero rows**. If a `transfer_in` survived without its `transfer_out`, you have
created stock from nothing and must stop the launch.

#### TR-4 · Zero and negative quantity

**Expect** `400`, `transfer quantity must be positive`, both cases.

#### TR-5 · Transfer across temperature zones

Chilled → frozen, then frozen → ambient, 5 Kg each, keys `ZZT-TR-5a` / `ZZT-TR-5b`.
**Expect** both succeed. There is no temperature-compatibility rule in the ledger.

**Decide now:** should the system stop a chilled item being transferred into ambient
storage? If yes, that is a missing rule, not a bug — record it as a gap for release 2.
The ledger will happily let staff move frozen goods into ambient racking.

#### TR-6 · Queued transfer and FIFO override

```json
{
  "idempotency_key": "ZZT-TR-6",
  "lot_id": <a NEWER lot of FLOUR at AMBIENT>,
  "from_location_id": AMBIENT_LOC, "to_location_id": CHILLED_LOC,
  "quantity": "5", "queue_stock": true
}
```

With an older lot of the same product present at AMBIENT:
**Expect** `400`, `fifo_override_reason is required when not using oldest stock.`

Re-send with `"fifo_override_reason": "runbook test"`:
**Expect** `201`, `message: "Transfer queued."`, `data.posting.status = "queued"`, a
`data.label`, and **no balance movement at either location yet**. A `stock_fifo_override`
row is written.

Then print → verify → `POST /stock/entries/<out_id>/post/`.
**Expect both legs to move together**: AMBIENT −5 and CHILLED +5 in the same call.
`entry_posting.post_entry()` pairs the legs by `transfer_group_id`. Confirm this, because
it is the behaviour that reversal is missing (D2).

#### TR-7 · Repeat a completed transfer key

Re-send the exact TR-1 body with the same `idempotency_key`.
**Expect** `201` with the **same two entry IDs** as TR-1, and **balances unchanged**
(AMBIENT 80, CHILLED 20). No second movement.

---

### Group D — Reconcile (stock count / count adjustment)

`POST {{base}}/stock/count-adjustment/` · trace `ZZTD`

Set up: receive 100 Kg FLOUR into AMBIENT, trace `ZZTD`, key `ZZT-D-SETUP`. Record `LOT_D`.

#### RC-1 · Counted higher than system (found stock)

```json
{
  "idempotency_key": "ZZT-RC-1",
  "lot_id": LOT_D, "location_id": AMBIENT_LOC,
  "counted_quantity": "105"
}
```

**Expect** `201`, `entry_type count_adjustment`, **`quantity 5.000000`** (the *delta*, not
the count), `quantity_base 5.000000`.
**Balance** `105.000000`, and `stock_balance.last_count_entry_id` = this entry.

#### RC-2 · Counted lower than system (shrinkage)

`counted_quantity: "95"`, key `ZZT-RC-2`.
**Expect** `quantity -10.000000` (105 → 95). **Balance** `95.000000`.

#### RC-3 · Counted matches system

`counted_quantity: "95"`, key `ZZT-RC-3`.
**Expect** `400`,
`count_adjustment delta must be non-zero (counted matches on-hand)`.
**Balance unchanged.**

This is correct behaviour, but the UI must present it as "no variance, nothing to post",
not as a red error. A counter who sees a failure message when the count is *right* will
learn to distrust the screen.

#### RC-4 · Counted zero (location emptied)

`counted_quantity: "0"`, key `ZZT-RC-4`.
**Expect** `201`, `quantity -95.000000`, and the balance row **deleted** (D6).
`GET .../balances/?lot_id=LOT_D&include_zero=true` → empty list.
`last_count_entry_id` is lost with the row.

#### RC-5 · Counted negative

`counted_quantity: "-5"` on a lot with 50 on hand, key `ZZT-RC-5`.
**Expect** `400`, `stock_balance: negative without authorised override`.
A physical count can never be negative — record whether the UI blocks this at input.

#### RC-6 · Two counts at once — the double-apply race (D4)

Set the lot to `100`. Then fire **two requests within the same second**, different
idempotency keys, both `counted_quantity: "90"`:

```
ZZT-RC-6a  counted_quantity 90
ZZT-RC-6b  counted_quantity 90
```

**Correct behaviour** would be a final balance of `90`.
**Actual expected behaviour (BUG)**: both compute `delta = 90 − 100 = −10`, both apply,
final balance **`80`**.

If you get 80, D4 is confirmed. Mitigation for MVP: one operator per location during a
count, or use `quantity_delta` (RC-7) which is safe under concurrency.

If you get 90, the read happened to serialise — retry a few times before concluding it is
safe. This is a race, so a single pass proves nothing.

#### RC-7 · Delta path instead of count path

`{"idempotency_key": "ZZT-RC-7", "lot_id": LOT_D, "location_id": AMBIENT_LOC, "quantity_delta": "-7"}`
**Expect** `201`, `quantity -7.000000`, balance down by exactly 7.

Repeat the same test concurrently as in RC-6: two `quantity_delta: "-10"` calls should
correctly give −20, because deltas are additive. Confirm this — it is your workaround.

#### RC-8 · Both `counted_quantity` and `quantity_delta` supplied

Send both, key `ZZT-RC-8`.
**Expect:** `counted_quantity` wins — `count_adjustment()` overwrites `quantity_delta`
whenever `counted_quantity` is not None. Confirm, and make sure the UI never sends both.

#### RC-9 · Reconcile each unit type

Repeat RC-1 and RC-2 for SPICE (grams), MILK (Liter) and PATTY (unit).
**Expect** deltas in the product's own stock unit, and `quantity_base` moving by
`delta × to_kg`. For PATTY, count a fractional quantity such as `199.5`:
**expect** it to be accepted — the ledger has no integer constraint on countable items.
Decide whether the UI should block half a burger patty.

---

### Group E — Reversal

`POST {{base}}/stock/reversal/` · requires floor write

#### REV-1 · Reverse a receipt

Receive 40 Kg FLOUR into AMBIENT (trace `ZZTE`, key `ZZT-E-SETUP`), then:

```json
{"idempotency_key": "ZZT-REV-1", "entry_id": <receipt id>}
```

**Expect** `201`, `entry_type reversal`, `quantity -40.000000`,
`reverses_entry_id` = the receipt.
**Balance** back to its pre-receipt value (row deleted if that reaches zero).

#### REV-2 · Reverse ONE leg of a transfer — expect phantom stock (D2)

Using TR-1's result (AMBIENT 80, CHILLED 20, total 100):

```json
{"idempotency_key": "ZZT-REV-2", "entry_id": ENTRY_OUT_C}
```

**Expect** `201` — the call **succeeds**, which is the problem.

| location | before | after |
|---|---|---|
| AMBIENT | 80.000000 | **100.000000** |
| CHILLED | 20.000000 | **20.000000** |
| **total** | **100.000000** | **120.000000** |

**20 Kg of flour has been created out of nothing.** `services.reversal()` copies
`transfer_group_id` onto the reversal but never reverses the paired `transfer_in`.
Meanwhile `entry_posting.post_entry()` *does* pair both legs — so the codebase knows how,
the reversal path just does not do it.

Mark **BUG**. Then reverse `ENTRY_IN_C` too (key `ZZT-REV-2b`) and confirm the total
returns to `100.000000`. **Until this is fixed, "reverse a transfer" must be a two-call
operation in the UI, or must not exist at all.**

#### REV-3 · Reverse the same entry twice

Re-send REV-1 with a **different** idempotency key against the same `entry_id`.
**Expect** the *original* reversal returned (`services.reversal` short-circuits on
`entry.reversed_by`) and **no second balance movement**. Verify the balance did not move.

#### REV-4 · Reverse a reversal

`entry_id` = the REV-1 reversal entry, key `ZZT-REV-4`.
**Expect:** record what happens. `reverses_entry` is a `OneToOneField`, so a reversal can
itself be reversed once. Confirm the balance ends up back where it started and does not
double-apply.

#### REV-5 · Reverse an entry (period close removed)

Skip. `stock_period` is gone; reversals post like any other entry.

**Confirm this is what finance wants.** It means a reversal of a June receipt lands in
August's numbers. If that is wrong, it is a policy gap to close before month end, not a
code bug.

#### REV-6 · Reverse a non-existent entry

`entry_id: 999999999`, key `ZZT-REV-6`.
**Expect** `404`, `entry_id not found.`

---

### Group F — Unit of measure. The group that can ruin you.

This group targets D1 directly. Run it. Do not skip it because the answer is already known.

Set up: receive 100 Kg FLOUR into AMBIENT, trace `ZZTF`, key `ZZT-F-SETUP`. Record `LOT_F`.
**Balance: 100.000000 Kg.**

#### UOM-1 · Issue in grams against a Kg product

```json
{
  "idempotency_key": "ZZT-UOM-1",
  "lot_id": LOT_F, "location_id": AMBIENT_LOC,
  "quantity": "500", "unit_id": UNIT_G
}
```

The operator means "issue 500 grams" = 0.5 Kg. Correct balance: **99.5**.

**Expect (BUG)** `201`, entry stored as `quantity -500.000000` with `unit_name grams`, and
the balance computed as `100 − 500 = -400.000000`.

`_project_balance()` adds `entry.quantity` to `balance.quantity` with no unit check
(services.py). `to_product_unit()` is never called on this path.

Wait — this should also trip the negative-balance guard. Record which happens:

- **`400 stock_balance: negative without authorised override`** → the guard caught it *by
  luck*, because the wrong number happened to be negative. It would not have caught
  `receive 10 Kg then receive 500 grams` (UOM-2).
- **`201` with a negative balance** → an override was in play; even worse.

Either way the defect is real. Mark **BUG**.

#### UOM-2 · Receive in grams against a Kg product — the silent one

```json
{
  "idempotency_key": "ZZT-UOM-2",
  "product_id": FLOUR_ID, "location_id": AMBIENT_LOC,
  "supplier_id": SUPPLIER_LOC, "trace_number": "ZZTF",
  "quantity": "500", "unit_id": UNIT_G
}
```

Operator means 500 g = 0.5 Kg. Correct balance: **100.5**.

**Expect (BUG)** `201` and balance **`600.000000`**.

**No error. No warning. Nothing negative to trip the guard.** 500 kg of flour that does
not exist is now on the system, and on MySQL that entry can never be edited or deleted.
This is the exact failure mode to protect against: a 1000× overstatement, silently, from a
single dropdown.

Confirm on the entry: `unit_name = grams`, `base_unit_factor = 0.001000`,
`quantity_base = 0.500000`. **The kg column is right while the stock column is wrong** —
so `display_kg` and `quantity` disagree by 1000×. That divergence is your best detector
(check C4).

#### UOM-3 · The kg column gets wiped (D3)

Pick a unit that has **no** `stock_unit_conversion` row for FLOUR — verify first with:

```sql
SELECT * FROM stock_unit_conversion WHERE unit_id = <UNIT_L> AND (product_id = FLOUR_ID OR product_id IS NULL);
```

→ must return zero rows. Then post a receipt on FLOUR with that `unit_id`, key `ZZT-UOM-3`.

**Expect (BUG)** `201`, entry has `base_unit_factor: null` and `quantity_base: null`, and
**`stock_balance.quantity_base` for that lot/location is now NULL** — the previously
accumulated kg total is gone, not just unchanged.

```sql
SELECT lot_id, location_id, quantity, quantity_base FROM stock_balance WHERE lot_id = LOT_F;
```

`quantity_base` was 100.500000 before this call. Confirm it is now NULL. It is never
recomputed from the ledger.

#### UOM-4 · Box and unit carry the same kg factor (D7)

```sql
SELECT u.name, c.to_kg FROM stock_unit_conversion c
JOIN product_unit u ON u.id = c.unit_id
WHERE c.product_id = PATTY_ID AND u.name IN ('unit', 'Box', 'Liter');
```

**Expect (BUG)** three rows with **identical** `to_kg`, all equal to
`product_packaging.unitary_weight`.

Then receive 10 with `unit_id = <Box>` on PATTY and confirm `quantity_base` equals
10 × *one patty's* weight, when a box of 24 should give 10 × 24 × patty weight.

**Mitigation:** do not go live with `Box` as any product's stock unit, and hand-verify
every `Liter` factor (S5).

#### UOM-5 · The safe path — omit `unit_id`

Repeat UOM-2 with **no `unit_id` field at all**.
**Expect** `201`, `unit_name Kg`, `quantity 500.000000` posted in the product's own unit.

This is the contract the warehouse UI must follow: **never send `unit_id`.**
Write this into the frontend acceptance criteria today.

#### UOM-6 · Pack-based goods-in (the one path that converts correctly)

Using a `product_supplier_id` mapping — e.g. 1 sack = 25 Kg, `multiplier` 25,
`inner_unit` Kg:

```json
{
  "idempotency_key": "ZZT-UOM-6",
  "product_id": FLOUR_ID, "location_id": AMBIENT_LOC,
  "product_supplier_id": <mapping id>, "trace_number": "ZZTF-6",
  "quantity": "4"
}
```

4 sacks × 25 = 100 Kg.
**Expect** `201`, `quantity 100.000000` (converted by `packs_to_stock()` →
`to_product_unit()`), `unit_name Kg`, `pack_quantity 4.000000`, `pack_unit_name` = the
outer unit, and the lot stamped with `product_supplier_id` and `shape_format_id`.

Also test a mapping whose product differs from the lot:
**expect** `400`, `product_supplier_id=X is for product_id=Y, lot is product_id=Z`.

**This path is correct.** Compare it against UOM-2 to see exactly what the plain path is
missing.

---

### Group G — Idempotency and duplicate posting

#### IDEM-1 · Same key, same payload

Post RI-1's body again with key `ZZT-RI-1`.
**Expect** `201`, the **same `data.id`** as the original, and **no balance movement**.

#### IDEM-2 · Same key, different quantity

Post key `ZZT-RI-1` with `quantity: "999"`.
**Expect** the **original 100 Kg entry** returned, and `999` silently ignored —
`_insert_entry()` returns the existing row before it looks at anything else.

**This is a real operational trap.** A retry with a corrected quantity is silently
discarded, and the response looks like a success. The UI must generate a fresh
`idempotency_key` for any corrected re-submission, and must display the returned
`quantity` back to the operator rather than the one that was typed.

#### IDEM-3 · Different key, same payload

Post RI-1's body with a fresh key `ZZT-IDEM-3`.
**Expect** `201`, a **new** entry, and the balance up by another 100.

Correct by design, but it means a double-click on a slow connection books stock twice.
Confirm the UI disables submit on click and reuses one key per form submission.

#### IDEM-4 · Transfer half-key collision

Post a receipt using idempotency key `ZZT-IDEM-4:out`, then attempt a transfer with key
`ZZT-IDEM-4` (which internally becomes `:out` and `:in`).

**Expect:** the transfer finds the receipt under `ZZT-IDEM-4:out` and returns it as the
"out" leg. Record what actually happens. Contrived, but it shows the key namespace is
shared across entry types — so the UI must use a prefix scheme (e.g. `RCPT-`, `TRF-`)
rather than raw sequence numbers.

---

### Group H — Period control (removed)

`stock_period` is gone. Skip PER-1 / PER-2 / PER-3.

---

### Group I — Access control

#### AUTH-1 · No token

Post any write with no `Authorization` header.
**Expect** `403`.

#### AUTH-2 · Warehouse user without `goods_in`

Use an operator whose `WarehouseAccess` row has `can_goods_in = false`.
**Expect** `403` on `/stock/receipt/`, but **success** on `/stock/transfer/` (which needs
only general warehouse access) and on `/stock/count-adjustment/`.

**Decide whether that is right.** A user who cannot book goods in *can* currently move
stock between locations and adjust counts. For raw material worth real money, count
adjustment is arguably the most sensitive action in the system, and it is behind the
weakest gate.

#### AUTH-3 · Wrong unit

An operator with access to Unit 11 only, posting against a Unit 2 location.
**Expect** `403`. Confirm the gate checks the *location*, not just the user's unit flag.

---

## Part 3 — Integrity checks. Run these every day after go-live.

Testing tells you the system worked on the cases you thought of. These checks tell you it
is still working on the cases you did not. Run C1–C8 as a nightly job and alert on any
non-empty result.

### C0 — The built-in verification suite

```bash
python manage.py verify_stock_ledger --json
```

Runs four checks from `util/verify.py`:

| check | what it proves |
|---|---|
| `chain_continuity` | every entry is reachable via `prev_hash`; nothing inserted or removed behind the trigger |
| `balance_invariant` | `stock_balance.quantity` == `SUM(stock_entry.quantity)` per lot/location |
| `transfer_atomicity` | every `transfer_group_id` sums to exactly zero |
| `reservation_overbook` | open reservations never exceed on-hand |

Exit code 1 on any failure. Wire this into monitoring on day one.

**`transfer_atomicity` is your D2 detector.** A reversal copies `transfer_group_id` from
the entry it reverses, so reversing one leg of a transfer makes that group sum to
`+quantity` instead of zero, and the check fires. It will not *prevent* the phantom stock,
but it will tell you the same night instead of at the next stock take.

### C1 — Balance drift, with queued entries excluded (D10)

`find_balance_drift()` compares `stock_balance` against `SUM(stock_entry.quantity)` with
**no filter on posting status**. Queued and cancelled entries are in `stock_entry` but
deliberately not in `stock_balance` (`project_balance=False`), so **every queued receipt
and every cancelled entry shows up as drift**.

That matters more than it sounds: your alarm for real corruption will be permanently
ringing with false positives, and the day a genuine drift appears nobody will look.

Use this query for the real signal — it excludes anything not posted, including the
`transfer_in` leg whose paired `transfer_out` is still queued:

```sql
SELECT e.lot_id, e.location_id,
       SUM(e.quantity) AS ledger_qty,
       COALESCE(b.quantity, 0) AS balance_qty,
       SUM(e.quantity) - COALESCE(b.quantity, 0) AS drift
FROM stock_entry e
LEFT JOIN stock_entry_posting po ON po.stock_entry_id = e.id
LEFT JOIN stock_entry_posting pg
       ON pg.stock_entry_id = (
            SELECT o.id FROM stock_entry o
            WHERE o.transfer_group_id = e.transfer_group_id
              AND o.entry_type = 'transfer_out' LIMIT 1)
LEFT JOIN stock_balance b
       ON b.lot_id = e.lot_id AND b.location_id = e.location_id
WHERE COALESCE(po.status, 'posted') = 'posted'
  AND COALESCE(pg.status, 'posted') = 'posted'
GROUP BY e.lot_id, e.location_id, b.quantity
HAVING SUM(e.quantity) <> COALESCE(b.quantity, 0);
```

**Expect zero rows.** Any row means the read model and the ledger disagree — the ledger is
the truth, and someone must work out how the balance diverged before any more stock moves.

Sanity-check the query itself first: queue a receipt (RI-13), confirm
`verify_stock_ledger` reports drift while this query returns nothing, then post the
receipt and confirm both agree.

### C2 — Negative balances

```sql
SELECT b.lot_id, b.location_id, b.quantity, b.negative_authorised_by_entry_id,
       e.override_reason, e.authorised_by_user_id, e.lan_username, e.recorded_at
FROM stock_balance b
LEFT JOIN stock_entry e ON e.id = b.negative_authorised_by_entry_id
WHERE b.quantity < 0;
```

**Expect zero rows in normal operation.** Every row is stock the system believes is owed.
Check that `authorised_by_user_id` is a real, senior user — remember it is unvalidated
(D5), so `999999` will sit there looking official.

### C3 — Unit mismatch. The D1 detector. Run this hourly, not nightly.

```sql
SELECT e.id, e.recorded_at, e.entry_type, e.quantity,
       eu.name AS entry_unit, pu.name AS product_unit,
       p.name AS product, e.lan_username, e.location_id
FROM stock_entry e
JOIN stock_lot l  ON l.id = e.lot_id
JOIN product   p  ON p.id = l.product_id
JOIN product_unit eu ON eu.id = e.unit_id
LEFT JOIN product_unit pu ON pu.id = p.unit_id
WHERE p.unit_id IS NOT NULL
  AND e.unit_id <> p.unit_id
ORDER BY e.id DESC;
```

**Expect zero rows.** Every row is a quantity that was added to the balance without
conversion (D1).

This is the check that protects you from the 1000× error in UOM-2. Catch it within the
hour and a compensating count adjustment is a nuisance. Catch it at month end, after the
number has driven purchase orders, and it is a serious problem.

Run it before go-live too — if it already returns rows, existing balances are wrong today.
`python manage.py fix_ledger_product_unit` (no `--apply`) gives the same list with the
correct converted quantity for each, which is what you need to size the compensating
adjustments.

### C4 — Kg column lost (D3)

```sql
SELECT b.lot_id, b.location_id, b.quantity, b.quantity_base, p.name, u.name AS unit
FROM stock_balance b
JOIN stock_lot l ON l.id = b.lot_id
JOIN product p ON p.id = l.product_id
LEFT JOIN product_unit u ON u.id = p.unit_id
WHERE b.quantity_base IS NULL AND b.quantity <> 0;
```

**Expect zero rows** once every live product has a conversion (S5). A row appearing later
means an entry was posted in a unit with no `stock_unit_conversion`, and the accumulated
kg total for that lot was discarded.

### C5 — Transfer groups that do not net to zero

```sql
SELECT transfer_group_id,
       SUM(quantity) AS net_qty,
       COUNT(*) AS legs,
       GROUP_CONCAT(CONCAT(entry_type, ':', quantity) ORDER BY id) AS detail
FROM stock_entry
WHERE transfer_group_id IS NOT NULL
GROUP BY transfer_group_id
HAVING SUM(quantity) <> 0;
```

**Expect zero rows.** A non-zero group is stock created or destroyed by a transfer —
either a half-reversed transfer (D2) or a leg that failed to roll back. This is the same
logic as `verify_stock_ledger`'s `transfer_atomicity`, shown here so you can read the
detail column and see immediately which leg is unbalanced.

### C6 — Entries stuck in the queue

```sql
SELECT po.status, COUNT(*) AS n, MIN(po.queued_at) AS oldest
FROM stock_entry_posting po
WHERE po.status = 'queued'
GROUP BY po.status;
```

Anything queued for more than a shift is stock that physically arrived but is invisible to
planning. Investigate whether the label was never verified, or the operator walked away.

### C7 — Zero-quantity and sub-precision entries

```sql
SELECT id, entry_type, quantity, unit_id, recorded_at
FROM stock_entry
WHERE quantity = 0 AND entry_type <> 'downtime';
```

**Expect zero rows.** `chk_stock_entry_qty_nonzero` should make this impossible; if RI-10
produced a row here, the constraint is being bypassed by decimal rounding on write and the
UI must cap input at 6 decimal places.

### C8 — Daily conservation check for Unit 2

```sql
SELECT p.name, u.name AS unit,
       SUM(b.quantity) AS on_hand,
       SUM(b.quantity_base) AS on_hand_kg,
       COUNT(DISTINCT b.lot_id) AS lots
FROM stock_balance b
JOIN stock_lot l ON l.id = b.lot_id
JOIN product p ON p.id = l.product_id
LEFT JOIN product_unit u ON u.id = p.unit_id
WHERE b.location_id IN (AMBIENT_LOC, CHILLED_LOC, FROZEN_LOC)
GROUP BY p.name, u.name
ORDER BY p.name;
```

Compare `on_hand × to_kg` against `on_hand_kg` for each row. **They must agree.** A
divergence means either D1 (wrong unit posted) or D3 (kg column wiped), and this single
query catches both without needing to know which.

This is the report to put on a screen the warehouse manager actually looks at.

---

## Part 4 — Go-live gate

Do not launch until every line here is signed off. The failure mode you are protecting
against is not a crash — it is a number that looks fine and is wrong.

### Must fix or must mitigate before 150 people rely on this

| # | Item | Status | Owner |
|---|---|---|---|
| 1 | Warehouse UI never sends `unit_id` on receipt / issue / transfer / count (D1) | ☐ | frontend |
| 2 | C3 unit-mismatch query returns zero rows on current data (D1) | ☐ | |
| 3 | C3 scheduled hourly with an alert to a named person (D1) | ☐ | |
| 4 | "Undo transfer" either reverses both legs or does not exist in the UI (D2) | ☐ | frontend |
| 5 | `verify_stock_ledger` scheduled nightly, exit-1 alerts to a named person | ☐ | |
| 6 | Every live product has a hand-verified `to_kg` (S5, D3, D7) | ☐ | |
| 7 | No live product uses `Box` as its stock unit (D7) | ☐ | |
| 8 | `override_reason` / `authorised_by_user_id` not exposed to floor staff (D5) | ☐ | frontend |
| 9 | Stock count is single-operator per location, or uses `quantity_delta` (D4) | ☐ | ops |
| 10 | UI generates a fresh `idempotency_key` per submission and shows the returned quantity back to the operator (IDEM-2) | ☐ | frontend |
| 11 | Submit button disables on click (IDEM-3) | ☐ | frontend |
| 12 | Decision recorded on whether count-adjustment needs a stronger permission than general warehouse access (AUTH-2) | ☐ | you |
| 13 | Decision recorded on temperature-zone transfer rules (TR-5) | ☐ | you |
| 14 | Decision recorded on fractional quantities for `unit` products (RC-9) | ☐ | you |

### Test execution sign-off

| Group | Cases | Run by | Date | Pass | Fail | Bug (expected) |
|---|---|---|---|---|---|---|
| A — Stock In | RI-1 … RI-13 | | | | | |
| B — Stock Out | IO-1 … IO-7 | | | | | |
| C — Transfer | TR-1 … TR-7 | | | | | |
| D — Reconcile | RC-1 … RC-9 | | | | | |
| E — Reversal | REV-1 … REV-6 | | | | | |
| F — UOM | UOM-1 … UOM-6 | | | | | |
| G — Idempotency | IDEM-1 … IDEM-4 | | | | | |
| H — Period | skipped (`stock_period` removed) | | | n/a | | |
| I — Access | AUTH-1 … AUTH-3 | | | | | |

### Cleanup after testing

Every `ZZTEST_` entry is permanent — the ledger is append-only and the MySQL triggers block
UPDATE and DELETE on `stock_entry`. Do not attempt to delete them.

Instead:

1. Bring every `ZZTEST_` balance to zero with `count_adjustment` (`counted_quantity: 0`),
   which removes the balance row (D6) and takes the products out of every stock report.
2. Set the four `ZZTEST_` products to `is_active = false` — `balance_list_api` and
   `warehouse_remaining_api` both filter on `lot__product__is_active`, so they disappear
   from the UI.
3. Leave the `ZZTEST_` locations in place but unassigned, so the entries keep a valid FK.
4. Exclude `product.name LIKE 'ZZTEST%'` from any report you build.

Do **not** run the go-live period close until step 1 is done, or the test quantities will
land in the closing figures.
