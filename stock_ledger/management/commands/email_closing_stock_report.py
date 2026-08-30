from datetime import date

from django.core.management.base import BaseCommand, CommandError

from stock_ledger.util.closing_stock_email import send_closing_stock_report
from stock_ledger.util.ses_mail import SesMailError


class Command(BaseCommand):
    help = 'Email yesterday\'s closing stock CSV to active report recipients via AWS SES.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--as-of',
            dest='as_of',
            default=None,
            help='Closing date YYYY-MM-DD (default: yesterday, app timezone).',
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
            result = send_closing_stock_report(
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
