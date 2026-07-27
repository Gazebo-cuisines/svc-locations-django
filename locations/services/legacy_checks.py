import os

import MySQLdb
from django.core.exceptions import ValidationError


def _legacy_connection():
    database = os.getenv('LEGACY_DB_NAME', 'production')
    return MySQLdb.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT') or 3306),
        user=os.getenv('DB_USER'),
        passwd=os.getenv('DB_PASSWORD'),
        db=database,
        connect_timeout=15,
    )


def location_has_product_references(location_id: int) -> bool:
    conn = _legacy_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM tblproducts
            WHERE srccontainer = %s OR destcontainer = %s
            """,
            (location_id, location_id),
        )
        return cur.fetchone()[0] > 0
    finally:
        conn.close()


def location_has_stock_movement_references(location_id: int) -> bool:
    conn = _legacy_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM tblstockmovement
            WHERE srccontainer = %s OR destcontainer = %s
            """,
            (location_id, location_id),
        )
        return cur.fetchone()[0] > 0
    finally:
        conn.close()


def assert_can_change_identity(location_id: int) -> None:
    if location_has_product_references(location_id):
        raise ValidationError(
            'Location cannot be renamed or change external code: products reference this location.'
        )
    if location_has_stock_movement_references(location_id):
        raise ValidationError(
            'Location cannot be renamed or change external code: stock movements reference this location.'
        )


def assert_can_delete_location(location_id: int) -> None:
    if location_has_product_references(location_id):
        raise ValidationError(
            'Location cannot be deleted: products reference this location.'
        )
    if location_has_stock_movement_references(location_id):
        raise ValidationError(
            'Location cannot be deleted: stock movements reference this location.'
        )
