---
name: Category table redesign
overview: Expand product_category from tblcategories in small safe chunks. Chunk 1 is schema-only (model + migration) and does not change API behaviour.
todos:
  - id: chunk1-schema
    content: "Chunk 1: Expand Category model + migration only (no API change)"
    status: completed
  - id: chunk2-get
    content: "Chunk 2: Expand GET /product/category/ response with all fields"
    status: completed
  - id: chunk3-filter
    content: "Chunk 3: Nested tree response (parent.children) + optional ?parent_id= filter"
    status: completed
  - id: chunk4-postman
    content: "Chunk 4: Update Postman docs for expanded category GET"
    status: completed
isProject: false
---

# Expand `product_category` from `tblcategories` (chunked)

## Will this break the workflow?

**No — if we ship chunk-by-chunk, starting with schema-only.**

| Consumer | What it uses today | Chunk 1 impact |
|---|---|---|
| `Product.category` FK | `category_id` only | Unchanged |
| Product create/update | `Category.objects.get(pk=...)` | Still works |
| `GET /product/category/` | `{id, name}` via `_rows()` | **Unchanged in Chunk 1** |
| Seeds / tests / prove command | `Category(id=..., name=...)` | Still works (new cols have defaults/null) |
| Stock / recipe | Only needs a valid `category_id` | Unchanged |

Risks avoided by Chunk 1:
- No response-shape change → frontends keep working
- New columns nullable or defaulted → existing rows migrate cleanly
- No rename of `name` / `id` → no FK rewrite
- Table name stays `product_category`

Later chunks (GET expand) are **additive JSON** (`id`/`name` still present). Only risk then is a strict client that rejects unknown keys — uncommon; we ship that only after you approve Chunk 2.

## Field mapping (reference)

| Legacy | New field | Type |
|---|---|---|
| `id` | `id` | `IntegerField(pk)` keep |
| `category` | `name` | widen to 256 |
| `parentid` | `parent` | self-FK null |
| `catdefault` | `is_default` | bool default False |
| `container` | `is_container` | bool null |
| `purchaseunit` | `purchase_unit` | FK Unit null |
| `multiplier` | `multiplier` | Decimal(10,2) null |
| `categorypath` | `path` | varchar 256 null |
| `categorypathnodes` | `path_nodes` | varchar 128 null |
| `codegenerator` | `code_generator` | varchar 256 null |
| `codegeneratorpath` | `code_generator_path` | varchar 256 null |
| `lastincrementautocode` | `last_increment_auto_code` | int default 0 |
| `itemflag` | `item_flag` | SmallInt default -1 |
| `rangeflag` | `is_range` | bool |
| `resourceflag` | `is_resource` | bool |
| `containerflag` | `is_container_flag` | bool |
| `othersflag` | `is_other` | bool |
| `locked` | `is_locked` | bool |
| `lockedAssigned` | `is_locked_assigned` | bool |
| `lockedPath` | `is_locked_path` | bool |
| `remarks` | `remarks` | text null |

## Chunk list

| Chunk | What | Breaks workflow? | Approve before build |
|---|---|---|---|
| **1** | Model + migration only | **No** | waiting |
| **2** | Expand GET list payload | Additive only | later |
| **3** | `?parent_id=` filter | No (opt-in) | later |
| **4** | Postman docs | No | later |

Out of scope for all chunks: CRUD write APIs, path auto-rebuild, production ETL, nested tree JSON.

---

## Chunk 1 (ready for your approve) — schema only

**Goal:** Make `product_category` able to hold full legacy data. Do not change any HTTP contract.

**Files:**
1. [`product/models.py`](product/models.py) — replace stub `Category` (lines 20–31) with full fields above
2. New migration `product/migrations/0008_expand_category.py` (next number after `0007`)

**What migration does:**
- `AlterField` `name` max_length 64 → 256
- `AddField` for every new column / FK with null or defaults matching legacy
- Existing category rows + `Product.category_id` keep working

**What Chunk 1 does NOT do:**
- Touch `lookups_views.py` → GET still returns `{id, name}` only
- Touch product create/update
- Import production data
- Change Postman

**Verify after Chunk 1:**
- `python manage.py migrate`
- `GET /product/category/` still `{id, name}`
- Product create with `category_id` still 201
- Existing seeds/tests still pass

**Stop after Chunk 1** until you approve Chunk 2.
