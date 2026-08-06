"""
Local test settings: in-memory SQLite so tests do not need the shared MySQL box.

Migrations are skipped and tables are built straight from the models, because
several migrations are raw MySQL DDL. The ledger's hash chain and its
period-closed / immutability guards live in MySQL triggers, so those are not
exercised here; run against MySQL to cover them.
"""

from core.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}


class _NoMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = _NoMigrations()
