from django.db import migrations


ENTRY_BI = """
CREATE TRIGGER stock_entry_bi BEFORE INSERT ON stock_entry FOR EACH ROW
BEGIN
  DECLARE v_prev CHAR(64);

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
"""

ENTRY_BI_REVERSE = """
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
"""


def _swap_entry_bi(schema_editor, sql):
    if schema_editor.connection.vendor != 'mysql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('DROP TRIGGER IF EXISTS stock_entry_bi')
        cursor.execute(sql)


def apply_entry_bi(apps, schema_editor):
    _swap_entry_bi(schema_editor, ENTRY_BI)


def revert_entry_bi(apps, schema_editor):
    _swap_entry_bi(schema_editor, ENTRY_BI_REVERSE)


class Migration(migrations.Migration):

    dependencies = [
        ('stock_ledger', '0015_lot_product_supplier'),
    ]

    operations = [
        migrations.RunPython(apply_entry_bi, revert_entry_bi),
        migrations.RemoveField(
            model_name='stockentry',
            name='period',
        ),
        migrations.DeleteModel(
            name='StockPeriod',
        ),
    ]
