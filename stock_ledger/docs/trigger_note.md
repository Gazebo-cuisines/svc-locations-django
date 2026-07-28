On every new stock movement (stock_entry)
stock_entry_bi (before insert)
Runs before the row is saved:

Period check — period_id must be an open period. Closed period → reject.
Future-date check — effective_at can’t be more than ~1 minute in the future → reject.
Lock the chain head — locks stock_chain_head (id=1) so two inserts can’t race.
Fill audit fields — sets recorded_at to now, copies current head hash into prev_hash.
Compute entry_hash — SHA-256 of the important fields (qty, lot, location, etc.) so the row is tamper-evident.
stock_entry_ai (after insert)
After the row is saved: updates stock_chain_head to point at this new entry (head_entry_id, head_hash, entry_count++).

stock_entry_bu / stock_entry_bd
Blocks UPDATE and DELETE. Mistakes must be fixed with a new reversal row, not by editing history.

Append-only side tables
stock_genealogy_bu / bd
Genealogy edges (input→output for production) cannot be changed or deleted.

stock_lot_amendment_bu / bd
Lot amendments (e.g. use-by changes) cannot be changed or deleted — only new amendment rows.

Balance guard
stock_balance_bu (before update on balance)
If the new quantity would go negative and there is no authorised override entry linked → reject.
Negative stock is only allowed when the write path sets negative_authorised_by_entry_id.

In one line: triggers make the ledger a hash chain, stop silent edits, and block unauthorised negative stock — even if something bypasses Django and talks to MySQL directly.