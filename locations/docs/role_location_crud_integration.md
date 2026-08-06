# Frontend API integration — suppliers / couriers / customers / storage

Base URL: `{locations_base_url}`  
Prefix for these routes: `/container/`

## Auth

Every `/container/...` call:

```
Authorization: Bearer {CONTAINER_STATIC_TOKEN}
```

or

```
X-API-Token: {CONTAINER_STATIC_TOKEN}
```

Local default is often `dev-static-token`.

## Response envelope

```ts
type ApiOk<T> = { status: 'success'; message: string; data: T }
type ApiErr = { status: 'error'; message: string; data?: unknown }
```

---

## Important model rule

There is **no** separate supplier table.

| UI | Role to send on create | List endpoint |
|----|------------------------|---------------|
| Supplier | `"supplier"` | `GET /container/suppliers/` |
| Courier | `"courier"` | `GET /container/couriers/` |
| Customer | `"customer"` | `GET /container/customers/` |
| Storage | `"storage"` | `GET /container/storage/` |

**Writes** always go to `/container/locations/`  
**Reads** for typed screens use the list/detail URLs above.

---

## 1. List suppliers (table / dropdown)

```http
GET /container/suppliers/
```

Same pattern:

- `GET /container/couriers/`
- `GET /container/customers/`
- `GET /container/storage/`

**Row shape:**

```ts
type LocationListRow = {
  container_id: number
  id: number              // use this as supplier_id
  name: string
  external_code: string | null
  visible: boolean
  static: boolean
  locked: boolean
  roles: string[]
  features: string[]
}
```

**FE:**

```ts
const { data } = await api.get('/container/suppliers/')
// dropdown options: data.map(s => ({ value: s.id, label: s.name }))
```

---

## 2. Get one supplier

```http
GET /container/suppliers/{id}/
```

404 if the id exists but is **not** a supplier (e.g. Unit 2).

Detail also includes: `remarks`, `po_remarks`, `stock_profile`, `addresses`, `contacts`, timestamps, parents.

---

## 3. Create supplier

```http
POST /container/locations/
Content-Type: application/json
```

**Required:** `name`, `roles`  
**Do not send `id`** — server auto-assigns. Read `data.id` from the response.

```json
{
  "name": "NEW SUPPLIER LTD",
  "external_code": "SUP-NEW",
  "visible": true,
  "roles": ["supplier"]
}
```

Courier / customer / storage — same body, only change:

```json
"roles": ["courier"]
```

```json
"roles": ["customer"]
```

```json
"roles": ["storage"]
```

**Success:** `201`

```json
{
  "status": "success",
  "message": "Location created successfully.",
  "data": {
    "id": 229,
    "name": "NEW SUPPLIER LTD",
    "roles": ["supplier"],
    "...": "..."
  }
}
```

**Optional fields:** `external_code`, `visible`, `static`, `locked`, `remarks`, `po_remarks`, `features`, `stock_profile`, `zone_parent_id`, `subordinate_parent_id`  
**Optional `id`:** only if you must force a legacy PK.

---

## 4. Update supplier

```http
PATCH /container/locations/{id}/
```

Partial body:

```json
{
  "name": "NEW SUPPLIER LTD (UK)",
  "remarks": "Preferred for sugar",
  "visible": true
}
```

- Omit `roles` to leave roles unchanged.  
- If you send `roles`, it **replaces** all roles — keep `["supplier"]` when editing a supplier.  
- Soft-hide: `{ "visible": false }` (prefer this over delete when used in stock).

**Success:** `200` + detail.

---

## 5. Delete supplier

```http
DELETE /container/locations/{id}/
```

Prefer soft-hide if the supplier is used on product mappings or goods-in.

---

## 6. Screen → API map

| UI action | Call |
|-----------|------|
| Suppliers page load | `GET /container/suppliers/` |
| Create → Save | `POST /container/locations/` + `roles: ["supplier"]` |
| Edit → Save | `PATCH /container/locations/{id}/` |
| Hide | `PATCH` `{ "visible": false }` |
| Delete | `DELETE /container/locations/{id}/` |
| Goods-in supplier dropdown | `GET /container/suppliers/` → `supplier_id = id` |
| Product pack mapping | `POST /product/{productId}/suppliers/` with `supplier_id` |

---

## 7. Goods-in (warehouse) — must send supplier

Flow:

1. `POST /stock/lots/` — create lot  
2. `POST /stock/receipt/` — book qty **with supplier**

### Create lot

```http
POST /stock/lots/
```

```json
{
  "product_id": 910125,
  "origin": "purchase",
  "trace_number": "26220",
  "use_by": "2027-02-10"
}
```

### Receipt (required for purchase)

```http
POST /stock/receipt/
```

```json
{
  "idempotency_key": "wh-in-unique-key-001",
  "lot_id": 103,
  "location_id": 8,
  "quantity": "50",
  "unit_id": 6,
  "supplier_id": 11,
  "po_number": "PO-12345"
}
```

| Field | Meaning |
|-------|---------|
| `location_id` | Warehouse (e.g. Unit 2 = `8`) |
| `supplier_id` | From suppliers list (`id`) — **required** on purchase |
| `po_number` | Optional but recommended; shows on balances |
| `product_supplier_id` | Alternative to `supplier_id` if user picks a pack mapping row |

**Do not** put supplier only in `remarks`.

Without supplier → `400`:  
`supplier_id or product_supplier_id is required for purchase goods-in.`

Receipt response includes:

```json
{
  "supplier_id": 11,
  "supplier_name": "GAZEBO",
  "po_number": "PO-12345",
  "counterparty_location_id": 11
}
```

### Balances

```http
GET /stock/balances/?location_id=8
```

Each row may include:

```ts
{
  supplier_id: number | null
  supplier_name: string | null
  po_number: string | null
  // ...product, lot, qty fields
}
```

Null = older receipt without supplier/PO.

---

## 8. Product ↔ supplier pack mapping (shape format)

List mappings for a product:

```http
GET /product/{productId}/suppliers/
```

Create mapping (after supplier location exists):

```http
POST /product/{productId}/suppliers/
```

```json
{
  "supplier_id": 229,
  "supplier_code": "SUG01",
  "supplier_product_name": "Sugar 10kg",
  "outer_qty": "1",
  "outer_unit_id": 6,
  "inner_qty": "10",
  "inner_unit_id": 2,
  "is_default": true
}
```

Then goods-in can send `"product_supplier_id": <mapping.id>` instead of raw `supplier_id`.

---

## 9. Do not

- `POST /container/suppliers/` — **does not exist** (GET only)  
- Require user to type location `id` on create — it’s auto  
- Use a department/storage id as `supplier_id` unless it has role `supplier`  
- Put supplier name only in receipt `remarks`

---

## 10. Minimal FE client sketch

```ts
const headers = {
  Authorization: `Bearer ${token}`,
  'Content-Type': 'application/json',
}

// list
await fetch(`${base}/container/suppliers/`, { headers })

// create
const res = await fetch(`${base}/container/locations/`, {
  method: 'POST',
  headers,
  body: JSON.stringify({
    name: form.name,
    external_code: form.code || undefined,
    roles: ['supplier'],
    visible: true,
  }),
})
const created = await res.json()
const supplierId = created.data.id

// goods-in receipt
await fetch(`${base}/stock/receipt/`, {
  method: 'POST',
  headers,
  body: JSON.stringify({
    idempotency_key: crypto.randomUUID(),
    lot_id: lotId,
    location_id: warehouseId, // e.g. 8
    quantity: qty,
    unit_id: unitId,
    supplier_id: supplierId,
    po_number: poNumber || undefined,
  }),
})
```
