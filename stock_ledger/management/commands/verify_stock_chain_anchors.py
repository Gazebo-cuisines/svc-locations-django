import json
import sys

from django.core.management.base import BaseCommand

from stock_ledger.util.anchor import verify_chain_anchors


class Command(BaseCommand):
    help = 'Verify stock_chain_anchor rows against S3 JSON and ledger hashes.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true')
        parser.add_argument('--limit', type=int, default=None)

    def handle(self, *args, **options):
        report = verify_chain_anchors(limit=options['limit'])
        if options['json']:
            self.stdout.write(json.dumps(report, default=str, separators=(',', ':')))
        elif report['ok']:
            self.stdout.write(self.style.SUCCESS(
                f"anchors ok ({report['checked']} checked)"
            ))
        else:
            self.stderr.write(self.style.ERROR(
                f"anchor mismatches: {report['mismatch_count']}"
            ))
            for row in report['mismatches']:
                self.stderr.write(json.dumps(row, default=str, separators=(',', ':')))
        if not report['ok']:
            sys.exit(1)
