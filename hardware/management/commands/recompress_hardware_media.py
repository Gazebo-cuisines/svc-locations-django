from django.core.management.base import BaseCommand

from hardware.media import recompress_post
from hardware.models import HardwareDevicePost


class Command(BaseCommand):
    help = 'Recompress existing gun-feed JPEGs in S3 to WebP.'

    def handle(self, *args, **options):
        qs = HardwareDevicePost.objects.select_related('device')
        done = 0
        for row in qs:
            if recompress_post(row):
                done += 1
                self.stdout.write(f'{row.device.code} post {row.id} -> {row.media_key}')
        self.stdout.write(self.style.SUCCESS(f'{done} of {qs.count()} recompressed.'))
