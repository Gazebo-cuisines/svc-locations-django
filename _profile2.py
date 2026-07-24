import os
from pathlib import Path
from dotenv import load_dotenv
import MySQLdb

load_dotenv(Path(".env"))
conn = MySQLdb.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT") or 3306),
    user=os.getenv("DB_USER"), passwd=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"), connect_timeout=15)
cur = conn.cursor()

flags = ["internal","process","supplier","customer","customerOrders","customerForecasts","courier","depot","storage","transform","productionplanshort","pickinglist","breakdownlist","linearplan","issuereceipies","staffbudget","anomalyReportStkIn","planClosingReport","extendsComponentUseBy","containerVisible","containerStatic","locked"]
print("FLAG COUNTS (nonzero):")
for f in flags:
    cur.execute(f"SELECT COUNT(*) FROM tblcontainers WHERE `{f}` IS NOT NULL AND `{f}` <> 0")
    n = cur.fetchone()[0]
    if n:
        print(f"  {f}: {n}")

print("\nROLE COMBOS (top flags):")
cur.execute("""
SELECT internal, process, supplier, customer, courier, depot, storage, transform, COUNT(*) c
FROM tblcontainers
GROUP BY 1,2,3,4,5,6,7,8
ORDER BY c DESC
""")
for r in cur.fetchall():
    print(r)

print("\nTOPCONTAINER vs CHILD GRAPH:")
cur.execute("SELECT COUNT(*), COUNT(DISTINCT topcontainer), SUM(topcontainer IS NULL) FROM tblcontainers")
print("containers", cur.fetchone())
cur.execute("SELECT id, container, topcontainer FROM tblcontainers WHERE topcontainer IS NOT NULL AND topcontainer <> id LIMIT 20")
print("self-top samples:")
for r in cur.fetchall(): print(r)
cur.execute("SELECT COUNT(*) FROM tblcontainers WHERE topcontainer = id OR topcontainer IS NULL")
print("roots/self", cur.fetchone())
cur.execute("SELECT parentContainer, childContainer FROM tblcontainerschildcontainers")
print("edges:")
for r in cur.fetchall(): print(r)

print("\nHARD FKs INTO tblcontainers:")
cur.execute("""
SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME='tblcontainers'
""")
for r in cur.fetchall(): print(r)

print("\nproducts src/dest distinct:")
cur.execute("SELECT COUNT(DISTINCT srccontainer), COUNT(DISTINCT destcontainer) FROM tblproducts")
print(cur.fetchone())
cur.execute("SELECT COUNT(DISTINCT srccontainer), COUNT(DISTINCT destcontainer) FROM tblstockmovement")
print("stockmovement", cur.fetchone())

cur.close(); conn.close()
