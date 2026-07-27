import os
from pathlib import Path
from dotenv import load_dotenv
import MySQLdb

load_dotenv(Path(".env"))
host = os.getenv("DB_HOST")
db = os.getenv("DB_NAME")
print(f"Connecting {db} @ {host}...")
conn = MySQLdb.connect(
    host=host,
    port=int(os.getenv("DB_PORT") or 3306),
    user=os.getenv("DB_USER"),
    passwd=os.getenv("DB_PASSWORD"),
    db=db,
    connect_timeout=15,
)
cur = conn.cursor()

cur.execute("SHOW TABLES")
tables = sorted(r[0] for r in cur.fetchall())
print(f"\nTABLES ({len(tables)}):")
for t in tables:
    print(f"  {t}")

expected_loc = [
    "loc_location", "loc_location_role", "loc_location_feature",
    "loc_stock_profile", "loc_location_edge", "loc_address", "loc_contact",
]
auth_tables = [
    "auth_user", "auth_group", "auth_permission", "django_session", "django_content_type",
]

print("\n--- loc_* ---")
for t in expected_loc:
    if t in tables:
        cur.execute(f"SELECT COUNT(*) FROM `{t}`")
        print(f"  OK {t}: {cur.fetchone()[0]} rows")
    else:
        print(f"  MISSING {t}")

print("\n--- auth/session (should be absent) ---")
for t in auth_tables:
    print(f"  {'STILL EXISTS' if t in tables else 'gone'}: {t}")

print("\n--- django_migrations ---")
if "django_migrations" in tables:
    cur.execute("SELECT app, name, applied FROM django_migrations ORDER BY applied")
    for r in cur.fetchall():
        print(f"  {r[0]} | {r[1]} | {r[2]}")
else:
    print("  MISSING django_migrations")

print("\n--- loc_location_edge schema ---")
if "loc_location_edge" in tables:
    cur.execute("SHOW COLUMNS FROM loc_location_edge")
    cols = [r[0] for r in cur.fetchall()]
    print("  columns:", ", ".join(cols))
    has_rt = "relation_type" in cols
    print("  relation_type:", "OK" if has_rt else "MISSING (run migrate 0002)")
    if has_rt:
        cur.execute("SELECT relation_type, parent_id, child_id FROM loc_location_edge LIMIT 10")
        for r in cur.fetchall():
            print(f"    {r}")

print("\n--- Django check ---")
conn.close()
