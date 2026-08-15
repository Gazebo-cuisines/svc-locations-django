from django.core.management.base import BaseCommand
from django.test import RequestFactory
from django.utils import timezone

from product.audit_log import capture_product_audit
from recipe.models import RecipeVersion, RecipeVersionStatus
from recipe.utils import activate_version


class Command(BaseCommand):
    help = 'Activate approved recipe versions whose effective_from is today or earlier.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        today = timezone.localdate()
        due = list(
            RecipeVersion.objects.filter(
                status=RecipeVersionStatus.APPROVED,
                effective_from__lte=today,
            ).select_related('recipe').order_by('effective_from', 'id')
        )
        if dry_run:
            for version in due:
                self.stdout.write(
                    f'would activate version_id={version.id} recipe_id={version.recipe_id}'
                )
            self.stdout.write(f'{len(due)} version(s) due')
            return

        request = RequestFactory().post('/recipe/cron/activate-due/')
        count = 0
        for version in due:
            before = {
                'status': version.status,
                'version_number': version.version_number,
            }
            activated = activate_version(version)
            capture_product_audit(
                request,
                product_id=version.recipe.product_id,
                entity='recipe_version',
                action='update',
                before_data=before,
                after_data={
                    'status': activated.status,
                    'version_number': activated.version_number,
                    'source': 'scheduled',
                },
            )
            count += 1
            self.stdout.write(f'activated version_id={version.id}')
        self.stdout.write(self.style.SUCCESS(f'{count} version(s) activated'))
