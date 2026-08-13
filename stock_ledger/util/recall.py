"""Complaint / recall reports: product + use-by → lots + genealogy trees."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q

from product.models import Product
from product.query import active_products
from stock_ledger.models import (
    StockBalance,
    StockEntry,
    StockEntryType,
    StockGenealogy,
    StockLot,
    StockUnit,
)
from stock_ledger.util.serialize import (
    BALANCE_SELECT_RELATED,
    receipt_meta_by_lot_ids,
    serialize_balance_row,
)
from stock_ledger.util.trace import trace_backward, trace_forward

PRODUCT_GENEALOGY_LOT_CAP = 200


class RecallLookupError(ValueError):
    """Missing / inactive product for recall lookup."""

    def __init__(self, message: str, *, status_code: int = 404):
        super().__init__(message)
        self.status_code = status_code


def _dec(value):
    return str(value) if value is not None else None


def _lot_payload(lot: StockLot) -> dict:
    return {
        'id': lot.id,
        'product_id': lot.product_id,
        'recipe_version_id': lot.recipe_version_id,
        'shape_format_id': lot.shape_format_id,
        'trace_number': lot.trace_number,
        'supplier_lot_code': lot.supplier_lot_code,
        'origin': lot.origin,
        'production_date': (
            lot.production_date.isoformat() if lot.production_date else None
        ),
        'use_by': lot.use_by.isoformat() if lot.use_by else None,
        'created_at': lot.created_at.isoformat() if lot.created_at else None,
    }


def _stock_unit_payload(unit: StockUnit) -> dict:
    return {
        'id': unit.id,
        'unit_serial': unit.unit_serial,
        'lot_id': unit.lot_id,
        'location_id': unit.location_id,
        'unit_id': unit.unit_id,
        'quantity_initial': _dec(unit.quantity_initial),
        'quantity_remaining': _dec(unit.quantity_remaining),
        'status': unit.status,
        'created_by_entry_id': unit.created_by_entry_id,
        'created_at': unit.created_at.isoformat() if unit.created_at else None,
        'voided_at': unit.voided_at.isoformat() if unit.voided_at else None,
        'void_reason': unit.void_reason,
    }


def _product_payload(product: Product) -> dict:
    return {
        'id': product.id,
        'name': product.name,
        'recipe_code': product.recipe_code,
        'external_barcode': product.external_barcode,
        'label_mode': product.label_mode,
        'unit_id': product.unit_id,
        'unit_name': product.unit.name if product.unit_id else None,
        'is_active': product.is_active,
    }


def enrich_trace_rows(rows: list[dict]) -> list[dict]:
    """Add product_name; stringify Decimal quantity_base."""
    product_ids = {
        int(row['product_id'])
        for row in rows
        if row.get('product_id') is not None
    }
    names = {}
    if product_ids:
        names = dict(
            Product.objects.filter(pk__in=product_ids).values_list('id', 'name')
        )
    out = []
    for row in rows:
        item = dict(row)
        pid = item.get('product_id')
        item['product_name'] = names.get(int(pid)) if pid is not None else None
        qty = item.get('quantity_base')
        if isinstance(qty, Decimal):
            item['quantity_base'] = _dec(qty)
        out.append(item)
    return out


def _node_id(lot_id: int) -> str:
    return f'lot-{lot_id}'


def _supplier_node_id(location_id: int) -> str:
    return f'supplier-{location_id}'


def _location_node_id(location_id: int) -> str:
    return f'loc-{location_id}'


def _layout_nodes(nodes: dict[str, dict], *, x_gap: int = 280, y_gap: int = 120) -> None:
    by_depth: dict[int, list[str]] = defaultdict(list)
    for nid, node in nodes.items():
        by_depth[int(node['data'].get('depth', 0))].append(nid)
    for depth, ids in by_depth.items():
        for i, nid in enumerate(sorted(ids)):
            nodes[nid]['position'] = {
                'x': depth * x_gap,
                'y': (i - (len(ids) - 1) / 2) * y_gap,
            }


def _attach_cycle_ends(
    *,
    nodes: dict[str, dict],
    edges: list[dict],
    seen_edges: set[str],
    lot_ids: set[int],
    focus_lot_id: int,
) -> None:
    """
    Extend genealogy with supplier (left) and stocked locations / transfers (right)
    so one graph reads: Supplier → lots → Dispatch.
    """
    if not lot_ids:
        return

    # --- Suppliers via purchase receipts ---
    for entry in (
        StockEntry.objects
        .filter(lot_id__in=lot_ids, entry_type=StockEntryType.RECEIPT)
        .select_related('counterparty_location', 'lot')
        .order_by('id')
    ):
        supplier = entry.counterparty_location
        if supplier is None:
            continue
        sid = _supplier_node_id(supplier.id)
        lot_nid = _node_id(entry.lot_id)
        lot_node = nodes.get(lot_nid)
        if lot_node is None:
            continue
        lot_depth = int(lot_node['data'].get('depth', 0))
        if sid not in nodes:
            nodes[sid] = {
                'id': sid,
                'type': 'supplier',
                'position': {'x': 0, 'y': 0},
                'data': {
                    'location_id': supplier.id,
                    'name': supplier.name,
                    'role': 'supplier',
                    'depth': lot_depth - 1,
                    'po_number': entry.po_number or None,
                },
            }
        else:
            # Keep furthest-left depth
            nodes[sid]['data']['depth'] = min(
                int(nodes[sid]['data'].get('depth', 0)),
                lot_depth - 1,
            )
            if entry.po_number and not nodes[sid]['data'].get('po_number'):
                nodes[sid]['data']['po_number'] = entry.po_number

        edge_id = f'receipt-{entry.id}'
        if edge_id not in seen_edges:
            seen_edges.add(edge_id)
            edges.append({
                'id': edge_id,
                'source': sid,
                'target': lot_nid,
                'label': 'receipt',
                'data': {
                    'kind': 'receipt',
                    'quantity': _dec(entry.quantity),
                    'entry_id': entry.id,
                    'po_number': entry.po_number or None,
                },
            })

    # --- Transfers: location → location for each lot ---
    transfer_outs = (
        StockEntry.objects
        .filter(lot_id__in=lot_ids, entry_type=StockEntryType.TRANSFER_OUT)
        .select_related('location', 'counterparty_location')
        .order_by('effective_at', 'id')
    )
    for entry in transfer_outs:
        src = entry.location
        dst = entry.counterparty_location
        if src is None or dst is None:
            continue
        src_id = _location_node_id(src.id)
        dst_id = _location_node_id(dst.id)
        lot_nid = _node_id(entry.lot_id)
        lot_node = nodes.get(lot_nid)
        base_depth = int(lot_node['data'].get('depth', 0)) if lot_node else 0

        for loc, nid, depth in (
            (src, src_id, base_depth),
            (dst, dst_id, base_depth + 1),
        ):
            if nid not in nodes:
                nodes[nid] = {
                    'id': nid,
                    'type': 'location',
                    'position': {'x': 0, 'y': 0},
                    'data': {
                        'location_id': loc.id,
                        'name': loc.name,
                        'role': 'location',
                        'depth': depth,
                    },
                }
            else:
                # Prefer deeper (further right) for dest sites like Dispatch
                nodes[nid]['data']['depth'] = max(
                    int(nodes[nid]['data'].get('depth', 0)),
                    depth,
                )

        # Lot touches source location
        touch_id = f'at-{entry.lot_id}-{src.id}'
        if lot_node is not None and touch_id not in seen_edges:
            seen_edges.add(touch_id)
            edges.append({
                'id': touch_id,
                'source': lot_nid,
                'target': src_id,
                'label': 'at',
                'data': {'kind': 'stocked_at', 'lot_id': entry.lot_id},
            })

        move_id = f'transfer-{entry.id}'
        if move_id not in seen_edges:
            seen_edges.add(move_id)
            edges.append({
                'id': move_id,
                'source': src_id,
                'target': dst_id,
                'label': 'transfer',
                'data': {
                    'kind': 'transfer',
                    'lot_id': entry.lot_id,
                    'quantity': _dec(abs(entry.quantity)),
                    'entry_id': entry.id,
                    'transfer_group_id': entry.transfer_group_id,
                },
            })

    # --- Current balances: lot → location (Dispatch end of cycle) ---
    for bal in (
        StockBalance.objects
        .filter(lot_id__in=lot_ids)
        .select_related('location')
        .order_by('lot_id', 'location_id')
    ):
        if bal.quantity is not None and bal.quantity == 0:
            continue
        loc = bal.location
        if loc is None:
            continue
        lid = _location_node_id(loc.id)
        lot_nid = _node_id(bal.lot_id)
        lot_node = nodes.get(lot_nid)
        if lot_node is None:
            continue
        # Focus lot balances sit furthest right
        depth = int(lot_node['data'].get('depth', 0)) + (
            2 if bal.lot_id == focus_lot_id else 1
        )
        if lid not in nodes:
            nodes[lid] = {
                'id': lid,
                'type': 'location',
                'position': {'x': 0, 'y': 0},
                'data': {
                    'location_id': loc.id,
                    'name': loc.name,
                    'role': 'location',
                    'depth': depth,
                },
            }
        else:
            nodes[lid]['data']['depth'] = max(
                int(nodes[lid]['data'].get('depth', 0)),
                depth,
            )

        edge_id = f'balance-{bal.lot_id}-{loc.id}'
        if edge_id not in seen_edges:
            seen_edges.add(edge_id)
            edges.append({
                'id': edge_id,
                'source': lot_nid,
                'target': lid,
                'label': _dec(bal.quantity),
                'data': {
                    'kind': 'balance',
                    'quantity': _dec(bal.quantity),
                    'lot_id': bal.lot_id,
                },
            })


def build_genealogy_graph(
    *,
    root_lot: StockLot,
    backward: list[dict],
    forward: list[dict],
) -> dict:
    """
    Full cycle graph for React Flow:
    Supplier → ingredient lots → FG (focus) → locations (Dispatch).
    """
    entry_ids: set[int] = set()
    for row in (*backward, *forward):
        if row.get('input_entry_id') is not None:
            entry_ids.add(int(row['input_entry_id']))
        if row.get('output_entry_id') is not None:
            entry_ids.add(int(row['output_entry_id']))

    entry_lot: dict[int, StockLot] = {}
    if entry_ids:
        for entry in (
            StockEntry.objects
            .filter(pk__in=entry_ids)
            .select_related('lot__product')
        ):
            entry_lot[entry.id] = entry.lot

    root = (
        StockLot.objects
        .select_related('product')
        .filter(pk=root_lot.id)
        .first()
    ) or root_lot

    nodes: dict[str, dict] = {}
    roles: dict[int, str] = {root.id: 'focus'}
    depths: dict[int, int] = {root.id: 0}

    def ensure_lot_node(lot: StockLot, *, role: str, depth: int) -> None:
        nid = _node_id(lot.id)
        existing = nodes.get(nid)
        if existing is None:
            product = getattr(lot, 'product', None)
            nodes[nid] = {
                'id': nid,
                'type': 'lot',
                'position': {'x': 0, 'y': 0},
                'data': {
                    'lot_id': lot.id,
                    'product_id': lot.product_id,
                    'product_name': product.name if product is not None else None,
                    'trace_number': lot.trace_number,
                    'supplier_lot_code': lot.supplier_lot_code,
                    'origin': lot.origin,
                    'use_by': lot.use_by.isoformat() if lot.use_by else None,
                    'production_date': (
                        lot.production_date.isoformat()
                        if lot.production_date
                        else None
                    ),
                    'role': role,
                    'depth': depth,
                },
            }
            roles[lot.id] = role
            depths[lot.id] = depth
            return
        if role == 'focus' or (
            roles.get(lot.id) != 'focus' and abs(depth) > abs(depths.get(lot.id, 0))
        ):
            existing['data']['role'] = role
            existing['data']['depth'] = depth
            roles[lot.id] = role
            depths[lot.id] = depth

    ensure_lot_node(root, role='focus', depth=0)

    edges: list[dict] = []
    seen_edges: set[str] = set()

    def add_gene_edge(row: dict, *, direction: str) -> None:
        inp_id = int(row['input_entry_id'])
        out_id = int(row['output_entry_id'])
        inp_lot = entry_lot.get(inp_id)
        out_lot = entry_lot.get(out_id)
        if inp_lot is None or out_lot is None:
            return
        depth = int(row.get('depth') or 1)
        if direction == 'backward':
            ensure_lot_node(inp_lot, role='upstream', depth=-depth)
            ensure_lot_node(
                out_lot,
                role='upstream' if out_lot.id != root.id else 'focus',
                depth=-(depth - 1) if depth > 1 else 0,
            )
        else:
            ensure_lot_node(out_lot, role='downstream', depth=depth)
            ensure_lot_node(
                inp_lot,
                role='downstream' if inp_lot.id != root.id else 'focus',
                depth=depth - 1 if depth > 1 else 0,
            )

        edge_id = f'e-{inp_id}-{out_id}'
        if edge_id in seen_edges:
            return
        seen_edges.add(edge_id)
        qty = row.get('quantity_base')
        edges.append({
            'id': edge_id,
            'source': _node_id(inp_lot.id),
            'target': _node_id(out_lot.id),
            'label': _dec(qty) if qty is not None else None,
            'data': {
                'kind': 'genealogy',
                'direction': direction,
                'depth': depth,
                'quantity_base': _dec(qty) if isinstance(qty, Decimal) else qty,
                'input_entry_id': inp_id,
                'output_entry_id': out_id,
            },
        })

    for row in backward:
        add_gene_edge(row, direction='backward')
    for row in forward:
        add_gene_edge(row, direction='forward')

    lot_ids = {
        int(n['data']['lot_id'])
        for n in nodes.values()
        if n.get('type') == 'lot' and n['data'].get('lot_id') is not None
    }
    _attach_cycle_ends(
        nodes=nodes,
        edges=edges,
        seen_edges=seen_edges,
        lot_ids=lot_ids,
        focus_lot_id=root.id,
    )
    _layout_nodes(nodes)

    return {
        'nodes': list(nodes.values()),
        'edges': edges,
    }


def _genealogy_for_lot(lot: StockLot) -> dict:
    backward = enrich_trace_rows(trace_backward(lot_id=lot.id))
    forward = enrich_trace_rows(trace_forward(lot_id=lot.id))
    return {
        'backward': backward,
        'forward': forward,
        'graph': build_genealogy_graph(
            root_lot=lot,
            backward=backward,
            forward=forward,
        ),
    }


def _balances_for_lots(lot_ids: list[int]) -> dict[int, list[dict]]:
    if not lot_ids:
        return {}
    qs = (
        StockBalance.objects.filter(lot_id__in=lot_ids)
        .select_related(*BALANCE_SELECT_RELATED)
        .order_by('lot_id', 'location_id')
    )
    receipt_meta = receipt_meta_by_lot_ids(set(lot_ids))
    by_lot: dict[int, list[dict]] = defaultdict(list)
    for bal in qs:
        by_lot[bal.lot_id].append(
            serialize_balance_row(bal, receipt_meta=receipt_meta.get(bal.lot_id))
        )
    return by_lot


def _units_for_lots(lot_ids: list[int]) -> dict[int, list[dict]]:
    if not lot_ids:
        return {}
    by_lot: dict[int, list[dict]] = defaultdict(list)
    for unit in (
        StockUnit.objects.filter(lot_id__in=lot_ids)
        .order_by('lot_id', 'id')
    ):
        by_lot[unit.lot_id].append(_stock_unit_payload(unit))
    return by_lot


def _require_active_product(product_id: int) -> Product:
    product = (
        active_products()
        .select_related('unit')
        .filter(pk=product_id)
        .first()
    )
    if product is None:
        raise RecallLookupError(
            f'product_id={product_id} not found.',
            status_code=404,
        )
    return product


def _genealogy_flags(lot_ids: list[int]) -> tuple[set[int], dict[int, int]]:
    """Return (lot_ids_with_edges, edge_count_per_lot)."""
    if not lot_ids:
        return set(), {}
    entry_rows = list(
        StockEntry.objects.filter(lot_id__in=lot_ids).values_list('id', 'lot_id')
    )
    if not entry_rows:
        return set(), {}
    entry_to_lot = {eid: lid for eid, lid in entry_rows}
    entry_ids = list(entry_to_lot.keys())
    edges = StockGenealogy.objects.filter(
        Q(input_entry_id__in=entry_ids) | Q(output_entry_id__in=entry_ids)
    ).values_list('id', 'input_entry_id', 'output_entry_id')

    with_gene: set[int] = set()
    counts: dict[int, int] = defaultdict(int)
    for _edge_id, inp, out in edges:
        seen_lots: set[int] = set()
        for eid in (inp, out):
            lid = entry_to_lot.get(eid)
            if lid is not None:
                with_gene.add(lid)
                seen_lots.add(lid)
        for lid in seen_lots:
            counts[lid] += 1
    return with_gene, dict(counts)


def build_lot_detail(lot: StockLot, *, balances: list[dict], units: list[dict]) -> dict:
    gene = _genealogy_for_lot(lot)
    has = bool(gene['backward'] or gene['forward'])
    return {
        'lot': _lot_payload(lot),
        'balances': balances,
        'stock_units': units,
        'has_genealogy': has,
        'genealogy': gene,
    }


def build_recall_report(*, product_id: int, use_by: date) -> dict:
    product = _require_active_product(product_id)
    lots = list(
        StockLot.objects.filter(product_id=product_id, use_by=use_by)
        .order_by('id')
    )
    lot_ids = [lot.id for lot in lots]
    balances_by = _balances_for_lots(lot_ids)
    units_by = _units_for_lots(lot_ids)
    return {
        'product': _product_payload(product),
        'use_by': use_by.isoformat(),
        'lot_count': len(lots),
        'lots': [
            build_lot_detail(
                lot,
                balances=balances_by.get(lot.id, []),
                units=units_by.get(lot.id, []),
            )
            for lot in lots
        ],
    }


def build_product_genealogy_index(
    *,
    product_id: int,
    with_trees: bool = True,
) -> dict:
    product = _require_active_product(product_id)
    lots = list(
        StockLot.objects.filter(product_id=product_id)
        .order_by('-id')[:PRODUCT_GENEALOGY_LOT_CAP]
    )
    lot_ids = [lot.id for lot in lots]
    with_gene, edge_counts = _genealogy_flags(lot_ids)

    rows = []
    if with_trees:
        balances_by = _balances_for_lots(lot_ids)
        units_by = _units_for_lots(lot_ids)
        for lot in lots:
            detail = build_lot_detail(
                lot,
                balances=balances_by.get(lot.id, []),
                units=units_by.get(lot.id, []),
            )
            detail['edge_count'] = edge_counts.get(lot.id, 0)
            rows.append(detail)
    else:
        for lot in lots:
            rows.append({
                'lot': _lot_payload(lot),
                'has_genealogy': lot.id in with_gene,
                'edge_count': edge_counts.get(lot.id, 0),
            })

    return {
        'product': _product_payload(product),
        'lot_count': len(lots),
        'with_trees': with_trees,
        'lots': rows,
    }
