# React Flow — stock genealogy chain UI

Use [React Flow](https://reactflow.dev/) (`@xyflow/react`) to show the whole
ingredient → finished-goods → downstream chain.

## API (already shaped for you)

`GET /stock/recall/?product_id=&use_by=`

Each lot includes:

```json
"genealogy": {
  "backward": [ /* flat list — keep for tables */ ],
  "forward": [ /* flat list */ ],
  "graph": {
    "nodes": [
      {
        "id": "lot-12",
        "type": "lot",
        "position": { "x": 0, "y": 0 },
        "data": {
          "lot_id": 12,
          "product_id": 545,
          "product_name": "Samosa FG",
          "trace_number": "26218",
          "supplier_lot_code": null,
          "origin": "production",
          "use_by": "2026-09-15",
          "production_date": "2026-08-10",
          "role": "focus",
          "depth": 0
        }
      }
    ],
    "edges": [
      {
        "id": "e-101-202",
        "source": "lot-7",
        "target": "lot-12",
        "label": "5.000000",
        "data": { "direction": "backward", "depth": 1, "quantity_base": "5.000000" }
      }
    ]
  }
}
```

| `type` | `data.role` | Meaning |
|--------|-------------|---------|
| `supplier` | `supplier` | Goods-in counterparty (left) |
| `lot` | `focus` / `upstream` / `downstream` | Stock batch |
| `location` | `location` | Warehouse / kitchen / Dispatch (right) |

Edge `data.kind`: `receipt` | `genealogy` | `transfer` | `balance` | `stocked_at`

One canvas shows the full cycle:

```
[Supplier] --receipt--> [Ingredient lot] --genealogy--> [FG focus] --balance--> [Dispatch]
                              |                              |
                         transfers between locations
```

Register three node components in React Flow (`supplier`, `lot`, `location`).


## Frontend install

```bash
npm install @xyflow/react
```

## Minimal screen

```tsx
import { useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

function LotNode({ data }) {
  const border =
    data.role === 'focus' ? '#0f766e' :
    data.role === 'upstream' ? '#2563eb' : '#c2410c';
  return (
    <div style={{
      padding: 10, minWidth: 160, borderRadius: 8,
      border: `2px solid ${border}`, background: '#fff', fontSize: 12,
    }}>
      <div style={{ fontWeight: 700 }}>{data.product_name}</div>
      <div>Trace {data.trace_number}</div>
      {data.use_by && <div>Use by {data.use_by}</div>}
      {data.supplier_lot_code && <div>Supplier {data.supplier_lot_code}</div>}
      <div style={{ opacity: 0.6 }}>{data.role} · depth {data.depth}</div>
    </div>
  );
}

const nodeTypes = { lot: LotNode };

export function GenealogyFlow({ graph }) {
  const nodes = useMemo(
    () => (graph?.nodes ?? []).map((n) => ({ ...n, type: 'lot' })),
    [graph],
  );
  const edges = useMemo(
    () => (graph?.edges ?? []).map((e) => ({
      ...e,
      animated: e.data?.direction === 'backward',
      markerEnd: { type: MarkerType.ArrowClosed },
    })),
    [graph],
  );

  if (!nodes.length) return <p>No genealogy recorded for this batch.</p>;

  return (
    <div style={{ height: 480, width: '100%' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
      >
        <Background />
        <Controls />
        <MiniMap />
      </ReactFlow>
    </div>
  );
}
```

## Page wiring

1. Form: `product_id` + `use_by` → `GET /stock/recall/`.
2. If `lot_count > 1`, tabs/cards per lot (`lot.trace_number`).
3. For selected lot: `<GenealogyFlow graph={lot.genealogy.graph} />`.
4. Side panel on node click: show balances / stock_units from the same lot payload.
5. Legend: left = ingredients, centre = this pack, right = where it went.

## Optional nicer layout

API positions work out of the box. For denser trees, run
[dagre](https://reactflow.dev/examples/layout/dagre) on the client after fetch;
keep `id` / `source` / `target` unchanged.

## What you do NOT need

- Extra `/trace/backward` calls — `graph` is complete per lot.
- Recipe BOM (`/recipe/product/.../tree/`) — that is recipe design, not stock lots.
