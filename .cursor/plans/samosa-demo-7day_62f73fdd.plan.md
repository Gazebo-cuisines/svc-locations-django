---
name: samosa-demo-7day
overview: Deliver a single end-to-end samosa demo proving calculation accuracy, speed, and traceability compliance behavior, without full legacy migration.
todos:
  - id: freeze-scope
    content: Write one-page samosa fixture and lock no-scope-creep rules
    status: pending
  - id: build-trace-core
    content: Create lot/batch/event/ccp/cost schema and migrations
    status: pending
  - id: implement-guardrails
    content: Enforce hold/release and scan-first dispatch blocking
    status: pending
  - id: implement-recall-massbalance
    content: Deliver genealogy and mass-balance queries with tests
    status: pending
  - id: implement-costing
    content: Compute material+labor+overhead true cost per kg
    status: pending
  - id: demo-rehearsal
    content: Run end-to-end script with timing and evidence artifacts
    status: pending
isProject: false
---

# 7-Day Samosa Proof Plan

## Non-Negotiable Goal
Prove one claim only: for one samosa product, the system returns full lot trace + mass balance quickly and computes true batch cost accurately.

## Ruthless Scope Cuts
- Do **not** migrate full legacy categories/master data.
- Do **not** rebuild every legacy screen.
- Do **not** implement multi-product/multi-line support.
- Do **not** optimize for generic engine design this week.

## Demo Slice (One Product, One Line)
- Product: single samosa SKU.
- Process stages: goods-in -> prep -> cooking -> frying -> packing -> branding -> dispatch.
- Single plant, single line, limited operators.
- Barcode scan required at each movement in demo flow.

## Minimum Data Model to Add
Target app likely under new `traceability` (or equivalent) Django app.

Core entities:
- `lot`: immutable lot record (status: quarantine/hold/released, source, timestamps).
- `batch`: production run header for samosa run.
- `lot_consumption`: parent lot consumed by batch with quantity.
- `lot_output`: child lot produced by batch with quantity.
- `lot_movement`: stage/location transitions with scan event + operator.
- `ccp_reading`: fryer temp/core temp/metal detector with limits + pass/fail.
- `corrective_action`: mandatory when CCP fail; links to auto-hold lot/batch.
- `work_order_labor`: crew-hours logged per batch.
- `cost_snapshot`: standard vs actual material/labor/overhead per batch.

Rules:
- Append-only events for transactional tables (no hard delete).
- Every write stores actor + timestamp + reason.
- Unreleased lots blocked from dispatch action.

## APIs/Queries Needed for Demo
- Create goods-in lot (raw material).
- Start batch and consume parent lots.
- Record CCP readings by process step.
- Produce child lots and move through stages.
- Dispatch released lot only.
- Recall query endpoint: given dispatched lot, return backward + forward genealogy.
- Mass balance endpoint: input kg, output kg, variance %, pass/fail threshold.
- Cost endpoint: true cost/kg for the batch.

## Acceptance Gates (Must Pass)
- Recall result includes full backward + forward links for samosa dispatched lot.
- Mass balance report generated with explicit variance for that batch.
- Out-of-spec CCP reading automatically sets lot/batch to hold and requires corrective action before release.
- Dispatch attempt fails for hold/quarantine lots.
- Cost/kg output includes material + labor + overhead and matches expected fixture values.
- End-to-end query response time within demo target (set and measure).

## 7-Day Execution

### Day 1
- Freeze scope and write fixture story for one samosa batch.
- Define schema and migration plan for lot/batch/event/cost tables.

### Day 2
- Implement models + migrations + admin visibility.
- Seed deterministic fixture data for one end-to-end batch.

### Day 3
- Implement transaction APIs for goods-in, consume, produce, move, dispatch.
- Enforce status and scan gates.

### Day 4
- Implement CCP + corrective action + auto-hold workflow.
- Add unit tests for fail/pass transitions.

### Day 5
- Implement genealogy recall + mass balance query.
- Add performance timing and indexes for genealogy path.

### Day 6
- Implement costing calculation (standard vs actual, cost/kg).
- Backtest against manual spreadsheet for fixture parity.

### Day 7
- Demo script rehearsal: happy path + failure path (CCP fail then corrective release).
- Produce evidence pack (API outputs, timings, reconciliation report).

## Demo Script (What You Show Stakeholders)
1. Receive raw potato/spice/flour lots.
2. Start samosa batch; consume lots with scanned IDs.
3. Enter CCP readings across cooking/frying; trigger one intentional fail.
4. Show auto-hold and blocked dispatch.
5. Add corrective action; release lot.
6. Dispatch final lot.
7. Run recall query on dispatched lot.
8. Show mass balance and cost/kg report with timing.

## Legacy Strategy (So You Don’t Drown)
- Treat legacy as reference, not migration backlog.
- Extract only fields needed for this demo slice.
- Map remaining legacy tables as “deferred”.
- Keep a `legacy_gap_log.md` listing deferred modules and rationale.

## Files to Prioritize
- Existing: [`product/models.py`](product/models.py), [`locations/models.py`](locations/models.py)
- New likely files:
  - [`traceability/models.py`](traceability/models.py)
  - [`traceability/views.py`](traceability/views.py)
  - [`traceability/services/genealogy.py`](traceability/services/genealogy.py)
  - [`traceability/services/mass_balance.py`](traceability/services/mass_balance.py)
  - [`traceability/services/costing.py`](traceability/services/costing.py)
  - [`traceability/tests/test_samosa_e2e.py`](traceability/tests/test_samosa_e2e.py)

## Personal Operating Rules (Ruthless)
- If a task doesn’t move the samosa demo, kill it.
- If a field isn’t used by a demo query, defer it.
- If a refactor takes >2 hours without demo impact, stop.
- Ship working ugly over elegant incomplete.
