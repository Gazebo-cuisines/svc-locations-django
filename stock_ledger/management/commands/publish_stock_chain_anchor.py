import json

from django.core.management.base import BaseCommand

from stock_ledger.util.anchor import publish_chain_anchor


class Command(BaseCommand):
    help = 'Publish stock_chain_head JSON anchor to S3 (AUDIT_S3_BUCKET).'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        anchor = publish_chain_anchor()
        if anchor is None:
            self.stdout.write('nothing to publish (empty head)')
            return
        payload = {
            'anchor_id': anchor.id,
            'head_entry_id': anchor.head_entry_id,
            'head_hash': anchor.head_hash,
            'entry_count': anchor.entry_count,
            's3_object_key': anchor.s3_object_key,
            's3_version_id': anchor.s3_version_id,
        }
        if options['json']:
            self.stdout.write(json.dumps(payload, separators=(',', ':')))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'anchored entry={anchor.head_entry_id} key={anchor.s3_object_key}'
            ))
