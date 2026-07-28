import os
from dotenv import load_dotenv
import MySQLdb

load_dotenv()
conn = MySQLdb.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    passwd=os.getenv("DB_PASSWORD"),
    db=os.getenv("DB_NAME"),
    port=int(os.getenv("DB_PORT")),
)
cur = conn.cursor()


def run(sql: str) -> None:
    print("SQL:", sql.replace("\n", " "))
    cur.execute(sql)
    if cur.description:
        print("ROWS:", cur.fetchall())
    else:
        print("OK, affected:", cur.rowcount)


try:
    cur.execute("SHOW VARIABLES LIKE 'binlog_format'")
    print("BINLOG:", cur.fetchall())
    cur.execute("SHOW GRANTS")
    print("GRANTS:", cur.fetchall())

    run("DROP TRIGGER IF EXISTS _trigger_probe_tmp_bu")
    run("DROP TABLE IF EXISTS _trigger_probe_tmp")
    run(
        """
        CREATE TABLE _trigger_probe_tmp (
          id INT PRIMARY KEY AUTO_INCREMENT,
          note VARCHAR(32),
          touched_at TIMESTAMP NULL
        )
        """
    )
    run(
        """
        CREATE TRIGGER _trigger_probe_tmp_bu
        BEFORE UPDATE ON _trigger_probe_tmp
        FOR EACH ROW
        SET NEW.touched_at = NOW()
        """
    )
    run("INSERT INTO _trigger_probe_tmp (note) VALUES ('a')")
    run("UPDATE _trigger_probe_tmp SET note = 'b' WHERE id = 1")
    run("SELECT id, note, touched_at FROM _trigger_probe_tmp WHERE id = 1")
    run("DROP TRIGGER IF EXISTS _trigger_probe_tmp_bu")
    run("DROP TABLE IF EXISTS _trigger_probe_tmp")
    conn.commit()
    print("PROBE_RESULT=PASS")
except Exception as e:
    conn.rollback()
    print("PROBE_RESULT=FAIL")
    print(type(e).__name__ + ":", e)
finally:
    cur.close()
    conn.close()
