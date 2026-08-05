---
name: Samosa Recipe Seed
overview: Import the 6-stage Potato & Pea Samosa BOM as a flushable demo seed (IDs 910xxx). Phase 1 creates raw materials and packaging first; later phases add locations, intermediates, and recipes.
todos:
  - id: chunk1-raw-materials
    content: "Chunk 1 FIRST: create all raw material + packaging product rows (910101+) with unit/lookups/yield; no recipes yet"
    status: completed
  - id: chunk2-locations
    content: "Chunk 2: seed 7 demo make/dispatch locations (910201+)"
    status: completed
  - id: chunk3-intermediates-fg
    content: "Chunk 3: create intermediate + FG products (910001–910007) with src/dest containers and recipe_codes"
    status: completed
  - id: chunk4-recipes
    content: "Chunk 4: seed 6 recipes bottom-up (version, components, activate, sync_has_recipe)"
    status: completed
  - id: chunk5-doc-verify
    content: Document command + ID map + verify steps in mock-data/recipe.md
    status: completed
isProject: false
---

# Samosa Recipe BOM Demo Seed

## Build order (locked)

**Chunk 1 = raw materials first** (your request). No intermediates, no recipes until RMs exist.

```mermaid
flowchart LR
  chunk1[Chunk1_RawMaterials] --> chunk2[Chunk2_Locations]
  chunk2 --> chunk3[Chunk3_IntermediatesFG]
  chunk3 --> chunk4[Chunk4_Recipes]
  chunk4 --> chunk5[Chunk5_Docs]
```

---

## Chunk 1 — Raw materials + packaging (do this first)

Create **leaf `product` rows only** (no `recipe` / `recipe_version` / `recipe_component`).

### Products from mock (all as demo IDs `910101+`)

**New RMs (must create):**
- POTATO DICED 10MM (FROZEN)
- POTATO MASH / FLAKE
- VEG SAMOSA SEASONING

**Reused names from FG 1230 (still create as demo copies for isolation):**
- SUGAR
- CUMIN SEED
- CHILLI POWDER
- ONION WHITE DICED 10MM (FROZEN)
- PEAS (FROZEN)
- LEMON JUICE
- Water - Step 1 (or Water)
- SAMOSA PASTRY - LARGE CUT

**Packaging (leaf products + yield):**
- Tray - Grab & Go - Square Tray → `product_yield.yield_factor = 0.99`
- Film - K peel 7G - 25mu - f240mm → `1.0`
- G&G Potato & Pea Samosa sleeve label (95x263mm) → `0.9999`
- Box - Grab and Go x 6 Tubs - 280x148x134 → `0.99`

### Prerequisites for each RM product row

Same as product create API / [`seed_demo_product`](product/management/commands/seed_demo_product.py):
- Lookups: `product_class`, `category`, `range`, `unit` (g and each)
- Two locations for `source_container_id` / `destination_container_id` (minimal demo stock locations if chunk 2 not done yet — use temporary demo locs `910200`/`910201` or create them as part of chunk 1 prerequisites)

### Delivery for chunk 1

- Management command flag or early section of `seed_demo_recipe_samosa`: e.g. `--raw-materials-only` so we can run/verify RMs alone.
- After run: `GET /product/` shows 9101xx RMs; packaging has `product_yield` rows.
- **Not in chunk 1:** Spice/Mixer/Belt/Fry/CVSAL/FG products, any recipe tables.

---

## Later chunks (after you approve each)

| Chunk | What |
|-------|------|
| **2** | Full location set: Spice Room → Dispatch (`910201–910207`) |
| **3** | Intermediate/FG products `910001–910007` with recipe_codes + src→dest |
| **4** | Six recipes bottom-up: version + components + activate + `has_recipe` |
| **5** | Doc in [`.cursor/mock-data/recipe.md`](.cursor/mock-data/recipe.md) |

## Mapping rules (unchanged)

- Yield columns → `product_yield` on leaf packaging products (chunk 1).
- Fraction in mixer stage → ignore as field; later use batch qty as `recipe_component.quantity`.
- Reserved demo IDs only; `--flush` removes 910xxx graph only.
- BOM explosion = Phase 2 (out of scope).

## Files

- [`recipe/management/commands/seed_demo_recipe_samosa.py`](recipe/management/commands/seed_demo_recipe_samosa.py) — implement chunk 1 first (`--raw-materials-only`).
- Reuse patterns from [`product/management/commands/seed_demo_product.py`](product/management/commands/seed_demo_product.py).

**Next:** say **approve chunk 1** to create raw materials only.
