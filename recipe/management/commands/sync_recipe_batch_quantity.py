from django.core.management.base import BaseCommand

from recipe.utils import sync_active_bom_batch_quantities


class Command(BaseCommand):
    help = (
        'Set batch_quantity from ingredient totals on every active '
        'mix/spice/cook/steam recipe version.'
    )

    def handle(self, *args, **options):
        updated = sync_active_bom_batch_quantities()
        self.stdout.write(self.style.SUCCESS(
            f'{updated} active version(s) updated',
        ))
