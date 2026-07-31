# User journey — create a plan (with example data)

**Story:** Priya plans Monday cooking for **Kitchen**. She needs **100 trays of Samosas**.

IDs below are made-up so you can follow the rows.

---

## Step 1 — Start a new plan

Priya chooses date **2026-08-04** and location **Kitchen** (id `10`).

### Table: `plan`

| id | plan_date  | location_id | status | remarks              | created_by_user_id |
|----|------------|-------------|--------|----------------------|--------------------|
| 1  | 2026-08-04 | 10          | draft  | Monday samosa run    | 42                 |

**Meaning:** One plan exists for Kitchen on that day. Still editable.

---

## Step 2 — Add what to make

She adds: product **Samosa tray** (id `501`), quantity **100**, unit **tray** (id `3`).

### Table: `plan_line`

| id | plan_id | product_id | quantity | unit_id | source | recipe_version_id |
|----|---------|------------|----------|---------|--------|-------------------|
| 1  | 1       | 501        | 100.000000 | 3     | manual | 88                |

**Meaning:** “This plan asks for 100 samosa trays.”  
Recipe version `88` is the formula she pinned (or the system will pick one when she runs).

Nothing exploded yet. No stock touched.

---

## Step 3 — Run the plan (break the recipe)

Priya clicks **Run plan**.

### Table: `plan_run` (new snapshot)

| id | plan_id | run_number | status   | driver_version | started_at          | completed_at        |
|----|---------|------------|----------|----------------|---------------------|---------------------|
| 1  | 1       | 1          | complete | explode-1.0    | 2026-08-03 09:00:00 | 2026-08-03 09:00:02 |

### Table: `plan_requirement` (what must be made / used)

Assume pastry already has some stock, so netting reduced pastry need.

| id | run_id | plan_line_id | parent_requirement_id | level | batch_number | product_id | product (name) | net_required | gross_required | yield_factor | process_loss | stock_on_hand | balance | closed |
|----|--------|--------------|----------------------|-------|--------------|------------|----------------|--------------|----------------|--------------|--------------|---------------|---------|--------|
| 10 | 1      | 1            | NULL                 | 1     | 1            | 501        | Samosa tray    | 100.000000   | 105.000000     | 1.000000     | 0.952381     | 0.000000      | 105.000 | false  |
| 11 | 1      | NULL         | 10                   | 2     | 1            | 502        | Pastry sheet   | 40.000000    | 42.000000      | 1.000000     | 0.952381     | 20.000000     | 22.000  | false  |
| 12 | 1      | NULL         | 10                   | 2     | 1            | 503        | Potato filling | 55.000000    | 58.000000      | 1.000000     | 0.948276     | 0.000000      | 58.000  | false  |
| 13 | 1      | NULL         | 11                   | 3     | 1            | 504        | Flour          | 15.000000    | 15.000000      | 1.000000     | 1.000000     | 50.000000     | 0.000   | false  |

**How to read one row (pastry, id 11):**

- Parent = samosa (id 10)  
- Needed ~42 kg gross, but 20 kg already on hand → **balance left to make ≈ 22**  
- Flour (id 13) was fully covered by stock → balance **0** (nothing extra to buy/make)

Also written: `source_location_id`, `destination_location_id`, `min_shelf_life_days`, `recipe_version_id` on each row (snapshots of what the engine used).

---

## Step 4 — Earmark stock lots (soft)

Priya says: use flour lot **L-9001** from Cold Store (location `20`) for the flour line.

### Table: `plan_allocation`

| id | requirement_id | lot_id | location_id | quantity   | stock_reservation_id |
|----|----------------|--------|-------------|------------|----------------------|
| 1  | 13             | 9001   | 20          | 15.000000  | NULL                 |

**Meaning:** “We intend to use 15 kg from lot 9001.”  
`stock_reservation_id` is empty → not firmly held in the warehouse yet.

---

## Step 5 — Commit (firm hold)

Priya clicks **Commit**.

### Table: `plan_allocation` (updated)

| id | requirement_id | lot_id | location_id | quantity  | stock_reservation_id |
|----|----------------|--------|-------------|-----------|----------------------|
| 1  | 13             | 9001   | 20          | 15.000000 | **7001**             |

### Existing stock system table: `stock_reservation` (created by commit)

| id   | lot_id | location_id | quantity  | status | source_document_type | source_document_id | source_document_line |
|------|--------|-------------|-----------|--------|----------------------|--------------------|----------------------|
| 7001 | 9001   | 20          | 15.000000 | open   | plan                 | 1                  | 13                   |

**Meaning:** Warehouse now holds 15 kg of that lot for **this plan** (plan id 1, requirement 13).

### Table: `plan_event`

| id | plan_id | event_type | payload_json (example)        | actor_user_id | created_at          |
|----|---------|------------|-------------------------------|---------------|---------------------|
| 1  | 1       | created    | {"location_id": 10}           | 42            | 2026-08-03 08:55:00 |
| 2  | 1       | run_complete | {"run_id": 1, "run_number": 1} | 42          | 2026-08-03 09:00:02 |
| 3  | 1       | committed  | {"allocations": 1}            | 42            | 2026-08-03 09:10:00 |

---

## Step 6 — Lock the plan (sign-off)

Priya locks so nobody changes the “100 samosas” line.

### Table: `plan` (updated)

| id | plan_date  | location_id | status   | remarks           |
|----|------------|-------------|----------|-------------------|
| 1  | 2026-08-04 | 10          | **locked** | Monday samosa run |

New `plan_event`: `locked`.

---

## Optional anytime — expected deliveries

Someone records: “50 kg flour arriving Tuesday morning.”

### Table: `plan_supply`

| id | product_id | location_id | expected_at         | quantity  | unit_id | kind            | source_document_type | source_document_id |
|----|------------|-------------|---------------------|-----------|---------|-----------------|----------------------|--------------------|
| 1  | 504        | 20          | 2026-08-05 08:00:00 | 50.000000 | 2       | purchase_order  | po                   | 1205               |

**Meaning:** When running a plan, the system can count this incoming flour if the need date is on/after that arrival — so it does not over-order.

---

## End of day — close

### Table: `plan`

| id | status   |
|----|----------|
| 1  | **closed** |

Open stock holds for this plan are released. Requirements marked closed where finished. Event: `closed`.

If unfinished work ages out, unfinished **plan_line** quantities can be copied onto the **next day’s plan** (rollover) instead of being thrown away.

---

## Picture of the whole flow

```
plan              →  “Kitchen plan for 4 Aug”
plan_line         →  “Make 100 samosas”
plan_run          →  “Run #1 finished”
plan_requirement  →  “Samosas / pastry / filling / flour + how much”
plan_allocation   →  “Use lot 9001 for flour” (soft, then linked)
stock_reservation →  “Warehouse hold 15 kg for this plan”
plan_event        →  “Diary of what Priya did”
plan_supply       →  “Flour truck Tuesday” (optional)
```

---

## If she runs the plan again

A **new** `plan_run` appears (run_number = 2) with a **new** set of `plan_requirement` rows.  
Run #1 rows stay as history. Soft allocations for the old run are not reused blindly — she allocates against the latest complete run.
