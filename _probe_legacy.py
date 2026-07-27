import os
from pathlib import Path
from dotenv import load_dotenv
import MySQLdb

load_dotenv(Path(".env"))
host = os.getenv("DB_HOST")
user = os.getenv("DB_USER")
passwd = os.getenv("DB_PASSWORD")
port = int(os.getenv("DB_PORT") or 3306)

def probe(db):
    try:
        conn = MySQLdb.connect(host=host, port=port, user=user, passwd=passwd, db=db, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM tblcontainers")
        n = cur.fetchone()[0]
        cur.execute("SHOW DATABASES")
        dbs = [r[0] for r in cur.fetchall() if not r[0] in ("information_schema","performance_schema","mysql","sys")]
        conn.close()
        return f"OK tblcontainers={n} dbs_sample={dbs[:15]}"
    except Exception as e:
        return f"ERR {e}"

print("host", host)
for db in ["DB_LOCATIONS", "production", "production_dev"]:
    print(f"  {db}: {probe(db)}")
