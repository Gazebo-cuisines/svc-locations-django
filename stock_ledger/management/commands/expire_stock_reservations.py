from django.core.management.base import BaseCommand

from stock_ledger.util.reservations import expire_due


class Command(BaseCommand):
    help = 'Expire open stock reservations past expires_at.'

    def handle(self, *args, **options):
        count = expire_due()
        self.stdout.write(self.style.SUCCESS(f'expired {count} reservation(s)'))
