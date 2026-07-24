import os
from pathlib import Path
from dotenv import load_dotenv
import MySQLdb

load_dotenv(Path(".env"))
conn = MySQLdb.connect(
    host=os.getenv("DB_HOST"),
    port=int(os.getenv("DB_PORT") or 3306),
    user=os.getenv("DB_USER"),
    passwd=os.getenv("DB_PASSWORD"),
    db=os.getenv("DB_NAME"),
    connect_timeout=15,
)
cur = conn.cursor()

tables = [
    "tblcontainers",
    "tblcontainerschildcontainers",
    "tblcontainerscontacts",
    "tblcontainerscontactscontacts",
    "tblcontainerspostaladdress",
]

out = []
for t in tables:
    out.append("=" * 80)
    out.append(f"TABLE {t}")
    out.append("=" * 80)
    cur.execute(f"SHOW CREATE TABLE `{t}`")
    row = cur.fetchone()
    out.append(row[1])
    cur.execute(f"SELECT COUNT(*) FROM `{t}`")
    out.append(f"ROW_COUNT {cur.fetchone()[0]}")
    out.append("")
    out.append(f"COLUMNS {t}")
    cur.execute(f"SHOW FULL COLUMNS FROM `{t}`")
    for r in cur.fetchall():
        out.append(" | ".join("" if x is None else str(x) for x in r))
    out.append("")

out.append("=" * 80)
out.append("FOREIGN KEYS involving container tables")
out.append("=" * 80)
cur.execute(
    """
SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE()
  AND (
    TABLE_NAME IN ('tblcontainers','tblcontainerschildcontainers','tblcontainerscontacts','tblcontainerscontactscontacts','tblcontainerspostaladdress')
    OR REFERENCED_TABLE_NAME IN ('tblcontainers','tblcontainerschildcontainers','tblcontainerscontacts','tblcontainerscontactscontacts','tblcontainerspostaladdress')
  )
  AND REFERENCED_TABLE_NAME IS NOT NULL
ORDER BY TABLE_NAME, COLUMN_NAME
"""
)
fks = cur.fetchall()
out.append(f"FK_COUNT {len(fks)}")
for r in fks:
    out.append(" | ".join(str(x) for x in r))

out.append("")
out.append("=" * 80)
out.append("OTHER TABLES with Container columns")
out.append("=" * 80)
cur.execute(
    """
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND COLUMN_NAME LIKE '%Container%'
  AND TABLE_NAME NOT IN ('tblcontainers','tblcontainerschildcontainers','tblcontainerscontacts','tblcontainerscontactscontacts','tblcontainerspostaladdress')
ORDER BY TABLE_NAME, COLUMN_NAME
"""
)
for r in cur.fetchall():
    out.append(" | ".join(str(x) for x in r))

out.append("")
out.append("=" * 80)
out.append("TRIGGERS")
out.append("=" * 80)
cur.execute(
    """
SELECT TRIGGER_NAME, EVENT_MANIPULATION, EVENT_OBJECT_TABLE, ACTION_TIMING, ACTION_STATEMENT
FROM information_schema.TRIGGERS
WHERE TRIGGER_SCHEMA = DATABASE()
  AND EVENT_OBJECT_TABLE IN (
    'tblcontainers','tblcontainerschildcontainers','tblcontainerscontacts',
    'tblcontainerscontactscontacts','tblcontainerspostaladdress'
  )
"""
)
for r in cur.fetchall():
    out.append("-" * 40)
    out.append(f"{r[0]} {r[3]} {r[1]} ON {r[2]}")
    out.append(r[4] or "")

out.append("")
out.append("=" * 80)
out.append("CORE ROUTINES")
out.append("=" * 80)
routines = [
    ("FUNCTION", "fnGetContainerName"),
    ("FUNCTION", "fnGetContainerRealStock"),
    ("FUNCTION", "fnGetContainerTopContainer"),
    ("FUNCTION", "fnContainerExtendUseBy"),
    ("FUNCTION", "fnGetProductionIdentifierForContainer"),
    ("FUNCTION", "fnGetStockIdentifierForContainer"),
]
for kind, name in routines:
    try:
        if kind == "FUNCTION":
            cur.execute(f"SHOW CREATE FUNCTION `{name}`")
        else:
            cur.execute(f"SHOW CREATE PROCEDURE `{name}`")
        row = cur.fetchone()
        out.append("-" * 40)
        out.append(row[2] if len(row) > 2 else str(row))
    except Exception as e:
        out.append(f"{name} ERR {e}")

# sample data shape / distinct values for key enum-like columns
out.append("")
out.append("=" * 80)
out.append("DATA PROFILE tblcontainers")
out.append("=" * 80)
cur.execute("SHOW COLUMNS FROM tblcontainers")
cols = [r[0] for r in cur.fetchall()]
out.append("COL_LIST " + ", ".join(cols))
# try common identity fields
for col in cols:
    cl = col.lower()
    if any(x in cl for x in ["type", "flag", "real", "stock", "active", "status", "parent", "child", "kind"]):
        try:
            cur.execute(f"SELECT `{col}`, COUNT(*) c FROM tblcontainers GROUP BY `{col}` ORDER BY c DESC LIMIT 20")
            out.append(f"DIST {col}: " + "; ".join(f"{a}={b}" for a,b in cur.fetchall()))
        except Exception as e:
            out.append(f"DIST {col} ERR {e}")

# child containers sample
out.append("")
out.append("=" * 80)
out.append("DATA PROFILE child/contacts/address")
out.append("=" * 80)
for t in tables[1:]:
    cur.execute(f"SHOW COLUMNS FROM `{t}`")
    ccols = [r[0] for r in cur.fetchall()]
    out.append(f"{t} COLS: " + ", ".join(ccols))
    cur.execute(f"SELECT * FROM `{t}` LIMIT 3")
    rows = cur.fetchall()
    for r in rows:
        out.append(str(r))

Path("_db_dump_containers.txt").write_text("\n".join(out), encoding="utf-8")
print("WROTE", Path("_db_dump_containers.txt").stat().st_size, "bytes", "lines", len(out))
cur.close(); conn.close()
