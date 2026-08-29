# Chunk 6 brief — Warehouse mobile Goods In (3 tabs)

**For:** warehouse-mobile-app agent  
**Backend repo:** `svc-locations-django` (APIs already shipped)  
**App:** `/home/gazebo/projects/gazebo-cloud/warehouse-mobile-app`  
**Do not change** With-PO receive / delivery QC behaviour.

---

## Goal

Add a mode switch on Goods In:

| Tab | Behaviour | Backend |
|---|---|---|
| **A. With PO** | Existing flow only | Unchanged purchasing deliveries |
| **B. Without PO** | Product → QC → shape/qty/labels → print/scan/post | `/purchasing/adhoc-goods-in/…` |
| **C. Stock Adjustment** | Product → shape/qty → manual trace+use_by → labels → print/scan/post | `POST /purchasing/stock-adjustment/` |

UX: short screens, one job each, no fluff. Server owns multipliers, QC ranges, label split, queue/post.

---

## Hard rules

1. **Tab A:** do not regress. Keep period / supplier / PO / receive stack as today.
2. **Tab B:** never call `POST /stock/receipt/`. Always adhoc session so QC cannot be skipped.
3. **Tab C:** never invent a PO; never use adhoc QC session.
4. Never send `po_number` or `source_document_type: "po"` on free stock paths.
5. After receive: **reuse** `GoodsInCompleteScreen` + `GoodsInVerifyScreen` (print → scan → post). Extend nav types so `poId` / `deliveryId` are optional for B/C.
6. Labels: Tab A remains read-only from PO; B/C need **editable** pallet/box + count (default 1).
7. Auth: same warehouse JWT; receive/adjustment need `goods_in` write grant. Use `location_id` from `useAppLocation()`.

---

## Existing app map (reuse)

| Path | Role |
|---|---|
| `src/screens/goodsIn/GoodsInScreen.tsx` | Entry — add 3 tabs here |
| `GoodsInReceiveScreen` + `HeaderQcForm` / line QC | Reuse patterns for Tab B QC |
| `GoodsInCompleteScreen` / `GoodsInVerifyScreen` | Shared print + scan + post |
| `src/services/goodsIn.ts` | Old unused `/stock/receipt/` helpers — **do not** use for Tab B; optional ignore for C |
| `src/services/purchasing.ts` | Keep for Tab A |
| `src/navigation/types.ts` | Extend `GoodsInComplete` / `GoodsInVerify` params |

Product search: `GET /product/?q=` (or existing search client if any).  
Packs: `GET /product/{id}/suppliers/` (already in `goodsInService.listProductSuppliers`).

---

## Tab A — With PO (no API change)

Keep current:

`GoodsIn` → period → orders → `GoodsInReceive` (header QC → line QC → receive) → Complete → Verify.

---

## Tab B — Without PO

### Screen order

1. Search/select product  
2. `POST` start session  
3. Header QC (delivery date → auto Julian trace; template answers)  
4. Line QC (use_by, product temp, spec_check, …)  
5. Manual **item PO#** (free text)  
6. Shape dropdown (supplier packs + **Other**) → qty → show calculated stock (client preview; server authoritative)  
7. Label: pallet \| box + count  
8. Receive → navigate Complete → Verify  

Statuses: `open` → `rejected` \| `qc_complete` → `received`.

### APIs

#### Start

`POST /purchasing/adhoc-goods-in/`

```json
{
  "product_id": 545,
  "location_id": 8,
  "created_by_user_id": 12
}
```

**201** — `data.session_id`, `header.items[]`, `line.template`, `suggested_delivery_date`, `suggested_trace_number`.

#### Get (resume)

`GET /purchasing/adhoc-goods-in/{session_id}/`

#### Header QC

`POST /purchasing/adhoc-goods-in/{session_id}/qc/header/`

```json
{
  "checked_by_user_id": 12,
  "delivery_date": "2026-08-29",
  "answers": {
    "vehicle_clean_fb_pest_odour": { "value": true },
    "primary_outer_packaging_damaged": { "value": false },
    "vehicle_temperature": { "value": "4" },
    "reject_delivery": { "value": false }
  }
}
```

Use **only** codes from `data.header.items` (food vs other/packaging differ).  
Trace: display Julian from delivery date (read-only) unless override + reason.  
If `status=rejected` → stop; start new session.

#### Line QC

`POST /purchasing/adhoc-goods-in/{session_id}/qc/line/`

```json
{
  "answers": {
    "use_by": { "value": "2026-12-01" },
    "product_temperature": { "value": "3" },
    "spec_check": { "value": true }
  }
}
```

Need `status=qc_complete` and `line.line_check_ok=true` before receive.

#### Receive

`POST /purchasing/adhoc-goods-in/{session_id}/receive/`  
Requires warehouse `goods_in` write.

**Pack:**

```json
{
  "idempotency_key": "adhoc-545-…",
  "item_po_ref": "SUP-12345",
  "product_supplier_id": 91,
  "quantity": "2",
  "label_format": "pallet",
  "label_count": 1
}
```

**Free kg:**

```json
{
  "idempotency_key": "…",
  "item_po_ref": "SUP-12345",
  "quantity": "5",
  "supplier_id": 84,
  "label_format": "box",
  "label_count": 1
}
```

**Other shape:**

```json
{
  "idempotency_key": "…",
  "quantity": "1",
  "shape_other": {
    "outer_qty": "1",
    "outer_unit_id": 92,
    "inner_qty": "5",
    "inner_unit_id": 91
  },
  "label_format": "pallet",
  "label_count": 1,
  "supplier_id": 84
}
```

Rules: `pallet` ⇒ `label_count=1`; `box` ⇒ count ≥ 1. Default queues stock.

**201** — `receive_results[]` with `stock_entry_id`, `entry_code`, `goods_in_label`, `posting_status` (`queued`). Map into Complete/Verify.

---

## Tab C — Stock Adjustment

### Screen order

1. Search/select product  
2. Shape (packs + Other) → qty → show calculated stock  
3. Trace **manual** + use-by (required)  
4. Label pallet/box + count (default 1)  
5. `POST /purchasing/stock-adjustment/` → Complete → Verify  

No header/line QC. No item PO#.

### API

`POST /purchasing/stock-adjustment/` (`goods_in` write)

```json
{
  "idempotency_key": "adj-…",
  "product_id": 545,
  "location_id": 8,
  "product_supplier_id": 91,
  "quantity": "1",
  "trace_number": "MANUAL-TRACE",
  "use_by": "2026-12-01",
  "label_format": "pallet",
  "label_count": 1
}
```

Same free qty / `shape_other` options as Tab B receive.  
Response: `receive_results[]`, `stock_qty`, `source_document_type` is `stock_adjustment` on entries.

---

## Shared after receive (B and C)

| Step | API |
|---|---|
| Print | `POST /stock/entries/{id}/labels/print/` |
| Verify scan | `POST /stock/entries/{id}/labels/verify/` |
| Post | `POST /stock/entries/{id}/post/` (or verify with post flag) |

Suggested nav extension:

```ts
GoodsInCompleteParams = {
  mode: 'po' | 'adhoc' | 'adjustment';
  poId?: number;
  deliveryId?: number;
  sessionId?: number;
  poNumber?: string;      // display: item_po_ref or "Adjustment"
  // …existing lines[] with goodsInLabel, entryId, postingStatus
}
```

---

## Suggested new files (mobile)

- `src/services/adhocGoodsIn.ts` — start, get, headerQc, lineQc, receive  
- `src/services/stockAdjustment.ts` — post adjustment  
- Screens under `src/screens/goodsIn/`: e.g. `GoodsInModeTabs`, `AdhocProductSearch`, `AdhocReceiveWizard`, `StockAdjustmentWizard`  
- Editable `LabelFormatPicker` (do not reuse read-only `LabelFormatField` as-is)  
- Types for session form / receive_results  

---

## QA checklist

- [ ] Tab A: existing PO goods-in still works end-to-end  
- [ ] Tab B: food product — temps + use_by → pack receive → print → scan → posted  
- [ ] Tab B: reject delivery → cannot receive  
- [ ] Tab B: free 5 kg path  
- [ ] Tab C: no QC screens; manual trace + use_by required  
- [ ] Tab C: pack + free qty → queue → scan → post  
- [ ] No PO `qty_received` change on B or C  

---

## Out of scope for this mobile chunk

- Postman  
- Goods Out with/without plan (Phase 2)  
- Backend changes (APIs already live: migrate `0008` + `0009` on Django if needed)  

---

## Backend reference (Django)

- Models: `AdhocGoodsInSession` / `AdhocGoodsInLine` (`po_adhoc_goods_in_*`)  
- Services: `purchasing/services/adhoc_goods_in.py`, `stock_adjustment.py`  
- Routes: `purchasing/urls.py` (`adhoc-goods-in/…`, `stock-adjustment/`)  
- Plan: `svc-locations-django/.cursor/plans/ad-hoc_goods_in_out_b695bd45.plan.md`
