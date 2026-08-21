"""One S3 client per process. A new boto3 Session per row stalls on credentials."""

import os
import time
from functools import lru_cache

import boto3
from django.conf import settings

# Same object → same URL for ~1h so the browser caches instead of re-fetching
# because list + detail each minted a new signature.
_PRESIGN: dict[tuple[str, str], tuple[str, float]] = {}
PRESIGN_SECONDS = 3600


@lru_cache(maxsize=1)
def s3_client():
    profile = os.getenv('AWS_PROFILE') or getattr(settings, 'AWS_PROFILE', None)
    region = (
        os.getenv('AWS_DEFAULT_REGION')
        or getattr(settings, 'AWS_DEFAULT_REGION', None)
        or 'eu-west-2'
    )
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    except Exception:
        session = boto3.Session()
    return session.client('s3', region_name=region)


def media_bucket() -> str:
    return getattr(settings, 'MEDIA_S3_BUCKET', None) or 'gazebo-media-files'


def presigned_get(key: str, *, expires_in: int = PRESIGN_SECONDS) -> str | None:
    if not key:
        return None
    bucket = media_bucket()
    now = time.time()
    cached = _PRESIGN.get((bucket, key))
    if cached and cached[1] > now + 120:
        return cached[0]
    try:
        url = s3_client().generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return None
    _PRESIGN[(bucket, key)] = (url, now + expires_in)
    return url
