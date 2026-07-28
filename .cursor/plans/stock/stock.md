Stock Ledger and Stock Balance module

This is the accuracy-critical core of the ERP. Everything below is written to be falsifiable: each design choice states what it prevents and what it does not.

Verified starting state

3.10.84.186:3306 authenticates as gazebo_dev. MySQL 8.4.10 (Ubuntu), REPEATABLE-READ, strict sql_mode, log_bin=ON with binlog_format=ROW, log_bin_trust_function_creators=0, innodb_flush_log_at_trx_commit=1. Grants are ALL PRIVILEGES on DB_LOCATIONS, production and production_dev, plus global CREATE, PROCESS.

The binlog setting blocks this user from creating stored functions; it does not restrict triggers. The design therefore uses triggers only and declares no routines.

DB_LOCATIONS holds 34 tables and 0 procedures, 0 functions, 0 triggers. Applied migrations: locations 0001-0002, product 0001-0003, recipe 0001.

The legacy databases on this server are empty







Source



movements



cache rows



products



recipe tree





production (live server)



0



0



39



47





production_dev (live server)



0



0



39



47





Dump20260720.sql



2,938



1,002



2,387



4,576

production carries 278 routines and 127 triggers with no data behind them — a structure-only restore. A shadow-run against this server is impossible. The live system holding current stock is elsewhere and its connection details are still outstanding; see the get-live-endpoint todo, which blocks the shadow-run todo.

Blocking dependency outside this module

DB_LOCATIONS.product holds 1 row against the dump's 2,387, and recipe, recipe_version, recipe_component are all empty. Every stock_lot needs a valid product_id, so backfill, replay and validation are all blocked until the product module is populated. This is the critical path for the stock module and it is not a stock-module task.

What the legacy audit changes

The legacy subsystem was audited in full: 152 tables, 344 views, 169 procedures, 109 functions, ~100 triggers. Four findings drive the design.

The "blockchain" is not one. procSTKstockBlockChainKeep writes tblstockmovementBlockChain(rowID, rowIDpreceeding) by selecting max(id) WHERE id < current for the same lot. No hash, no signature, no tamper evidence. Because the chain lived in a separate table, tblstockmovement.previousChainID was left dead and never written by any code in the dump. This is the direct reason the new chain is stored inline on the entry row as NOT NULL — a separate chain table makes an unchained entry representable, and legacy proves that is what happens in practice.

Backward trace does not exist. procSTKitemTraceBack is an empty BEGIN/END. Forward trace is a BFS over tblstockmovementAppends keyed on the string item#tracenumber.

The ledger is not self-contained. Balance is sum(stkrecon) + sum(stkin) - sum(stkout) restricted to date >= fnSTKlaststockreconfromcache(...), so reading it requires a second table. That anchor table is maintained by procRebuildStockCacheLastReconDate, which truncates and refills it. The rebuild WHERE clause also has an unparenthesised AND date >= lastRecon OR isnull(lastRecon), which binds as (A AND B) OR C.

Costing is empty. itemcost and linecost exist on every movement row and no procedure writes them. There is no labour cost on stock rows; tblOPSstaffAllocationDetails is empty in the dump.

Two deliberate departures from legacy

Legacy conflates lot identity with location — destcontainer sits inside the cache unique key, so moving a pallet mints a new identity. Here lot identity is intrinsic and location is a balance dimension.

Legacy stores a stock count as an absolute that discards all prior history, which is why it can never recompute a historical balance. Here a count is stored as the delta it implies, so balance is a plain SUM over all entries, the recon-anchor table disappears, and any as-of-date balance is recoverable.

Why "port the balance math 1:1" is rejected

It cannot coexist with an append-only ledger, because procRebuildStockCacheForItem depends on a TRUNCATE-and-refill anchor table. It would import the operator-precedence bug. And porting the absolute-recon semantics would destroy the recall and mass-balance requirement that motivates the whole module.

The replacement gate is behavioural equivalence on final balances, which is stronger as a test and honest about the internals: replay the dump's 2,938 movements and assert that all 1,002 legacy tblstockcache.quantity values equal the resulting stock_balance.quantity exactly.

Threat model for immutability

Stating precisely what each layer buys, because the previous revision of this plan overstated it.





Triggers that SIGNAL on UPDATE and DELETE stop the application, an ORM bug, and a careless operator. They do not stop anyone who can DROP TRIGGER.



An inline hash chain detects an edit to part of history. It does not detect a full rewrite, because whoever rewrites row 500 can recompute rows 500..N and the chain verifies clean.



Split credentials remove UPDATE, DELETE, DROP and TRIGGER from the runtime user, so a compromised application cannot rewrite anything. Today gazebo_dev does migrations and would serve traffic, which makes the trigger guards theatre.



An external anchor is the only layer that detects a full rewrite. The head hash and entry count are published to S3 Object Lock in compliance mode on a short interval, under an IAM role that can PutObject but not delete or overwrite. A rewrite then contradicts anchors that no database credential can reach.

All four are in scope.

Data model

flowchart LR
  product --> stock_lot
  recipe_version --> stock_lot
  stock_lot --> stock_lot_amendment
  stock_lot --> stock_entry
  loc_location --> stock_entry
  stock_period --> stock_entry
  stock_entry --> stock_genealogy
  stock_entry --> stock_balance
  stock_entry --> stock_chain_head
  stock_chain_head --> stock_chain_anchor
  stock_lot --> stock_reservation
  stock_balance --> stock_reservation

Nine tables. Three are append-only and hash-guarded (stock_entry, stock_genealogy, stock_lot_amendment); the rest are mutable state or read models, and are labelled as such.

stock_lot — intrinsic lot identity

CREATE TABLE stock_lot (
  id                  BIGINT NOT NULL AUTO_INCREMENT,
  product_id          INT NOT NULL,
  recipe_version_id   INT NULL,
  shape_format_id     INT NULL,
  trace_number        VARCHAR(32) NOT NULL,
  supplier_lot_code   VARCHAR(64) NULL,
  origin              VARCHAR(16) NOT NULL,
  production_date     DATE NULL,
  use_by              DATE NULL,
  created_at          DATETIME(6) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_stock_lot_identity
    (product_id, trace_number, production_date, use_by, recipe_version_id, shape_format_id),
  KEY idx_stock_lot_product_useby (product_id, use_by),
  KEY idx_stock_lot_trace (trace_number),
  CONSTRAINT fk_stock_lot_product        FOREIGN KEY (product_id)        REFERENCES product (id),
  CONSTRAINT fk_stock_lot_recipe_version FOREIGN KEY (recipe_version_id) REFERENCES recipe_version (id),
  CONSTRAINT fk_stock_lot_shape_format   FOREIGN KEY (shape_format_id)   REFERENCES product_purchase_shape_format (id),
  CONSTRAINT chk_stock_lot_origin CHECK (origin IN ('production','purchase','opening'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

trace_number is VARCHAR(32), not legacy's INT, so goods-in carries a supplier's own lot code. destcontainer is deliberately absent from the key.

There is no updated_at. The previous revision had one, which quietly made lot attributes mutable — and legacy shows shelf-life extension is a real business action (extendedUseBy, extendedOffset, extendedOffsetUser on the cache). Editing a lot in place would make every prior trace report unreproducible, so amendments are events.

stock_lot_amendment — append-only lot changes

CREATE TABLE stock_lot_amendment (
  id                BIGINT NOT NULL AUTO_INCREMENT,
  lot_id            BIGINT NOT NULL,
  field_name        VARCHAR(32) NOT NULL,
  old_value         VARCHAR(64) NULL,
  new_value         VARCHAR(64) NULL,
  reason            VARCHAR(255) NOT NULL,
  authorised_by_user_id INT NOT NULL,
  effective_at      DATETIME(6) NOT NULL,
  recorded_at       DATETIME(6) NOT NULL,
  PRIMARY KEY (id),
  KEY idx_stock_lot_amendment_lot (lot_id, recorded_at),
  CONSTRAINT fk_stock_lot_amendment_lot FOREIGN KEY (lot_id) REFERENCES stock_lot (id),
  CONSTRAINT chk_stock_lot_amendment_field CHECK (field_name IN ('use_by','production_date','supplier_lot_code'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

The effective use-by at any point in time is the lot's original value plus amendments up to that time. Trace reports resolve it as-of, so they stay reproducible.

stock_entry — the immutable hash-chained ledger

One signed quantity replaces legacy's stkin / stkout / stkrecon triple: positive is into location_id, negative is out.

CREATE TABLE stock_entry (
  id                        BIGINT NOT NULL AUTO_INCREMENT,
  idempotency_key           VARCHAR(64)  NOT NULL,
  entry_type                VARCHAR(24)  NOT NULL,
  lot_id                    BIGINT       NOT NULL,
  location_id               INT          NOT NULL,
  counterparty_location_id  INT          NULL,
  transfer_group_id         CHAR(36)     NULL,
  quantity                  DECIMAL(16,6) NOT NULL,
  unit_id                   INT          NOT NULL,
  base_unit_factor          DECIMAL(16,6) NULL,
  quantity_base             DECIMAL(16,6) NULL,
  period_id                 INT          NOT NULL,
  effective_at              DATETIME(6)  NOT NULL,
  recorded_at               DATETIME(6)  NOT NULL,
  reverses_entry_id         BIGINT       NULL,
  override_reason           VARCHAR(255) NULL,
  authorised_by_user_id     INT          NULL,
  source_document_type      VARCHAR(24)  NULL,
  source_document_id        BIGINT       NULL,
  source_document_line      INT          NULL,
  unit_cost                 DECIMAL(16,6) NULL,
  line_cost                 DECIMAL(16,6) NULL,
  actor_user_id             INT          NULL,
  lan_username              VARCHAR(64)  NULL,
  source_workstation        VARCHAR(64)  NULL,
  source_workstation_ip     VARCHAR(45)  NULL,
  remarks                   LONGTEXT     NULL,
  prev_hash                 CHAR(64)     NULL,
  entry_hash                CHAR(64)     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_stock_entry_idempotency (idempotency_key),
  UNIQUE KEY uq_stock_entry_hash        (entry_hash),
  UNIQUE KEY uq_stock_entry_reverses    (reverses_entry_id),
  KEY idx_stock_entry_lot_loc   (lot_id, location_id, id),
  KEY idx_stock_entry_effective (effective_at),
  KEY idx_stock_entry_location  (location_id, effective_at),
  KEY idx_stock_entry_document  (source_document_type, source_document_id),
  KEY idx_stock_entry_transfer  (transfer_group_id),
  CONSTRAINT fk_stock_entry_lot          FOREIGN KEY (lot_id)                   REFERENCES stock_lot (id),
  CONSTRAINT fk_stock_entry_location     FOREIGN KEY (location_id)              REFERENCES loc_location (id),
  CONSTRAINT fk_stock_entry_counterparty FOREIGN KEY (counterparty_location_id) REFERENCES loc_location (id),
  CONSTRAINT fk_stock_entry_unit         FOREIGN KEY (unit_id)                  REFERENCES product_unit (id),
  CONSTRAINT fk_stock_entry_reverses     FOREIGN KEY (reverses_entry_id)        REFERENCES stock_entry (id),
  CONSTRAINT fk_stock_entry_period       FOREIGN KEY (period_id)                REFERENCES stock_period (id),
  CONSTRAINT chk_stock_entry_type CHECK (entry_type IN (
    'receipt','issue','transfer_out','transfer_in','production_output',
    'production_consumption','count_adjustment','disposal','reversal')),
  CONSTRAINT chk_stock_entry_qty_nonzero CHECK (quantity <> 0),
  CONSTRAINT chk_stock_entry_factor_pos  CHECK (base_unit_factor IS NULL OR base_unit_factor > 0),
  CONSTRAINT chk_stock_entry_mass_pair   CHECK ((quantity_base IS NULL) = (base_unit_factor IS NULL)),
  CONSTRAINT chk_stock_entry_override    CHECK ((override_reason IS NULL) = (authorised_by_user_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

UNIQUE (reverses_entry_id) means an entry can be reversed at most once. There is deliberately no mutable is_reversed flag — setting one would require the UPDATE the guards forbid — so reversal status is derived by joining on it.

Nine entry types, not four. IN/OUT/TRANSFER/RECON cannot distinguish production consumption from a sale, or disposal from a transfer. Without that distinction yield loss is uncomputable, and yield loss is half the stated goal. Legacy's eleven tblstockmovementbatchactions map cleanly: STOCKIN to receipt, STOCKOUT to issue, STOCKRECON to count_adjustment, STOCKTRANSFER and the four BULKTRANSF* codes to a transfer_out/transfer_in pair, ITEMTRANSFER and ITEMTRANSFERRECIPE to a production_consumption/production_output pair. Legacy's overloading of action with department codes such as PRODHIGHRISK moves to location_id.

stock_period — close guard

CREATE TABLE stock_period (
  id            INT NOT NULL AUTO_INCREMENT,
  period_start  DATE NOT NULL,
  period_end    DATE NOT NULL,
  status        VARCHAR(8) NOT NULL DEFAULT 'open',
  closed_at     DATETIME(6) NULL,
  closed_by_user_id INT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_stock_period_range (period_start, period_end),
  CONSTRAINT chk_stock_period_status CHECK (status IN ('open','closed')),
  CONSTRAINT chk_stock_period_order  CHECK (period_end >= period_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

Period close subsumes the backdating question, and does so on accounting-correct grounds rather than an arbitrary constant: backdating inside an open period is legitimate, backdating into a closed one is forbidden. Future-dating is capped at one minute of clock skew.

stock_chain_head and stock_chain_anchor

CREATE TABLE stock_chain_head (
  id            TINYINT     NOT NULL,
  head_entry_id BIGINT      NULL,
  head_hash     CHAR(64)    NULL,
  entry_count   BIGINT      NOT NULL DEFAULT 0,
  updated_at    DATETIME(6) NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT chk_stock_chain_head_singleton CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE stock_chain_anchor (
  id             BIGINT NOT NULL AUTO_INCREMENT,
  head_entry_id  BIGINT NOT NULL,
  head_hash      CHAR(64) NOT NULL,
  entry_count    BIGINT NOT NULL,
  s3_object_key  VARCHAR(255) NOT NULL,
  s3_version_id  VARCHAR(128) NULL,
  anchored_at    DATETIME(6) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_stock_chain_anchor_entry (head_entry_id),
  KEY idx_stock_chain_anchor_time (anchored_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

stock_chain_anchor is a local index of what was published. It is not the evidence — the evidence is the WORM object in S3, which the database credentials cannot alter. Verification re-reads S3 and compares.

stock_genealogy — the trace graph

Replaces legacy's source / parentline / childline / tblstockmovementAppends string keys with real edges.

CREATE TABLE stock_genealogy (
  id              BIGINT NOT NULL AUTO_INCREMENT,
  output_entry_id BIGINT NOT NULL,
  input_entry_id  BIGINT NOT NULL,
  quantity_base   DECIMAL(16,6) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_stock_genealogy_edge (output_entry_id, input_entry_id),
  KEY idx_stock_genealogy_input (input_entry_id),
  CONSTRAINT fk_stock_genealogy_output FOREIGN KEY (output_entry_id) REFERENCES stock_entry (id),
  CONSTRAINT fk_stock_genealogy_input  FOREIGN KEY (input_entry_id)  REFERENCES stock_entry (id),
  CONSTRAINT chk_stock_genealogy_qty CHECK (quantity_base > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

stock_balance — read model, synchronous

CREATE TABLE stock_balance (
  lot_id              BIGINT NOT NULL,
  location_id         INT    NOT NULL,
  quantity            DECIMAL(16,6) NOT NULL,
  quantity_base       DECIMAL(16,6) NULL,
  last_entry_id       BIGINT NOT NULL,
  last_count_entry_id BIGINT NULL,
  negative_authorised_by_entry_id BIGINT NULL,
  updated_at          DATETIME(6) NOT NULL,
  PRIMARY KEY (lot_id, location_id),
  KEY idx_stock_balance_location (location_id),
  KEY idx_stock_balance_watermark (last_entry_id),
  CONSTRAINT fk_stock_balance_lot      FOREIGN KEY (lot_id)      REFERENCES stock_lot (id),
  CONSTRAINT fk_stock_balance_location FOREIGN KEY (location_id) REFERENCES loc_location (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

Updated synchronously in the same transaction as the ledger insert. A scheduled-only rebuild would leave availability stale between runs and let two orders allocate the same pallet — a regression even against legacy, which at least updated its cache inside the movement trigger. The scheduled job is a verifier, not the write path:

SELECT b.lot_id, b.location_id, b.quantity, SUM(e.quantity) AS ledger_qty
FROM stock_balance b
JOIN stock_entry e ON e.lot_id = b.lot_id AND e.location_id = b.location_id
GROUP BY b.lot_id, b.location_id, b.quantity
HAVING b.quantity <> SUM(e.quantity);

Because a count is a delta, this invariant is unconditional — it holds at every instant, with no recon-date window to reason about.

committed_quantity has been removed. The previous revision carried that column with nothing behind it, which was hand-waving; commitment now lives in a real table.

stock_reservation — mutable by design

CREATE TABLE stock_reservation (
  id                   BIGINT NOT NULL AUTO_INCREMENT,
  lot_id               BIGINT NOT NULL,
  location_id          INT NOT NULL,
  quantity             DECIMAL(16,6) NOT NULL,
  unit_id              INT NOT NULL,
  status               VARCHAR(12) NOT NULL DEFAULT 'open',
  source_document_type VARCHAR(24) NULL,
  source_document_id   BIGINT NULL,
  source_document_line INT NULL,
  consumed_by_entry_id BIGINT NULL,
  expires_at           DATETIME(6) NULL,
  created_at           DATETIME(6) NOT NULL,
  updated_at           DATETIME(6) NOT NULL,
  PRIMARY KEY (id),
  KEY idx_stock_reservation_open (lot_id, location_id, status),
  KEY idx_stock_reservation_doc (source_document_type, source_document_id),
  CONSTRAINT fk_stock_reservation_lot      FOREIGN KEY (lot_id)               REFERENCES stock_lot (id),
  CONSTRAINT fk_stock_reservation_location FOREIGN KEY (location_id)          REFERENCES loc_location (id),
  CONSTRAINT fk_stock_reservation_unit     FOREIGN KEY (unit_id)              REFERENCES product_unit (id),
  CONSTRAINT fk_stock_reservation_entry    FOREIGN KEY (consumed_by_entry_id) REFERENCES stock_entry (id),
  CONSTRAINT chk_stock_reservation_status CHECK (status IN ('open','consumed','released','expired')),
  CONSTRAINT chk_stock_reservation_qty    CHECK (quantity > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

Replaces legacy tblstockcacheCommitedStock plus fnSTKheldInBatchQueue and fnSTKheldInPlanOpenRows. This table is mutated — status transitions are its whole purpose — and that is fine because it is not the ledger. Available-to-promise is stock_balance.quantity minus the sum of open reservations.

stock_unit_conversion — the kilogram problem, solved explicitly

product_packaging has pack_weight, unitary_weight, gross_unitary_weight, items_per_unit and units_per_batch, all nullable, and the table holds 0 rows. product_unit contains meters and seconds, which have no mass at all — they exist for downtime pseudo-products, flagged by product.is_downtime.

CREATE TABLE stock_unit_conversion (
  id         BIGINT NOT NULL AUTO_INCREMENT,
  unit_id    INT NOT NULL,
  product_id INT NULL,
  to_kg      DECIMAL(16,6) NOT NULL,
  source     VARCHAR(24) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_stock_unit_conversion (unit_id, product_id),
  CONSTRAINT fk_stock_unit_conversion_unit    FOREIGN KEY (unit_id)    REFERENCES product_unit (id),
  CONSTRAINT fk_stock_unit_conversion_product FOREIGN KEY (product_id) REFERENCES product (id),
  CONSTRAINT chk_stock_unit_conversion_pos CHECK (to_kg > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

product_id NULL is a global rule (grams to 0.001, Kg to 1). Product-specific rows cover unit, Box and Liter, derived from product_packaging.unitary_weight. meters and seconds get no row at all, so resolution fails.

The hard rule: for a product that is not is_downtime, if no factor resolves, the write fails loudly rather than defaulting to 1. Non-mass entries store NULL in both quantity_base and base_unit_factor, and mass-balance queries filter them out. Silently defaulting a missing factor to 1 would corrupt every recall report while looking perfectly healthy.

Triggers

No stored functions, per the binlog constraint.

stock_entry_bi takes FOR UPDATE on the singleton head row, which serialises inserts and makes the chain race-free, and enforces the period and future-date guards:

CREATE TRIGGER stock_entry_bi BEFORE INSERT ON stock_entry FOR EACH ROW
BEGIN
  DECLARE v_prev CHAR(64);
  DECLARE v_status VARCHAR(8);

  SELECT status INTO v_status FROM stock_period WHERE id = NEW.period_id;
  IF v_status <> 'open' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_entry: period is closed';
  END IF;
  IF NEW.effective_at > NOW(6) + INTERVAL 1 MINUTE THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_entry: effective_at is in the future';
  END IF;

  SELECT head_hash INTO v_prev FROM stock_chain_head WHERE id = 1 FOR UPDATE;
  SET NEW.recorded_at = NOW(6);
  SET NEW.prev_hash   = v_prev;
  SET NEW.entry_hash  = SHA2(CONCAT_WS('|',
      COALESCE(v_prev,''),                    NEW.idempotency_key,
      NEW.entry_type,                         CAST(NEW.lot_id AS CHAR),
      CAST(NEW.location_id AS CHAR),          COALESCE(CAST(NEW.counterparty_location_id AS CHAR),''),
      CAST(NEW.quantity AS CHAR),             CAST(NEW.unit_id AS CHAR),
      COALESCE(CAST(NEW.base_unit_factor AS CHAR),''),
      COALESCE(CAST(NEW.quantity_base AS CHAR),''),
      CAST(NEW.period_id AS CHAR),
      DATE_FORMAT(NEW.effective_at,'%Y-%m-%d %H:%i:%s.%f'),
      DATE_FORMAT(NEW.recorded_at,'%Y-%m-%d %H:%i:%s.%f'),
      COALESCE(CAST(NEW.reverses_entry_id AS CHAR),''),
      COALESCE(NEW.override_reason,''),
      COALESCE(CAST(NEW.authorised_by_user_id AS CHAR),''),
      COALESCE(NEW.source_document_type,''),
      COALESCE(CAST(NEW.source_document_id AS CHAR),''),
      COALESCE(CAST(NEW.actor_user_id AS CHAR),'')
  ), 256);
END

Every nullable field is wrapped in COALESCE(...,'') deliberately: CONCAT_WS skips NULLs, which would shift field positions and let two materially different entries hash identically.

stock_entry_ai advances stock_chain_head. stock_entry_bu and stock_entry_bd unconditionally SIGNAL SQLSTATE '45000'; the same pair guards stock_genealogy and stock_lot_amendment. Corrections are entry_type='reversal' rows carrying reverses_entry_id.

The negative-stock guard lives on the read model, where the resulting quantity is actually known:

CREATE TRIGGER stock_balance_bu BEFORE UPDATE ON stock_balance FOR EACH ROW
BEGIN
  IF NEW.quantity < 0 AND NEW.negative_authorised_by_entry_id IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_balance: negative without authorised override';
  END IF;
END

The projector sets negative_authorised_by_entry_id only when the driving entry carries override_reason and authorised_by_user_id. Because those two fields are inside the hash payload, the exception is itself tamper-evident — you cannot retroactively invent an authorisation.

Verification

Chain continuity, which detects partial edits:

WITH RECURSIVE chain AS (
  SELECT id, entry_hash, prev_hash FROM stock_entry WHERE prev_hash IS NULL
  UNION ALL
  SELECT e.id, e.entry_hash, e.prev_hash
  FROM stock_entry e JOIN chain c ON e.prev_hash = c.entry_hash
)
SELECT (SELECT COUNT(*) FROM stock_entry) AS total, COUNT(*) AS linked FROM chain;

Anchor comparison, which detects full rewrites: for each stock_chain_anchor row, fetch the WORM object from S3 and assert its head_hash equals stock_entry.entry_hash at that head_entry_id. A rewrite cannot satisfy both the local chain and the historical anchors.

Transfer atomicity, since two legs are separate rows and nothing structural pairs them:

SELECT transfer_group_id, SUM(quantity_base)
FROM stock_entry WHERE transfer_group_id IS NOT NULL
GROUP BY transfer_group_id HAVING SUM(quantity_base) <> 0;

Every one of these checks must be proven to fail against a deliberately corrupted row before it is trusted.

Transaction discipline

The head-row lock is held from the ledger insert until commit, so it serialises all stock writes globally. At legacy's lifetime volume of 2,938 movements this is a non-issue — a 100-scan goods-in burst costs roughly 300ms — but two rules are non-negotiable:

Do all validation and reads before the insert, and let the balance upsert be the only statement after it. No network I/O inside the transaction, ever; an HTTP call there blocks every other stock write in the plant for its duration.

On retry, the uq_stock_entry_idempotency violation fires after the head lock is taken. The client must treat ER_DUP_ENTRY on that key as success, not as an error, or retries will manufacture phantom failures.

Recall query

Backward trace from a finished-goods lot:

WITH RECURSIVE back AS (
  SELECT g.input_entry_id, g.output_entry_id, g.quantity_base, 1 AS depth
  FROM stock_genealogy g JOIN stock_entry o ON o.id = g.output_entry_id
  WHERE o.lot_id = ?
  UNION ALL
  SELECT g.input_entry_id, g.output_entry_id, g.quantity_base, b.depth + 1
  FROM stock_genealogy g JOIN back b ON g.output_entry_id = b.input_entry_id
  WHERE b.depth < 32
)
SELECT DISTINCT l.id, l.product_id, l.trace_number, l.supplier_lot_code, b.depth
FROM back b JOIN stock_entry e ON e.id = b.input_entry_id JOIN stock_lot l ON l.id = e.lot_id;

Forward trace is the same CTE with input and output swapped. Mass balance per output entry compares SUM(g.quantity_base) of its inputs against its own quantity_base; the shortfall is yield loss. At this data volume both run in milliseconds, so the four-hour target has three orders of magnitude of headroom — the real risk to that target is missing genealogy edges, not query speed.

Costing, deferred

The ledger carries unit_cost and line_cost, populated on receipt only from the PO price, falling back to product_costing.unit_cost. Both are DECIMAL(16,6), matching product_costing rather than legacy's tighter DECIMAL(10,4). No rollup, no weighted-average layers, no labour absorption. A later Costing module consumes the ledger; quantity_base and the genealogy edges are the inputs it will need and both are captured from day one, so no re-migration.

Validation strategy

Replay-and-diff (available now). Restore the dump into a scratch schema, replay its 2,938 movements through the new write API in effective_at order, and gate on all 1,002 legacy tblstockcache.quantity values matching stock_balance.quantity exactly. Any mismatch is either a real legacy bug or a real new bug, and each must be classified explicitly rather than tolerated.

Shadow-run (blocked). Requires the live system's connection details, which are outstanding. Once available: mirror live traffic into the new ledger and diff balances continuously for an agreed soak period before cutover.

Scope and architecture

One Django app, stock, inside the nz-inventory service — consistent with react_erp_rebuild_0a452e56.plan.md making nz-inventory the sole writer of stock, while keeping the ledger, balance, trace and reservations in a single app so consistency is enforced by one transaction boundary rather than by network calls.

Delivery mirrors the recipe module: full DDL in this plan, applied to DB_LOCATIONS, with Django models and a stock/0001_stock_ledger migration using RunSQL plus reverse_sql for triggers and Meta.constraints for the CHECKs.

Open risks





Trigger creation is untested against these grants. ALL PRIVILEGES includes TRIGGER and ROW binlog does not restrict triggers, but confirm with one throwaway trigger before writing the migration.



Django does not manage triggers, so makemigrations will never detect drift in them. They need checked-in RunSQL with matching reverse_sql, and a startup assertion that all guards exist.



A direct SQL INSERT into stock_entry bypassing the service leaves stock_balance stale. The scheduled verifier catches it; the credential split makes it unlikely.



The S3 anchor interval defines the tamper-detection window. Anything written and rewritten between two anchors is undetectable, so the interval is a deliberate risk parameter, not an implementation detail.



stock_period rows must exist before any entry can be written, since period_id is NOT NULL. Seeding and monthly roll-forward need to be automated or the plant stops at midnight on the first of the month.

Diagrams

1. Entity relationships

Tables owned by the stock module, and the existing product, recipe and location tables they attach to.

erDiagram
    product ||--o{ stock_lot : "identifies"
    recipe_version ||--o{ stock_lot : "versions"
    product_purchase_shape_format ||--o{ stock_lot : "shape"
    stock_lot ||--o{ stock_lot_amendment : "amended by"
    stock_lot ||--o{ stock_entry : "moved by"
    loc_location ||--o{ stock_entry : "affects"
    stock_period ||--o{ stock_entry : "posted into"
    product_unit ||--o{ stock_entry : "measured in"
    stock_entry ||--o{ stock_genealogy : "as output"
    stock_entry ||--o{ stock_genealogy : "as input"
    stock_entry |o--o| stock_entry : "reverses"
    stock_lot ||--o{ stock_balance : "held as"
    loc_location ||--o{ stock_balance : "held at"
    stock_lot ||--o{ stock_reservation : "reserved from"
    stock_entry |o--o{ stock_reservation : "consumed by"
    stock_chain_head ||--o{ stock_chain_anchor : "published as"
    product_unit ||--o{ stock_unit_conversion : "converted by"
    product |o--o{ stock_unit_conversion : "overridden for"

    stock_lot {
        bigint id PK
        int product_id FK
        int recipe_version_id FK "nullable"
        int shape_format_id FK "nullable"
        varchar trace_number "part of identity"
        varchar supplier_lot_code "nullable"
        varchar origin "production purchase opening"
        date production_date
        date use_by "original value only"
        datetime created_at "no updated_at by design"
    }

    stock_lot_amendment {
        bigint id PK
        bigint lot_id FK
        varchar field_name "use_by production_date supplier_lot_code"
        varchar old_value
        varchar new_value
        varchar reason "required"
        int authorised_by_user_id "required"
        datetime effective_at
        datetime recorded_at
    }

    stock_entry {
        bigint id PK
        varchar idempotency_key UK "retry safety"
        varchar entry_type "nine types"
        bigint lot_id FK
        int location_id FK
        int counterparty_location_id FK "transfer other leg"
        char transfer_group_id "pairs the two legs"
        decimal quantity "signed plus in minus out"
        int unit_id FK
        decimal base_unit_factor "null for non mass"
        decimal quantity_base "kilograms"
        int period_id FK "close guard"
        datetime effective_at "business time"
        datetime recorded_at "system time"
        bigint reverses_entry_id FK "at most once"
        varchar override_reason "negative stock"
        int authorised_by_user_id "negative stock"
        decimal unit_cost "receipt only"
        decimal line_cost "receipt only"
        char prev_hash "inline chain"
        char entry_hash UK "SHA-256"
    }

    stock_genealogy {
        bigint id PK
        bigint output_entry_id FK
        bigint input_entry_id FK
        decimal quantity_base "kg of input into this output"
    }

    stock_balance {
        bigint lot_id PK
        int location_id PK
        decimal quantity "sum of all entries"
        decimal quantity_base
        bigint last_entry_id "watermark"
        bigint last_count_entry_id
        bigint negative_authorised_by_entry_id "gate"
        datetime updated_at
    }

    stock_reservation {
        bigint id PK
        bigint lot_id FK
        int location_id FK
        decimal quantity
        int unit_id FK
        varchar status "open consumed released expired"
        bigint consumed_by_entry_id FK
        datetime expires_at
    }

    stock_period {
        int id PK
        date period_start
        date period_end
        varchar status "open closed"
        datetime closed_at
    }

    stock_chain_head {
        tinyint id PK "always 1"
        bigint head_entry_id
        char head_hash
        bigint entry_count
    }

    stock_chain_anchor {
        bigint id PK
        bigint head_entry_id UK
        char head_hash
        bigint entry_count
        varchar s3_object_key "WORM evidence"
        varchar s3_version_id
        datetime anchored_at
    }

    stock_unit_conversion {
        bigint id PK
        int unit_id FK
        int product_id FK "null means global rule"
        decimal to_kg
        varchar source
    }

The stock_entry self-relationship for reverses is the one line to delete if the renderer is older than Mermaid 10.

2. Write path for a single movement

Everything before the insert is validation; everything after is the read model. The head lock is held from the insert to the commit, so nothing slow may sit between them.

sequenceDiagram
    autonumber
    participant UI as Scanner or UI
    participant SVC as nz-inventory stock app
    participant DB as MySQL DB_LOCATIONS
    participant TRG as stock_entry triggers

    UI->>SVC: POST movement with idempotency_key
    SVC->>DB: BEGIN
    SVC->>DB: resolve open stock_period for effective_at
    SVC->>DB: find or create stock_lot on identity tuple
    SVC->>DB: resolve to_kg from stock_unit_conversion
    Note over SVC: abort when no factor resolves and product is not is_downtime; never default to 1
    SVC->>DB: SELECT stock_balance FOR UPDATE
    Note over SVC: reject negative result unless override_reason and authoriser are present
    SVC->>DB: INSERT stock_entry
    DB->>TRG: BEFORE INSERT
    TRG->>DB: reject if period closed or effective_at in future
    TRG->>DB: SELECT head_hash FROM stock_chain_head FOR UPDATE
    TRG-->>DB: set prev_hash and entry_hash = SHA2 of payload
    DB->>TRG: AFTER INSERT
    TRG->>DB: advance stock_chain_head
    SVC->>DB: UPSERT stock_balance in the same transaction
    SVC->>DB: COMMIT
    SVC-->>UI: entry_id and entry_hash

On retry the duplicate idempotency_key fires at step 9, after the head lock is taken. The client must treat that as success.

3. Tamper-evidence layers

flowchart LR
    subgraph ledger [stock_entry append only]
      direction LR
      e1["entry 1 : prev NULL"] --> e2["entry 2 : prev is H1"] --> e3["entry 3 : prev is H2"] --> eN["entry N : prev is H of N minus 1"]
    end
    eN --> head["stock_chain_head : head_hash is HN"]
    head --> pub["anchor publisher on a fixed interval"]
    pub --> s3["S3 Object Lock in COMPLIANCE mode"]
    pub --> anch["stock_chain_anchor : local index only"]
    s3 --> verify["anchor verification job"]
    ledger --> chainck["chain continuity CTE"]
    chainck --> partial["detects a partial edit"]
    verify --> full["detects a full rewrite"]

The chain alone cannot detect a full rewrite, because whoever rewrites one entry can recompute every hash after it. Only the WORM anchors, which no database credential can alter, close that gap.

4. Production run, genealogy and yield loss

flowchart LR
    subgraph inputs [production_consumption entries]
      i1["Beef lot A : 120 kg out of Low Risk"]
      i2["Onion lot B : 30 kg out of Spice Room"]
      i3["Stock lot C : 50 kg out of Cooking"]
    end
    o1["production_output : Chilli base lot D 180 kg into High Risk"]
    i1 -->|"genealogy 120"| o1
    i2 -->|"genealogy 30"| o1
    i3 -->|"genealogy 50"| o1
    o1 --> mb["mass balance : 200 in versus 180 out"]
    mb --> yl["yield loss 20 kg"]

Yield loss is derived, never stored. It falls out of the genealogy edges, which is why the four entry types you originally proposed were insufficient: without separating consumption from issue, there is nothing to sum on the input side.

5. Recall in both directions

flowchart TB
    sup["Supplier lot X : receipt entry"]
    sup --> semiD["Semi finished lot D"]
    sup --> semiE["Semi finished lot E"]
    semiD --> finP["Finished lot P"]
    semiD --> finQ["Finished lot Q"]
    semiE --> finR["Finished lot R"]
    finP --> dispA["Dispatch to customer A"]
    finQ --> dispB["Dispatch to customer B"]
    finR --> dispC["Dispatch to customer C"]

Reading downward is the forward trace, answering which customers received the contaminated supplier lot. Reading upward from any finished lot is the backward trace, answering which supplier lots are in this pack. Legacy implemented only the downward direction; procSTKitemTraceBack was an empty stub.

6. Reservation lifecycle

stateDiagram-v2
    [*] --> open : reserved against a sales order or plan
    open --> consumed : issue entry posted
    open --> released : order cancelled
    open --> expired : expires_at passed
    consumed --> [*]
    released --> [*]
    expired --> [*]

Available to promise is stock_balance.quantity minus the sum of open reservations. This table is mutable on purpose; it is state, not ledger.

7. Legacy action codes mapped to entry types

flowchart LR
    subgraph legacy [tblstockmovementbatchactions]
      L1["STOCKIN"]
      L2["STOCKOUT"]
      L3["STOCKRECON absolute"]
      L4["STOCKTRANSFER and four BULKTRANSF codes"]
      L5["ITEMTRANSFER and ITEMTRANSFERRECIPE"]
    end
    subgraph modern [stock_entry entry_type]
      N1["receipt"]
      N2["issue"]
      N3["count_adjustment as a delta"]
      N4["transfer_out plus transfer_in"]
      N5["production_consumption plus production_output"]
      N6["disposal"]
      N7["reversal : new, replaces UPDATE"]
    end
    L1 --> N1
    L2 --> N2
    L3 --> N3
    L4 --> N4
    L4 --> N6
    L5 --> N5

Legacy's overloading of action with department codes such as PRODHIGHRISK and PRODCOOKING is dropped entirely; that information is location_id.