import os
from pathlib import Path
from dotenv import load_dotenv
import MySQLdb
load_dotenv(Path(".env"))
conn = MySQLdb.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT") or 3306), user=os.getenv("DB_USER"), passwd=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"))
cur = conn.cursor()
cur.execute("SHOW TABLES LIKE 'loc_%'")
print("TABLES:", [r[0] for r in cur.fetchall()])
for t in ["loc_location","loc_location_role","loc_location_feature","loc_stock_profile","loc_location_edge","loc_address","loc_contact"]:
    cur.execute(f"SELECT COUNT(*) FROM `{t}`")
    print(t, "rows", cur.fetchone()[0])
cur.close(); conn.close()
