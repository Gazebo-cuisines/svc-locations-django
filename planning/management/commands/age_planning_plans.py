from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_date

from planning.services import forecast


class Command(BaseCommand):
    help = (
        'Age draft/locked plans older than today: close and rollover open lines '
        'to the next calendar day (Chunk 10). No MySQL EVENT.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--as-of',
            dest='as_of',
            default=None,
            help='YYYY-MM-DD (default: today local)',
        )

    def handle(self, *args, **options):
        as_of = None
        if options.get('as_of'):
            as_of = parse_date(options['as_of'])
            if as_of is None:
                raise SystemExit('Invalid --as-of date; use YYYY-MM-DD')
        else:
            as_of = timezone.localdate()
        results = forecast.age_open_plans(as_of=as_of)
        self.stdout.write(
            self.style.SUCCESS(
                f'aged {len(results)} plan(s) as of {as_of.isoformat()}',
            ),
        )
        for row in results:
            self.stdout.write(
                f"  plan {row['source_plan_id']} → {row['target_plan_id']} "
                f"({row['target_plan_date']})",
            )
