import os
import time
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
)
cur = conn.cursor()

def timed(label, sql, params=None, n=50):
    # warm
    cur.execute(sql, params or ())
    cur.fetchall()
    t0 = time.perf_counter()
    for _ in range(n):
        cur.execute(sql, params or ())
        cur.fetchall()
    ms = (time.perf_counter() - t0) * 1000 / n
    cur.execute("EXPLAIN " + sql, params or ())
    plan = cur.fetchall()
    print(f"\n=== {label} ===")
    print(f"avg {ms:.3f} ms over {n} runs")
    print("EXPLAIN:", plan)
    cur.execute(sql, params or ())
    rows = cur.fetchall()
    print("sample rows:", rows[:5], f"(count={len(rows)})")

# Q1: legacy-equivalent fnGetContainerName
timed(
    "Q1 name by id (fnGetContainerName)",
    "SELECT id, name FROM loc_location WHERE id=%s",
    (23,),
)

# Q2: real_stock (fnGetContainerRealStock)
timed(
    "Q2 real_stock by id (fnGetContainerRealStock)",
    "SELECT location_id, real_stock, stock_identifier, production_identifier FROM loc_stock_profile WHERE location_id=%s",
    (23,),
)

# Q3: top/parent (fnGetContainerTopContainer) via edge
timed(
    "Q3 parent via edge (fnGetContainerTopContainer)",
    "SELECT parent_id FROM loc_location_edge WHERE child_id=%s",
    (23,),
)

# Q4: full txn payload join (typical stock movement needs)
timed(
    "Q4 txn payload join (location+stock+parent)",
    """
    SELECT
      l.id,
      l.name,
      l.external_code,
      l.visible,
      l.locked,
      sp.real_stock,
      sp.stock_identifier,
      sp.production_identifier,
      sp.extends_component_use_by,
      e.parent_id
    FROM loc_location l
    LEFT JOIN loc_stock_profile sp ON sp.location_id = l.id
    LEFT JOIN loc_location_edge e ON e.child_id = l.id
    WHERE l.id = %s
    """,
    (23,),
)

# Q5: src+dest pair in one roundtrip (common on stock txn)
timed(
    "Q5 src+dest pair one query",
    """
    SELECT
      l.id,
      l.name,
      sp.real_stock,
      sp.stock_identifier,
      e.parent_id
    FROM loc_location l
    LEFT JOIN loc_stock_profile sp ON sp.location_id = l.id
    LEFT JOIN loc_location_edge e ON e.child_id = l.id
    WHERE l.id IN (%s, %s)
    """,
    (23, 13),
)

# Q6: roles for authorization-ish checks
timed(
    "Q6 roles for location",
    "SELECT role FROM loc_location_role WHERE location_id=%s",
    (13,),
)

# Q7: children of parent (less common per txn, more UI)
timed(
    "Q7 children of parent",
    "SELECT child_id FROM loc_location_edge WHERE parent_id=%s",
    (168,),
)

print("\n=== INDEXES ===")
for t in [
    "loc_location",
    "loc_stock_profile",
    "loc_location_edge",
    "loc_location_role",
    "loc_location_feature",
    "loc_address",
    "loc_contact",
]:
    cur.execute(f"SHOW INDEX FROM `{t}`")
    idxs = [(r[2], r[4], r[5]) for r in cur.fetchall()]  # Key_name, Column, Seq
    print(t, idxs)

print("\n=== ROW COUNTS ===")
for t in ["loc_location","loc_stock_profile","loc_location_edge","loc_location_role","loc_location_feature","loc_address","loc_contact"]:
    cur.execute(f"SELECT COUNT(*) FROM `{t}`")
    print(t, cur.fetchone()[0])

cur.close(); conn.close()
