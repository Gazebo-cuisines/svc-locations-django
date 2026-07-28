import json
import sys

from django.core.management.base import BaseCommand

from stock_ledger.util.verify import run_all_verifications


class Command(BaseCommand):
    help = (
        'Run stock ledger verification suite '
        '(chain, balance, transfer, reservations). Exit 1 on failure.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        report = run_all_verifications()
        if options['json']:
            self.stdout.write(json.dumps(report, default=str, separators=(',', ':')))
        else:
            for row in report['results']:
                status = 'OK' if row['ok'] else 'FAIL'
                self.stdout.write(f"{row['check']}: {status}")
            self.stdout.write(
                self.style.SUCCESS('all ok')
                if report['ok']
                else self.style.ERROR('verification failed')
            )
        if not report['ok']:
            sys.exit(1)
