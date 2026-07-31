"""
Soft-clear catalog for a fresh start without breaking FKs / stock triggers.

- Deletes all recipes (components → versions → recipes)
- Deactivates all products (is_active=False)
- Releases open stock reservations

Usage:
  python manage.py wipe_catalog_fresh --dry-run
  python manage.py wipe_catalog_fresh
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Product
from recipe.models import Recipe, RecipeComponent, RecipeVersion
from stock_ledger.models import StockReservation, StockReservationStatus


class Command(BaseCommand):
    help = 'Deactivate products, delete recipes, release open reservations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print counts only; no writes',
        )

    def handle(self, *args, **options):
        active_n = Product.objects.filter(is_active=True).count()
        recipe_n = Recipe.objects.count()
        version_n = RecipeVersion.objects.count()
        component_n = RecipeComponent.objects.count()
        open_res = StockReservation.objects.filter(
            status=StockReservationStatus.OPEN,
        ).count()

        self.stdout.write(
            f'Plan: deactivate products={active_n}, '
            f'delete recipes={recipe_n} versions={version_n} components={component_n}, '
            f'release reservations={open_res}'
        )
        if options['dry_run']:
            return

        with transaction.atomic():
            deleted_components, _ = RecipeComponent.objects.all().delete()
            deleted_versions, _ = RecipeVersion.objects.all().delete()
            deleted_recipes, _ = Recipe.objects.all().delete()
            deactivated = Product.objects.filter(is_active=True).update(is_active=False)
            released = StockReservation.objects.filter(
                status=StockReservationStatus.OPEN,
            ).update(status=StockReservationStatus.RELEASED)

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. deactivated={deactivated} '
                f'recipes_deleted={deleted_recipes} versions={deleted_versions} '
                f'components={deleted_components} reservations_released={released} '
                f'active_left={Product.objects.filter(is_active=True).count()} '
                f'recipes_left={Recipe.objects.count()}'
            )
        )
