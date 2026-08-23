"""Copy gazebo_locations rows MySQL -> Postgres. Does not touch MySQL. Skips django_migrations."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pymysql
from psycopg import sql
from psycopg.types.json import Jsonb

SKIP = {'django_migrations'}
ROOT = Path(__file__).resolve().parent.parent


def _env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / '.env').read_text().splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out[k] = v
    return out


def _coerce(val, pg_type: str):
    if val is None:
        return None
    if pg_type in ('json', 'jsonb'):
        if isinstance(val, (bytes, bytearray)):
            val = val.decode()
        if isinstance(val, str):
            val = json.loads(val) if val else None
        return Jsonb(val) if val is not None else None
    if pg_type == 'boolean':
        return bool(val)
    return val


def main() -> None:
    env = _env()
    mysql = pymysql.connect(
        host=env['DB_HOST'],
        port=int(env['DB_PORT']),
        user=env['DB_USER'],
        password=env['DB_PASSWORD'],
        database=env['DB_NAME'],
        charset='utf8mb4',
    )
    pg = psycopg.connect(
        host=env['PG_DB_HOST'],
        port=int(env['PG_DB_PORT']),
        dbname=env['PG_DB_NAME'],
        user=env['PG_DB_USER'],
        password=env['PG_DB_PASSWORD'],
    )
    mcur = mysql.cursor()
    pcur = pg.cursor()

    mcur.execute(
        'SELECT table_name FROM information_schema.tables '
        'WHERE table_schema=%s AND table_type=%s ORDER BY table_name',
        (env['DB_NAME'], 'BASE TABLE'),
    )
    mysql_tables = [r[0] for r in mcur.fetchall()]
    pcur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    pg_tables = {r[0] for r in pcur.fetchall()}
    targets = [t for t in mysql_tables if t not in SKIP and t in pg_tables]

    pcur.execute('SET session_replication_role = replica')
    pcur.execute(
        sql.SQL('TRUNCATE TABLE {} RESTART IDENTITY CASCADE').format(
            sql.SQL(', ').join(sql.Identifier(t) for t in targets)
        )
    )

    copied = 0
    for table in targets:
        pcur.execute(
            'SELECT column_name, data_type FROM information_schema.columns '
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
            (table,),
        )
        pg_cols = pcur.fetchall()
        col_names = [c[0] for c in pg_cols]
        types = {c[0]: c[1] for c in pg_cols}
        mcur.execute(f'SELECT * FROM `{table}`')
        mysql_cols = [d[0] for d in mcur.description]
        idx = {name: i for i, name in enumerate(mysql_cols)}
        rows = mcur.fetchall()
        if not rows:
            print(f'  0  {table}')
            continue
        insert = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
            sql.Identifier(table),
            sql.SQL(', ').join(sql.Identifier(c) for c in col_names),
            sql.SQL(', ').join(sql.Placeholder() * len(col_names)),
        )
        batch = [
            [_coerce(row[idx[c]] if c in idx else None, types[c]) for c in col_names]
            for row in rows
        ]
        pcur.executemany(insert, batch)
        copied += len(batch)
        print(f'{len(batch):6d}  {table}')

    pcur.execute(
        """
        SELECT c.relname, a.attname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND pg_get_serial_sequence(c.relname, a.attname) IS NOT NULL
        """
    )
    for table, column in pcur.fetchall():
        pcur.execute(
            sql.SQL(
                'SELECT setval(pg_get_serial_sequence(%s, %s), '
                'COALESCE((SELECT MAX({}) FROM {}), 1), true)'
            ).format(sql.Identifier(column), sql.Identifier(table)),
            (table, column),
        )

    pcur.execute('SET session_replication_role = DEFAULT')
    pg.commit()
    mysql.close()
    pg.close()
    print('COPIED_ROWS', copied)


if __name__ == '__main__':
    main()
