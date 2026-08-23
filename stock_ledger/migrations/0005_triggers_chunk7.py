from django.db import migrations


TRIGGERS = [
    """
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
""",
    """
CREATE TRIGGER stock_entry_ai AFTER INSERT ON stock_entry FOR EACH ROW
BEGIN
  UPDATE stock_chain_head
  SET head_entry_id = NEW.id,
      head_hash = NEW.entry_hash,
      entry_count = entry_count + 1,
      updated_at = NOW(6)
  WHERE id = 1;
END
""",
    """
CREATE TRIGGER stock_entry_bu BEFORE UPDATE ON stock_entry FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_entry: updates forbidden';
END
""",
    """
CREATE TRIGGER stock_entry_bd BEFORE DELETE ON stock_entry FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_entry: deletes forbidden';
END
""",
    """
CREATE TRIGGER stock_genealogy_bu BEFORE UPDATE ON stock_genealogy FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_genealogy: updates forbidden';
END
""",
    """
CREATE TRIGGER stock_genealogy_bd BEFORE DELETE ON stock_genealogy FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_genealogy: deletes forbidden';
END
""",
    """
CREATE TRIGGER stock_lot_amendment_bu BEFORE UPDATE ON stock_lot_amendment FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_lot_amendment: updates forbidden';
END
""",
    """
CREATE TRIGGER stock_lot_amendment_bd BEFORE DELETE ON stock_lot_amendment FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_lot_amendment: deletes forbidden';
END
""",
    """
CREATE TRIGGER stock_balance_bu BEFORE UPDATE ON stock_balance FOR EACH ROW
BEGIN
  IF NEW.quantity < 0 AND NEW.negative_authorised_by_entry_id IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'stock_balance: negative without authorised override';
  END IF;
END
""",
]

DROP_TRIGGERS = [
    'DROP TRIGGER IF EXISTS stock_entry_bi',
    'DROP TRIGGER IF EXISTS stock_entry_ai',
    'DROP TRIGGER IF EXISTS stock_entry_bu',
    'DROP TRIGGER IF EXISTS stock_entry_bd',
    'DROP TRIGGER IF EXISTS stock_genealogy_bu',
    'DROP TRIGGER IF EXISTS stock_genealogy_bd',
    'DROP TRIGGER IF EXISTS stock_lot_amendment_bu',
    'DROP TRIGGER IF EXISTS stock_lot_amendment_bd',
    'DROP TRIGGER IF EXISTS stock_balance_bu',
]


def apply_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    with schema_editor.connection.cursor() as cursor:
        for drop in DROP_TRIGGERS:
            cursor.execute(drop)
        for sql in TRIGGERS:
            cursor.execute(sql)


def drop_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    with schema_editor.connection.cursor() as cursor:
        for drop in DROP_TRIGGERS:
            cursor.execute(drop)


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0004_satellite_tables_chunk6'),
    ]

    operations = [
        migrations.RunPython(apply_triggers, drop_triggers),
    ]
