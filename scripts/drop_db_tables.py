#!/usr/bin/env python
"""
Drop Django auth/session tables only. Keeps loc_* and django_migrations.

Usage:
  python scripts/drop_db_tables.py              # dry-run (prints SQL only)
  python scripts/drop_db_tables.py --execute    # run drops
"""

import argparse
import os
import sys
from pathlib import Path

import MySQLdb
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')

TABLES = [
    'auth_user_user_permissions',
    'auth_user_groups',
    'auth_group_permissions',
    'django_session',
    'auth_user',
    'auth_group',
    'auth_permission',
    'django_content_type',
]


def main():
    parser = argparse.ArgumentParser(description='Drop unused Django/auth/loc tables')
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Actually run DROP (default is dry-run)',
    )
    args = parser.parse_args()

    db_name = os.getenv('DB_NAME')
    host = os.getenv('DB_HOST')
    print(f'Database: {db_name} @ {host}')
    print(f'Tables ({len(TABLES)}):')
    for name in TABLES:
        print(f'  - {name}')

    if not args.execute:
        print('\nDry-run only. Re-run with --execute to drop.')
        return 0

    conn = MySQLdb.connect(
        host=host,
        port=int(os.getenv('DB_PORT') or 3306),
        user=os.getenv('DB_USER'),
        passwd=os.getenv('DB_PASSWORD'),
        db=db_name,
        connect_timeout=30,
    )
    cur = conn.cursor()
    try:
        cur.execute('SET FOREIGN_KEY_CHECKS = 0')
        for table in TABLES:
            cur.execute(f'DROP TABLE IF EXISTS `{table}`')
            print(f'Dropped (if existed): {table}')
        cur.execute('SET FOREIGN_KEY_CHECKS = 1')
        conn.commit()
    finally:
        cur.close()
        conn.close()

    print('Done.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
