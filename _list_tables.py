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
cur.execute("SHOW TABLES LIKE '%ontainer%'")
print("TABLES:")
for r in cur.fetchall():
    print(r[0])
cur.execute("SELECT @@lower_case_table_names, @@version, DATABASE()")
print("META", cur.fetchone())
cur.close(); conn.close()
