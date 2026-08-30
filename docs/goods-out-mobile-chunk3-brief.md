# Chunk 3 brief — Warehouse mobile Goods Out (With plan / Without plan)

**For:** warehouse-mobile-app agent  
**Backend:** `svc-locations-django` Chunk 2 **shipped**  
**App:** `/home/gazebo/projects/gazebo-cloud/warehouse-mobile-app`  
**Do not change** With-plan FIFO / transfer / print / verify behaviour.

---

## Goal

Top of Goods Out: **2 tabs**

| Tab | Behaviour | Backend |
|---|---|---|
| **A. With plan** | Existing only | Portal + transfer + `requirement_ids` |
| **B. Without plan** | Product list + scan on top → auto popup → FIFO/trace → auto dest → Complete/Verify | Same transfer APIs; **omit** `requirement_ids` + **omit** `to_location_id` |

UX: less effort — operator mainly **scans and respects trace/FIFO**. Dest = `product.destination_container` (from scan payload).

---

## Hard rules

1. **Tab A:** do not regress plan day / category / requirement / Verify scan flow.  
2. **Tab B:** never invent a plan requirement; never send `requirement_ids`.  
3. Still **`POST /stock/transfer/`** with `queue_stock: true` — never `/stock/issue/`.  
4. Dest: use scan `to_location_id` / omit on transfer (server fills from product). **No dest picker.**  
5. If `dest_ok === false` → block with `dest_message`; fix product master.  
6. After queue: **reuse** `GoodsOutCompleteScreen` + `GoodsOutPostScreen` / Verify path. Make `requirementId` / `planDay` / `categoryId` **optional** on nav params for Tab B.  
7. Auth: warehouse JWT; `goods_out` write. `from_location_id` = `useAppLocation()`.

---

## Existing app map (reuse)

| Path | Role |
|---|---|
| `GoodsOutScreen.tsx` | Entry — add **With plan \| Without plan** tabs |
| Plan day → Requirements → `GoodsOutVerify` | Tab A only (unchanged) |
| `BarcodeScannerPanel`, `FifoBanner`, `ThisBagCard`, `BagDetailsSheet`, `ReasonSheet` | Reuse on Tab B popup |
| `GoodsOutCompleteScreen` / `GoodsOutPostScreen` | Shared print → post for A/B |
| `stockService.scanGoodsOut` / `stockService.transfer` | Extend types for dest fields; transfer already posts body |
| `goodsOutService` portal helpers | Tab A only |

---

## Tab A — With plan (no behaviour change)

Keep:

`GoodsOut` → plan day → category → requirements → `GoodsOutVerify` (scan → FIFO → transfer with `requirement_ids`) → Complete → Post.

Wrap existing content inside **With plan** tab.

---

## Tab B — Without plan (new)

### Screen (one main screen)

1. **Product list** — `GET /stock/warehouse/remaining/?location_id=` (same warehouse remaining). Search/filter client-side.  
2. **Scan bar on top** — camera / gun → `GET /stock/scan/goods-out/?code=&location_id=`  
3. On 200 → **auto sheet/popup**: product name, trace, qty (default full bag), `fifo_ok` / recommended trace, dest name (read-only).  
4. User confirms qty (optional partial) → if `!fifo_ok` → same override reason sheet as Tab A → `POST /stock/transfer/`.  
5. Navigate Complete → Post (same as A).

Tapping a list row may set `expected_product_id` for the next scan (optional lock); scan still drives the popup.

### APIs

| Step | Call |
|---|---|
| List | `GET /stock/warehouse/remaining/?location_id={{wh}}` |
| Scan | `GET /stock/scan/goods-out/?code=&location_id=` (+ optional `expected_product_id`) |
| Queue | `POST /stock/transfer/` — see body below |
| Print / post | same as Tab A (`labels/print`, verify, `entries/:id/post/`) |

**Transfer body (Tab B):**

```json
{
  "idempotency_key": "…",
  "lot_id": 123,
  "from_location_id": 8,
  "quantity": "25",
  "queue_stock": true,
  "source_entry_id": 725
}
```

- **Omit** `to_location_id` (server uses product dest).  
- **Omit** `requirement_ids`.  
- If not oldest: add `fifo_override_reason`.

**Scan fields to use:** `product`, `lot_id`, `trace_number`, `quantity`, `fifo_ok`, `recommended_trace`, `to_location_id`, `to_location_name`, `dest_ok`, `dest_message`, `entry_id` / `entry_code`.

---

## Nav / types

Extend (make plan fields optional):

```ts
GoodsOutComplete: {
  outId: number;
  entryCode: string;
  goodsOutLabel: EntryLabelPayload;
  productName: string;
  quantity: string;
  // Tab A only:
  requirementId?: string;
  planDay?: GoodsOutPlanDay;
  categoryId?: GoodsOutCategoryId;
  mode?: 'with_plan' | 'without_plan';
};
```

Same idea for `GoodsOutPost` / back navigation — Tab B returns to Without-plan list, not requirements.

New optional screen or inline: `GoodsOutWithoutPlan` content can live **inside** `GoodsOutScreen` tab B (preferred — one entry).

---

## Implementation order

1. Tabs on `GoodsOutScreen` (With plan = current UI; Without plan = stub).  
2. Wire remaining list + scan on top.  
3. Reuse bag sheet + FIFO banner + override reason → transfer **without** req/dest.  
4. Loosen Complete/Post params; back stack for Tab B.  
5. Smoke: scan oldest → queue → print → post; scan newer → override required; product without dest → block.

---

## Out of scope

- Dest location picker  
- Multi-bag cart in one confirm  
- Changing Tab A portal or FIFO rules  
- New Django endpoints  

---

## Backend already ready (Chunk 2)

- Scan returns auto dest + `dest_ok`  
- Transfer omits `requirement_ids` + omits `to_location_id` → `goods_out_adhoc`  
- Postman: `svc-locations-django/postman/Gazebo-Stock-Goods-Out-Without-Plan.postman_collection.json`

---

## Approve to implement

Reply **approve** in the mobile-app chat (or here) to start coding Chunk 3.
