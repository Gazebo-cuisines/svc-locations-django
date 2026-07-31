# PLANNING_REQUIREMENTS.md — Chunk 1 (Signed)

**Status:** APPROVED — analysis complete  
**Date:** 2026-07-31  
**Sources:** [PHASE1_LEGACY_PLANNING_AUDIT.md](../../vb-access-legacy/PHASE1_LEGACY_PLANNING_AUDIT.md), MySQL dump `vb-access-legacy/data/Dump20260720.sql`, VBA `vb-access-legacy/VB Access/modules/mdlODBC-Calls.bas`, draft design [new_planning_module_59e66299.plan.md](../../vb-access-legacy/.cursor/plans/new_planning_module_59e66299.plan.md), modern product flags in `svc-locations-django/product/models.py`

**Method:** Map legacy *intent* (not observed behaviour). Legacy planning transactional tables never held a row; Access called missing SPs (`bomrecursive2/5/7`); real engine `BOMrecursive999Wrapper` was orphaned. NotaZone KB is UX inspiration only — not Gazebo behaviour.

---

## 0. Headline findings (non-negotiable context)

| Finding | Evidence | Implication |
|---|---|---|
| Planning never ran in production | All plan/BOM/resource-plan tables `AUTO_INCREMENT = 1` | No behaviour to preserve; rebuild from concepts |
| MRP not invokable | VBA → missing procs; wrapper never called | Do not port SP names or two-sweep MEMORY design |
| No DB locking | Zero `FOR UPDATE` / `GET_LOCK` in dump | New system must use `select_for_update` |
| Shelf life never enforced | `minshelflife` written 200+ times, never compared | Hard gate in modern eligibility |
| Dual reservations | Plan queue + batch queue unreconciled | Single `StockReservation` path only |
| Capacity never wired | Schedule tables empty/unreferenced | Finite capacity = net-new (Chunk 9+) |

---

## 1. Scope inventory

### 1.1 In scope for modern Planning (eventually)

| Legacy area | Access / MySQL objects | Chunk |
|---|---|---|
| Daily production plan | `frmPlanMaster`, `tblplanmaster`, `tblplanmasterdetails` | MVP (5–8) |
| MRP / BOM explode | `BOMrecursive*`, `tblBOMaggregate*`, `tblproducttree` (via recipe) | MVP |
| Stock netting / soft alloc | `tblplanmasterdetailsStockAllocations`, stock cache fns | MVP |
| Plan lock / close / ageing | `procOPSPlanIDlock/close`, `PLANAHEADDAYS`, scheduler | MVP (fixed) |
| Projected inbound supply | (intent only; no clean legacy table) | MVP table; populate manually until PO |
| Resource sequencing board | `frmFourWeekAheadPlan`, `tblplanresource*`, `tblResources`, `resourceplansort` | Chunk 9 |
| Demand / 4-week / shortage | trends SPs, `ProjectedPlan*`, `ShortStock` | Chunk 10 |
| Plan picking lists | `frmPlanMasterPickingLists`, `tblBOMaggregatePickingLists` | After MVP alloc |

### 1.2 Out of scope (unless re-opened)

| Area | Objects | Reason |
|---|---|---|
| Staff allocation | `frmOPSstaffAllocationHeader` | Separate HR/ops concern; not core MRP |
| Access RBAC form levels | `tblUsersResourcesMinimumLevel` | Misleading name = form ACL, not capacity |
| NotaZone Product Line / Create-by-Weight | KB only | Different product |
| Porting any MySQL SP / trigger / EVENT for planning | Entire planning SP family | Explicit project rule |
| Global MEMORY / scratch tables | `tblBOMaggregateMEMORY`, `*TMP` | Concurrency hazard |
| `jobList` pipe-delimited text | `tblResourcesQueuePlan.jobList` | Unfit; normalize later |

### 1.3 Already migrated upstream (Planning consumes, does not redefine)

| Domain | Modern home | Planning uses via |
|---|---|---|
| Products + plan flags | `product` (`has_plan`, `consider_stock_in_plan`, `full_batches_only`, `align_unitary_weight`, `gross_unitary_weight`, `absolute_min_shelf_life_days`, `relative_plan_position`, rates) | Adapter |
| Recipe / BOM tree | `recipe` (`RecipeVersion`, `RecipeComponent`) | Adapter — pin version at run |
| Locations / departments | `locations` (`Location`, `LocationStockProfile`) | Adapter — `plannable_locations()` |
| Stock ATP / lots / reservations | `stock_ledger` | Adapter — commit → `StockReservation` `source_document_type='plan'` |

---

## 2. Requirements matrix

Decision values: **Keep** | **Fix** | **Drop** | **New**  
MVP column: **Y** = Chunks 5–8 | **D** = deferred (9/10) | **N** = not building

### 2.1 Plan header & demand

| ID | Legacy concept | Legacy intent | Decision | Modern requirement | MVP |
|---|---|---|---|---|---|
| R-01 | Plan per date + department | One `tblplanmaster` per `plandate` + `department` | Keep | One `plan` per `(plan_date, location_id)`; unique constraint | Y |
| R-02 | Plan remarks / audit meta | `remarks`, user, workstation | Fix | `remarks`, `created_by`, timestamps; drop lanusername/workstation/IP as first-class fields (optional audit JSON later) | Y |
| R-03 | FG demand lines | `tblplanmasterdetails`: item, qty, overrides | Keep | `plan_line`: product, quantity, unit, optional overrides (consider stock, batch split, last-batch full) | Y |
| R-04 | Line source | Implicit (manual only in practice) | New | `source` ∈ `manual` \| `order` \| `forecast`; MVP writes `manual` (and `forecast` only if seeded) | Y |
| R-05 | Item bundles | `tblplanmasterItemBundles*` | Drop | Out of MVP; add later if ops need reusable kits | N |
| R-06 | Plan log trail | `tblplanmasterlogtrail` (no SQL writer) | New | Application audit events on status/run/commit (not a dead table) | Y |

### 2.2 MRP / BOM explosion

| ID | Legacy concept | Legacy intent | Decision | Modern requirement | MVP |
|---|---|---|---|---|---|
| R-10 | Multi-level BOM explode | Recursive CTE on `tblproducttree` | Keep | Explode from pinned `recipe_version` components; max depth + cycle detection | Y |
| R-11 | Two-sweep MRP + MEMORY table | Net then batch-split with re-explode | Fix | Single-pass ordered explode; batch split before descending; persist only to `plan_requirement` under immutable `plan_run` | Y |
| R-12 | Net vs gross | `gross = net / process_loss`; child from parent driver | Fix | **D1 locked:** parent **gross** drives child at every level. `net(child) = parent.gross × qty / yield(child)`; `gross(child) = net(child) / process_loss(child)` | Y |
| R-13 | Process loss | From active recipe | Keep | From pinned recipe version `process_loss`; never resolve “active” mid-run | Y |
| R-14 | Component yield | `productyield` / tree | Keep | Product yield via adapter; default `1` if missing | Y |
| R-15 | Recipe pin | Version on detail sometimes | Fix | Every requirement stores `recipe_version_id` used for that run | Y |
| R-16 | Run revision | `revision` never populated | New | `plan_run` with monotonic `run_number`; prior runs immutable and retained | Y |
| R-17 | Default resource on wrap-up | Assign `assignedresource` | Defer | Store `default_resource_id` on requirement when available; sequencing in Chunk 9 | D |
| R-18 | SP engine / VBA wrappers | `bomrecursive2/5/7`, `999Wrapper` | Drop | Python services only; zero planning SPs | Y |

### 2.3 Stock netting, eligibility, allocation

| ID | Legacy concept | Legacy intent | Decision | Modern requirement | MVP |
|---|---|---|---|---|---|
| R-20 | Consider stock in plan | Flag `genconsiderstockinplan` + overrides | Keep | Use `ProductFlags.consider_stock_in_plan` + per-line override | Y |
| R-21 | Netting as-of date | Always `curdate()` | Fix | Net as-of **plan.plan_date** | Y |
| R-22 | Stock visibility | Single dest container + cache | Fix | `plannable_locations(product)` = real_stock locations, prefer source; adapter seam | Y |
| R-23 | Deduct other open plans | Via committed cache | Fix | Deduct open `StockReservation` + other open plan soft allocs not yet committed | Y |
| R-24 | Deduct batch queue separately | Second reservation system | Drop | One reservation system only (`stock_ledger`) | Y |
| R-25 | Min/max stock bounds | Applied in modifiers | Keep | Apply product min/max when netting if configured | Y |
| R-26 | Min shelf life | Written, never compared | Fix | **D6:** lot eligible iff `use_by − plan_date ≥ max(product.absolute_min_shelf_life_days, location_min, 0)` | Y |
| R-27 | Allocation order | `tracenumber ASC` | Fix | **D7:** FEFO — `use_by` ASC, then `production_date`, then `lot_id` | Y |
| R-28 | Soft vs hard allocation | Plan alloc table + close releases | Fix | `plan_allocation` soft; **commit** creates `StockReservation`; close releases | Y |
| R-29 | Projected supply | None clean (cache forecasts broken) | New | `plan_supply` (PO / production_output / opening) included in ATP when `expected_at ≤ need_by` | Y |
| R-30 | Dual queue / MEMORY scratch | Global working tables | Drop | In-memory / DB rows scoped to `plan_run` only | Y |

### 2.4 Batch splitting

| ID | Legacy concept | Legacy intent | Decision | Modern requirement | MVP |
|---|---|---|---|---|---|
| R-40 | Full batches only | `genfullbatches` | Keep | `ProductFlags.full_batches_only`; `ceil(gross / gross_unitary_weight)` | Y |
| R-41 | Last-batch align | `unitaryweightalign` | Keep | **D5:** if `align_unitary_weight`, round last batch up to full; else remainder | Y |
| R-42 | Batch number inheritance | Children copy parent batch | Keep | `batch_number` on requirement; children inherit | Y |
| R-43 | Batch mangle mid-plan | Continue numbering on re-run | Drop | Replan = new `plan_run`; renumber from 1; no append mangle | Y |

### 2.5 Lifecycle & concurrency

| ID | Legacy concept | Legacy intent | Decision | Modern requirement | MVP |
|---|---|---|---|---|---|
| R-50 | Lock | `planLocked` editorial freeze | Keep | Status `locked`: no line edits; explode still allowed until closed (policy: explode only in `draft`/`locked`) | Y |
| R-51 | Close | Terminal; closes BOM; rebuilds queues | Keep | Status `closed`: release soft allocs + reservations; requirements closed | Y |
| R-52 | Reopen | None | New | `closed → draft` allowed with audit; clears closed flags; does not revive deleted runs | Y |
| R-53 | Ageing auto-close | `PLANAHEADDAYS` (5); force-close open work | Fix | **D2:** management command ages plans; **rollover** unfinished open lines to next plan date instead of silent kill | Y |
| R-54 | Row-level job close | Close BOM row when stock satisfies net | Keep | Requirement `closed` when allocated/produced qty meets net; reversible on reservation release/void | Y |
| R-55 | Concurrency | Fake SERIALIZABLE, no locks | Fix | `select_for_update` on `plan` for entire explode/commit; different plans concurrent | Y |
| R-56 | Nightly EVENT | `dailydbrefresh` → scheduler | Fix | Django management commands / cron — no MySQL EVENT for planning | Y |

### 2.6 Resource scheduling (deferred)

| ID | Legacy concept | Legacy intent | Decision | Modern requirement | MVP |
|---|---|---|---|---|---|
| R-60 | Resource master | `tblResources` (33 live) | Keep | Normalize `resource` table when Chunk 9 starts | D |
| R-61 | Resource-day plan | `tblplanresourcemaster/details` | Defer | Chunk 9 | D |
| R-62 | Infinite-capacity sequence | `resourceplansort` chains by position | Keep (for Chunk 9) | **D3:** MVP has no board; Chunk 9 = infinite capacity sequence only | D |
| R-63 | Finite capacity / shifts | Empty schedule tables | New (later) | Explicit future epic; not Chunk 9 | N |
| R-64 | Backward timing gap/dwell | Arrival/start from parent start | Defer | Use `ProductProduction` rates/gap/dwell in Chunk 9 | D |
| R-65 | Queue jobList text | GROUP_CONCAT pseudo-array | Drop | Normalized job↔resource rows only | D |
| R-66 | Staff allocation forms | OPS staff header | Drop | Out of Planning MVP | N |

### 2.7 Forecasting / shortage (deferred)

| ID | Legacy concept | Legacy intent | Decision | Modern requirement | MVP |
|---|---|---|---|---|---|
| R-70 | OLS weekly regression | Broken / typo procs | Drop | Do not port OLS | D |
| R-71 | 5-day projected plan + ShortStock | Valuable shape | Fix | Chunk 10: BOM-aware shortage over short horizon | D |
| R-72 | Demand source | Customer orders preferred | Keep | Confirmed orders beat forecast; until orders module exists, manual lines | D |
| R-73 | Day-of-week profiles | Recommended replacement | New | `demand_profile` (weekday mean + sample_count); **D8:** not MVP | D |
| R-74 | Hardcoded 5/7 calendar | No holiday support | Drop MVP | **D4:** no working-calendar table in MVP; skip non-production days by not creating plans | N |
| R-75 | Stock runway metric | on-hand ÷ avg daily sale | Defer | Chunk 10 reporting | D |

### 2.8 Platform / anti-patterns

| ID | Legacy concept | Decision | Modern requirement | MVP |
|---|---|---|---|---|---|
| R-80 | Access `-1` boolean | Drop | Real booleans / status enums | Y |
| R-81 | Planning SPs + triggers | Drop | All rules in Django services | Y |
| R-82 | Cross-module joins from planning | Fix | Adapters + frozen dataclasses + `CONTRACT_VERSION` | Y |
| R-83 | Separate planning microservice | Drop (for now) | Peer app in `svc-locations-django` | Y |
| R-84 | Interactive vs batch | New | **D9:** Interactive explode; sync until ~10k rows / ~30s; then async `plan_run.status` poll — schema ready either way | Y |

---

## 3. Locked decisions (D1–D9)

| ID | Decision | Locked answer |
|----|----------|---------------|
| **D1** | What drives child BOM qty? | **Parent gross → child** at every level |
| **D2** | Aged unfinished plan? | **Rollover** open unfinished lines to a new/next plan; do not silent force-close work |
| **D3** | Finite capacity? | **Out of MVP.** Chunk 9 = infinite-capacity sequencing only |
| **D4** | Working calendar? | **Out of MVP.** No plan rows for non-production days |
| **D5** | Last-batch full align? | **Keep** product `align_unitary_weight` behaviour |
| **D6** | Shelf life? | **Hard constraint** on netting and allocation (rule in R-26) |
| **D7** | Allocation order? | **FEFO** |
| **D8** | Forecasting in MVP? | **No.** Manual plan lines; profiles/shortage = Chunk 10 |
| **D9** | Scale assumption? | Interactive multi-planner; row-lock per plan; design for async later without schema rewrite |

Already recorded (unchanged):

- One source + one destination location per product (routing intentional)
- Planning stock visibility via `plannable_locations(product)` seam

---

## 4. MVP boundary (Chunks 5–8 will implement)

**In:**

1. Create/edit/list plans + demand lines  
2. Explode BOM → immutable `plan_run` + `plan_requirement` tree  
3. Stock netting as-of plan date + FEFO eligibility + soft `plan_allocation`  
4. Commit → `StockReservation` (`source_document_type='plan'`)  
5. Lifecycle draft → locked → closed → reopen  
6. Ageing command with rollover  
7. Manual `plan_supply` CRUD for ATP  
8. Adapters to product / recipe / locations / stock  
9. HTTP under `/planning/` + React feature for the above  

**Out of MVP:**

- Resource board / timing (Chunk 9)  
- Demand profiles / 4-week / ShortStock UI (Chunk 10)  
- Picking lists, staff allocation, finite capacity, working calendar  
- Item bundles  

---

## 5. Legacy table → concept map (input to Chunk 2 ERD)

| Legacy table | Disposition | Modern concept |
|---|---|---|
| `tblplanmaster` | Map | `plan` |
| `tblplanmasterdetails` | Map | `plan_line` |
| `tblplanmasterdetailsStockAllocations` | Map/Fix | `plan_allocation` → `StockReservation` |
| `tblBOMaggregate` / `MEMORY` | Map/Fix | `plan_requirement` under `plan_run` |
| `tblplanmasterbomdetails` / `bomaggregate` | Drop as stores | Covered by `plan_requirement` |
| `tblplanmasterItemBundles*` | Defer/Drop MVP | — |
| `tblplanmasterlogtrail` | Replace | App audit |
| `tblplanresourcemaster/details*` | Defer | Chunk 9 resource entities |
| `tblResources` | Defer | `resource` |
| `tblResourcesQueue*` | Drop | Normalized slots later |
| `tblResourcesSchedule*` | Drop until finite capacity epic | — |
| `tblItemsTrendsInterval*` / projected plan* | Drop/Replace | `demand_profile` + shortage views (Chunk 10) |
| `tblBOMaggregatePickingLists` | Defer | After alloc |

---

## 6. Formula reference (MVP engine)

```
gross(fg)     = net(fg) / process_loss(fg)          # net(fg) from plan_line qty after line overrides
net(child)    = parent.gross * component.qty / yield(child)
gross(child)  = net(child) / process_loss(child)

available     = eligible_on_hand(plannable_locations, plan_date)
              - open_reservations
              + plan_supply(expected_at <= need_by)

# when consider_stock_in_plan:
net_req'      = max(net_req - available * process_loss, 0)
gross_req'    = max(gross_req - available, 0)

# full_batches_only:
batches       = ceil(gross_req' / standard_batch_kg)
# last batch: full if align_unitary_weight else remainder
```

Lot eligibility (shared by netting + allocation):

```
use_by - plan_date >= max(absolute_min_shelf_life_days, location_min_shelf_life, 0)
order by use_by ASC, production_date ASC, lot_id ASC
```

---

## 7. Dependencies & risks for later chunks

| Risk | Mitigation |
|---|---|
| Recipe trees incomplete for live SKUs | Accept on samosa 6-level seed first |
| No sales-order module yet | Manual lines; `source=order` reserved |
| `plan_supply` empty until PO | Manual entry; ATP still correct with on-hand only |
| Stock `reserve()` over-book race (known stock gap) | Flag before Planning commit goes live; fix in stock_ledger |
| Form code-behinds not exported | Requirements from dump + audit; UI invents modern UX |

---

## 8. Sign-off checklist

- [x] Legacy never-ran finding accepted — rebuild not port  
- [x] Requirements matrix complete (R-01 … R-84)  
- [x] D1–D9 locked as in §3  
- [x] MVP vs deferred locked as in §4  
- [x] No planning SPs/triggers in target design  
- [x] Chunk 2 (ERD) unblocked  

**Chunk 1 complete.** Next: approve **Chunk 2** to produce `PLANNING_ERD.md` (full column dictionary, indexes, state machine, FK map).
