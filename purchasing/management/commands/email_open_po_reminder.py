from datetime import date

from django.core.management.base import BaseCommand, CommandError

from purchasing.services.open_po_email import send_open_po_reminder
from stock_ledger.util.ses_mail import SesMailError


class Command(BaseCommand):
    help = (
        'Email Ordered + Partial POs (newest first) to open-PO recipients via AWS SES. '
        'Cron: 10 0 * * * (00:10).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--as-of',
            dest='as_of',
            default=None,
            help='Report date YYYY-MM-DD (default: today, app timezone).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Build report and list recipients without calling SES.',
        )

    def handle(self, *args, **options):
        as_of = options.get('as_of')
        day = None
        if as_of:
            try:
                day = date.fromisoformat(as_of)
            except ValueError as exc:
                raise CommandError('Invalid --as-of. Use YYYY-MM-DD.') from exc

        try:
            result = send_open_po_reminder(
                as_of=day,
                dry_run=bool(options.get('dry_run')),
            )
        except SesMailError as exc:
            raise CommandError(str(exc)) from exc

        if result['skipped']:
            self.stdout.write(
                self.style.WARNING(
                    f"as_of={result['as_of']} rows={result['row_count']} "
                    'no active recipients — skipped SES'
                )
            )
            return

        mode = 'dry-run' if options.get('dry_run') else 'sent'
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} as_of={result['as_of']} rows={result['row_count']} "
                f"recipients={len(result['recipients'])} "
                f"message_id={result['message_id'] or '-'}"
            )
        )
