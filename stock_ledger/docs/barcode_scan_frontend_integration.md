# Barcode scan + FIFO batch picking — frontend integration

One reusable label per product. The barcode carries only our product id, so it
never goes stale and a damaged label can be typed in by hand. Which batch is
being moved or used is chosen on screen from a FIFO list, not read off the label.

Staff cannot sticker 50 frozen bags on a pallet or 350 trays, so the pallet or
the tray batch gets one label and the operator picks the batch at point of use.

## Base URL

All routes below are under the stock service prefix `/stock/`.

---

## 1. Print a product label

```
GET /stock/products/<product_id>/label/
GET /stock/products/<product_id>/label/?lot_id=<lot_id>
```

Use without `lot_id` for a permanent shelf/bin label. Use with `lot_id` at
goods-in or after production so the printed text shows that batch's use by and
trace number.

Response `data`:


| Field                            | Meaning                                                           |
| -------------------------------- | ----------------------------------------------------------------- |
| `bcid`                           | Always `datamatrix`. Pass straight to bwip-js.                    |
| `payload_string`                 | What to encode, e.g. `P7`. Nothing else is in the barcode.        |
| `product_code`                   | Same value, for printing under the symbol.                        |
| `human_readable.product_id`      | Print this as text. It is the fallback when the label is damaged. |
| `human_readable.product_name`    |                                                                   |
| `human_readable.recipe_code`     |                                                                   |
| `human_readable.unit_name`       | e.g. `Kg`                                                         |
| `human_readable.label_mode`      | `product`, `batch` or `per_unit`                                  |
| `human_readable.use_by`          | `null` unless `lot_id` was passed                                 |
| `human_readable.trace_number`    | `null` unless `lot_id` was passed                                 |
| `human_readable.production_date` | `null` unless `lot_id` was passed                                 |


Render with bwip-js:

```js
const { data } = await api.get(`/stock/products/${productId}/label/`, {
  params: lotId ? { lot_id: lotId } : {},
});

bwipjs.toCanvas(canvasEl, {
  bcid: data.bcid,            // 'datamatrix'
  text: data.payload_string,  // 'P7'
  scale: 3,
  height: 12,
  includetext: false,
});
```

Print `product_id`, `product_name`, `use_by` and `trace_number` as text beside
the symbol. Do not print the barcode value only — the text is what makes a
damaged label recoverable.

Status codes: `200`, `404` (unknown product, or `lot_id` belongs to a different
product), `400` (`lot_id` not an integer).

---



## 2. Scan or type a code

```
GET /stock/scan/?code=<scanned>&location_id=<department_id>
```

`code` accepts, in this order:

1. `P7` — what the label encodes
2. `7` — bare product id, the hand-typed fallback
3. a legacy per-unit serial, or a full GS1 string containing `(21)`

`location_id` is optional but should almost always be sent: it limits batches to
the department the operator is standing in.

Response `data`:


| Field                                                                                                                                                                  | Meaning                                                 |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| `match_type`                                                                                                                                                           | `product` or `unit_serial`                              |
| `selected_lot_id`                                                                                                                                                      | Non-null only for `unit_serial`. Pre-select this batch. |
| `scanned_code`, `location_id`                                                                                                                                          | Echoed back                                             |
| `product.product_id`, `product.product_code`, `product.name`, `product.recipe_code`, `product.unit_id`, `product.unit_name`, `product.label_mode`, `product.is_active` | Header of the screen                                    |
| `total_quantity`                                                                                                                                                       | On-hand across the returned batches                     |
| `batch_count`                                                                                                                                                          | Length of `batches`                                     |
| `batches[]`                                                                                                                                                            | FIFO-ordered batch rows, see below                      |


Each row in `batches`:


| Field                                                                                                                                             | Use on screen                                             |
| ------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `fifo_rank`                                                                                                                                       | `0` is the recommended batch. Highlight it.               |
| `lot_id`                                                                                                                                          | Send this to the write endpoints                          |
| `trace_number`                                                                                                                                    | Show it, staff read it off paperwork                      |
| `use_by`, `days_left`                                                                                                                             | `days_left` is negative when expired, `null` when undated |
| `quantity`, `unit_name`                                                                                                                           | Available in this batch at this location                  |
| `supplier_id`, `supplier_name`, `po_number`, `supplier_lot_code`                                                                                  | Goods-in provenance                                       |
| `origin`                                                                                                                                          | `purchase`, `production` or `opening`                     |
| `location_id`, `location_name`                                                                                                                    | Useful when `location_id` was not filtered                |
| `product_id`, `product_name`, `recipe_code`, `product_class_name`, `range_name`, `yield_factor`, `production_date`, `last_entry_id`, `updated_at` | Extra detail                                              |


Ordering is oldest `use_by` first, then oldest `production_date`, with undated
batches last. It is a recommendation, not a rule — the operator may pick any row.

Status codes: `200`, `400` (empty `code`, non-integer `location_id`), `404`
(code resolves to nothing).

### Suggested screen

1. Operator scans. Show `product.name` and `total_quantity` as the header.
2. Render `batches` as a dropdown or list, `fifo_rank === 0` preselected and
  badged "use first".
3. Operator types a quantity and confirms.
4. Call the existing write endpoint with the chosen `lot_id`.

Empty `batches` with a `200` means the product is known but has no stock at that
location. Say so rather than showing an empty dropdown.

---



## 3. Writes — unchanged endpoints

There is no scan-specific write endpoint. After the operator picks a batch, use
what already exists, passing `lot_id` from the chosen row.

Move stock to another department:

```
POST /stock/transfer/
{
  "idempotency_key": "<uuid>",
  "lot_id": 42,
  "from_location_id": 8,
  "to_location_id": 4,
  "quantity": "20",
  "unit_id": 1
}
```

Use stock against a production output:

```
POST /stock/production/<output_entry_id>/consume/
{
  "idempotency_key": "<uuid>",
  "lot_id": 42,
  "location_id": 4,
  "quantity": "5"
}
```

Always send a fresh `idempotency_key` per user action and reuse the same one on
retry, so a double tap on a flaky connection cannot post twice.

Partial use is normal: consume 5 kg now and 8 kg later from the same batch. The
batch stays available until its quantity reaches zero.

---



## 4. Stock list ordering

```
GET /stock/balances/?product_id=7&order=fifo
```

`order=fifo` applies the same ordering as the scan screen. Omit it (or send
`order=default`) for the previous ordering. Any other value returns `400`.

Use `order=fifo` on stock screens so a batch never appears first in one place
and third in another.

---



## 5. `label_mode` — what to print for a product

`label_mode` comes back on `GET /product/<id>/` and on the scan response, and is
editable via `PATCH /product/<id>/` with `{"label_mode": "batch"}`.


| Mode                | Meaning                                    | What the UI offers                                                                                |
| ------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `product` (default) | One reusable label per product             | "Print product label" only. Batch label printing is rejected by the API.                          |
| `batch`             | One label for a whole pallet or tray batch | "Print batch label": exactly one label, quantity equal to the full received or produced quantity. |
| `per_unit`          | One label per physical unit                | The existing labels-per-unit flow with serials.                                                   |


For `batch` mode, print via the receipt call or the print endpoint with
`unit_count: 1` and `quantity_per_unit` equal to the whole entry quantity.
Anything else returns `400` with a message naming the product.

Frozen chicken pallets and high-risk trays are `product`. Low-risk items that
need a label per batch are `batch`. Only genuinely bag-by-bag items are
`per_unit`.

---



## Rules worth repeating

- Printing a label never changes stock. Only receipt, transfer, consume,
disposal and adjustment move quantities.
- The barcode never contains a batch, an expiry or a supplier code. If a screen
needs those, read them from the scan response.
- A same-site transfer is one call. The receiving department's stock rises
immediately and does not scan anything in.

