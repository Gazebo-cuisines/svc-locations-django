# Complaint / recall — product + use-by

When a customer complaint only gives **product id** and **use-by** from the
pack (no lot id / unit serial):

1. Call `GET /stock/recall/?product_id=<id>&use_by=YYYY-MM-DD`.
2. If `lot_count > 1`, show every matching batch (different traces) so staff
   can pick the right one or investigate all.
3. Per lot, use `genealogy.backward` (what went into this pack) and
   `genealogy.forward` (where this lot went).
4. For a product-wide list of batches with genealogy, use
   `GET /stock/products/<product_id>/genealogy/` (`with_trees=0` for a light
   index).

Trees are empty when production never wrote `stock_genealogy` edges (consume
not recorded). Recipe BOM (not stock lots) remains
`GET /recipe/product/<id>/tree/`.

Each lot also returns `genealogy.graph` (`nodes` + `edges`) for
[React Flow](https://reactflow.dev/) — see
[react_flow_genealogy_ui.md](react_flow_genealogy_ui.md).
