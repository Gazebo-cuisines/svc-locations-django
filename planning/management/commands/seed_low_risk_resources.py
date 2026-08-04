"""
Ensure Low Risk process cells have at least one active resource.

  python manage.py seed_low_risk_resources
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from planning.models import Resource, ResourceGroup

# location_id -> [(name, code), ...]
SEEDS = {
    223: [('Steam Cabinet - 1', 'STEAM-1'), ('Steam Cabinet - 2', 'STEAM-2')],
    80: [('Debox Station - 1', 'DEBOX-1')],
    85: [('Marination Drum - 1', 'MARIN-1')],
    224: [('Mincer - 1', 'MINCE-1')],
    225: [('Soak Tank - 1', 'SOAK-1')],
}


class Command(BaseCommand):
    help = 'Seed missing Low Risk resources (Steaming, Deboxing, …)'

    def handle(self, *args, **options):
        next_id = (Resource.objects.order_by('-id').values_list('id', flat=True).first() or 0) + 1
        group = ResourceGroup.objects.order_by('id').first()
        created = 0
        for loc_id, items in SEEDS.items():
            for name, code in items:
                if Resource.objects.filter(code=code).exists():
                    continue
                while Resource.objects.filter(pk=next_id).exists():
                    next_id += 1
                Resource.objects.create(
                    id=next_id,
                    code=code,
                    name=name,
                    location_id=loc_id,
                    group=group,
                    is_active=True,
                )
                self.stdout.write(f'  + resource {next_id} {code} @ location {loc_id}')
                next_id += 1
                created += 1
        self.stdout.write(self.style.SUCCESS(f'Done. created={created}'))
