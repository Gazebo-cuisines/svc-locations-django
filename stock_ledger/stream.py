"""In-process SSE hub for stock balance deltas.

ponytail: global in-memory hub — single process only. Use Redis pub/sub
when multi-gunicorn-worker / multi-host fan-out is required.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Iterator

COALESCE_MS = 150
HEARTBEAT_S = 20
QUEUE_MAX = 256

_lock = threading.Lock()
_pending: dict[tuple[int, int], dict] = {}
_flush_scheduled = False
_subscribers: list[queue.Queue] = []


def _key(event: dict) -> tuple[int, int]:
    row = event['row']
    return (int(row['lot_id']), int(row['location_id']))


def _broadcast(event: dict) -> None:
    payload = json.dumps(event, separators=(',', ':'))
    dead: list[queue.Queue] = []
    with _lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(payload)
        except queue.Full:
            dead.append(q)
    if dead:
        with _lock:
            for q in dead:
                try:
                    _subscribers.remove(q)
                except ValueError:
                    pass


def _flush() -> None:
    global _flush_scheduled
    time.sleep(COALESCE_MS / 1000.0)
    with _lock:
        events = list(_pending.values())
        _pending.clear()
        _flush_scheduled = False
    for event in events:
        _broadcast(event)


def publish_balance_delta(event: dict) -> None:
    """Enqueue a delta; last event per (lot_id, location_id) wins in coalesce window."""
    global _flush_scheduled
    with _lock:
        _pending[_key(event)] = event
        if not _flush_scheduled:
            _flush_scheduled = True
            threading.Thread(target=_flush, daemon=True).start()


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def iter_sse(q: queue.Queue) -> Iterator[str]:
    """Yield SSE frames until the client disconnects (StopIteration from generator close)."""
    try:
        while True:
            try:
                payload = q.get(timeout=HEARTBEAT_S)
                yield f'data: {payload}\n\n'
            except queue.Empty:
                yield ': ping\n\n'
    finally:
        unsubscribe(q)


if __name__ == '__main__':
    # Self-check: coalesce keeps last event per key.
    q = subscribe()
    publish_balance_delta(
        {'type': 'upsert', 'at': 'a', 'row': {'lot_id': 1, 'location_id': 1, 'quantity': '1'}}
    )
    publish_balance_delta(
        {'type': 'upsert', 'at': 'b', 'row': {'lot_id': 1, 'location_id': 1, 'quantity': '2'}}
    )
    time.sleep(COALESCE_MS / 1000.0 + 0.05)
    got = json.loads(q.get_nowait())
    assert got['row']['quantity'] == '2', got
    unsubscribe(q)
    print('ok')
