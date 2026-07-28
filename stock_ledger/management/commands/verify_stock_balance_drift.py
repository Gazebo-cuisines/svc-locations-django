import json
import logging
import sys

from django.core.management.base import BaseCommand

from stock_ledger.util.balance import find_balance_drift

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Verify stock_balance against SUM(stock_entry.quantity). '
        'Exits 1 if drift is found (alarm only — does not repair).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--json',
            action='store_true',
            help='Print drift rows as JSON (Athena-friendly line or array).',
        )

    def handle(self, *args, **options):
        drifts = find_balance_drift()
        payload = {
            'event': 'stock_balance_drift_check',
            'drift_count': len(drifts),
            'drifts': drifts,
        }
        if options['json']:
            self.stdout.write(json.dumps(payload, separators=(',', ':')))
        elif drifts:
            self.stderr.write(self.style.ERROR(
                f'stock_balance drift: {len(drifts)} row(s)'
            ))
            for row in drifts:
                self.stderr.write(json.dumps(row, separators=(',', ':')))
        else:
            self.stdout.write(self.style.SUCCESS('stock_balance: no drift'))

        if drifts:
            logger.error('stock_balance_drift', extra=payload)
            sys.exit(1)
