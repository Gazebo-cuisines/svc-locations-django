import os
from pathlib import Path
from dotenv import load_dotenv
import MySQLdb
load_dotenv(Path(".env"))
print("DB", os.getenv("DB_NAME"))
conn = MySQLdb.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT") or 3306), user=os.getenv("DB_USER"), passwd=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"))
cur = conn.cursor()
cur.execute("SHOW TABLES")
print([r[0] for r in cur.fetchall()])
conn.close()
# also check production_dev still has legacy
conn2 = MySQLdb.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT") or 3306), user=os.getenv("DB_USER"), passwd=os.getenv("DB_PASSWORD"), db="production_dev")
cur2 = conn2.cursor()
cur2.execute("SELECT id, container, topcontainer, supplier, storage, internal FROM tblcontainers WHERE id IN (168,23,24,13,3)")
print("LEGACY SAMPLES:")
for r in cur2.fetchall(): print(r)
cur2.execute("SELECT parentContainer, childContainer FROM tblcontainerschildcontainers WHERE childContainer IN (23,24)")
print("EDGES:", cur2.fetchall())
cur2.execute("SELECT container, name, contactpointname, LEFT(address,80) FROM tblcontainerspostaladdress WHERE container=13 LIMIT 2")
print("ADDR:", cur2.fetchall())
conn2.close()
