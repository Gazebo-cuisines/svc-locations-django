# PLANNING_UI.md — Chunk 4 (Signed)

**Status:** COMPLETE  
**Date:** 2026-07-31  
**Folder:** `planning-redesign/chunk-04-ui/`  
**Depends on:** [API](../chunk-03-api/PLANNING_API.md), [User journey](../USER_JOURNEY_CREATE_PLAN.md)  
**Code later:** Chunk 8 in `gazeboo-cloud-web` on branch `Plan`

Copy patterns from `src/features/stock/` — `PageShell`, `DataTable`, `FilterBar`, `StatusBadge`, `apiFetch` + Cognito Bearer.

---

## 1. Feature folder layout (Chunk 8 will create)

```
src/features/planning/
  api/
    planningClient.ts      # all /planning/* calls
    planningMappers.ts     # envelope unwrap + types
    index.ts
  pages/
    PlansListPage.tsx
    PlanCreatePage.tsx
    PlanDetailPage.tsx     # lines + actions + tabs
    PlanRequirementsPage.tsx  # or tab inside detail
    PlanSupplyPage.tsx     # optional MVP screen
    index.ts
  hooks/
    usePlanDetail.ts
    usePlanMutations.ts
  types.ts
  index.ts
```

---

## 2. Routes (`App.tsx`)

| Route | Page | Notes |
|-------|------|--------|
| `/planning/plans` | PlansListPage | Default Planning home |
| `/planning/plans/new` | PlanCreatePage | Step 1 |
| `/planning/plans/:planId` | PlanDetailPage | Lines, run, allocate, commit, lock/close |
| `/planning/plans/:planId/requirements` | PlanRequirementsPage | Optional deep link; can be a tab on detail |
| `/planning/supply` | PlanSupplyPage | Incoming supply CRUD |

Redirect `/planning` → `/planning/plans`.

---

## 3. Nav + dashboard

**`src/data/nav.ts`** — Planning group (today stub, no href):

| Link | href |
|------|------|
| Production planning | `/planning/plans` |
| Expected supply | `/planning/supply` |
| Demand forecast | leave **no href** until Chunk 10 |

**`DashboardPage`** — change Planning tile from `#` → `/planning/plans`.

---

## 4. Screens (Priya journey)

### 4.1 Plans list — `/planning/plans`

**UI:** `PageShell` title “Production plans” + primary button **New plan**.  
**Filters:** date from/to, location, status (draft/locked/closed).  
**Table columns:** Date | Location | Status | Lines | Latest run | Updated | Open.

| User action | API |
|-------------|-----|
| Load / filter | `GET /planning/plans/?…` |
| New plan | navigate `/planning/plans/new` |
| Row click | `/planning/plans/:id` |

**Status badges:** draft = neutral, locked = info, closed = muted.

---

### 4.2 Create plan — `/planning/plans/new`

**Form fields:** Plan date (required), Location / department (required, dropdown from locations), Remarks (optional).

| Action | API | Next |
|--------|-----|------|
| Save | `POST /planning/plans/` | Go to `/planning/plans/:id` |
| Cancel | — | Back to list |

**Error:** 409 → show “A plan already exists for this date and location” with link to existing id if returned.

---

### 4.3 Plan detail — `/planning/plans/:planId` (main screen)

**Header:** date, location name, status badge, remarks.  
**Action bar** (enable by status):

| Button | Visible when | API |
|--------|--------------|-----|
| Run plan | draft or locked | `POST .../runs/` |
| Commit allocations | draft or locked; has soft allocs | `POST .../commit/` |
| Lock | draft | `POST .../lock/` |
| Close | draft or locked | `POST .../close/` |
| Reopen | closed | `POST .../reopen/` |

Confirm dialogs on Close / Reopen / Commit.

**Tabs:**

1. **Lines** (default)  
2. **Requirements**  
3. **Activity** (events)

#### Tab: Lines

Table: Product | Qty | Unit | Source | Recipe version | Actions.

- Draft only: Add line, Edit, Delete.  
- Add line form/modal: product search, quantity, unit, optional recipe version, source default `manual`.

| Action | API |
|--------|-----|
| Load | `GET .../lines/` or lines on plan GET |
| Add | `POST .../lines/` |
| Edit | `PATCH .../lines/:id/` |
| Delete | `DELETE .../lines/:id/` |

Example: add product 501, qty 100, unit tray → same as journey Step 2.

#### Tab: Requirements

After at least one successful run:

- Run selector (latest first): run #, status, time.  
- Table or indented tree: Level | Product | Batch | Net | Gross | On hand | Balance | Closed | Actions.

| Action | API |
|--------|-----|
| Load runs | `GET .../runs/` |
| Load reqs | `GET .../runs/:runId/requirements/` |
| Allocate (row with balance > 0) | open Allocate modal → `POST /planning/requirements/:id/allocations/` |
| Remove soft alloc | `DELETE /planning/allocations/:id/` |

**Allocate modal:** lot picker (show use-by for FEFO), location, quantity. Show API 422 messages plainly (“Lot too short shelf life” / “Not enough available”).

While run `status=running` (async later): show spinner, poll run every 2s.

#### Tab: Activity

Timeline from `GET .../events/` — created, run_complete, committed, locked, closed, etc.

---

### 4.4 Expected supply — `/planning/supply`

Simple CRUD list (MVP optional but wired):

Columns: Product | Location | Expected at | Qty | Kind | Document | Actions.

| Action | API |
|--------|-----|
| List | `GET /planning/supply/` |
| Add / edit / delete | POST / PATCH / DELETE |

---

## 5. Client API binding (`planningClient.ts`)

Mirror `stockClient.ts`:

| Function | HTTP |
|----------|------|
| `listPlans(params)` | GET `/planning/plans/` |
| `createPlan(body)` | POST `/planning/plans/` |
| `getPlan(id)` | GET `/planning/plans/:id/` |
| `patchPlan(id, body)` | PATCH |
| `lockPlan` / `closePlan` / `reopenPlan` | POST `.../lock|close|reopen/` |
| `listLines` / `createLine` / `patchLine` / `deleteLine` | lines routes |
| `startRun(planId, { async? })` | POST `.../runs/` |
| `listRuns` / `getRun` | GET runs |
| `listRequirements(planId, runId)` | GET requirements |
| `createAllocation` / `listAllocations` / `deleteAllocation` | allocation routes |
| `commitPlan(planId, body?)` | POST `.../commit/` |
| `listEvents(planId)` | GET events |
| `listSupply` / `createSupply` / … | supply routes |

Use existing `unwrapData` / envelope helpers from recipe or stock.

---

## 6. UX rules (keep simple)

- Quantities display with sensible decimals; send as strings to API.  
- Disable line edits when status ≠ draft.  
- After **Run plan**, switch to Requirements tab and toast success or show failure message from run.  
- After **Commit**, refresh allocations so `stock_reservation_id` shows.  
- Global API errors: existing `ApiErrorPopup` via `apiFetch`.  
- No resource board, no forecast screens in MVP.  
- Reuse `managers.css` / shared UI — no new design system.

---

## 7. Screen flow (Priya)

```
List → New plan (date + Kitchen)
    → Detail Lines: add 100 samosas
    → Run plan
    → Requirements: see pastry/flour; Allocate flour lot
    → Commit
    → Lock
    → (later) Close
```

---

## 8. Chunk 8 build checklist

- [ ] Create `src/features/planning/` as above  
- [ ] Register routes in `App.tsx`  
- [ ] Wire `nav.ts` + dashboard href  
- [ ] Implement list / create / detail (3 tabs) / supply  
- [ ] Types aligned with Chunk 3 JSON shapes  
- [ ] Manual walkthrough of user journey against backend on `Plan`

---

## 9. Sign-off

- [x] Screens map 1:1 to API journey  
- [x] Folder + routes + nav specified  
- [x] Matches stock feature conventions  
- [x] Forecast/resource UI deferred  

**Chunk 4 complete.** Design phase (1–4) done. Next: approve **Chunk 5** (Django models + migrations on `Plan`).
