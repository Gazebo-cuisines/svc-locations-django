from __future__ import annotations

import json
import os
from datetime import datetime, timezone as dt_timezone

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from stock_ledger.models import StockChainAnchor, StockChainHead, StockEntry


def _s3_client():
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


def _bucket() -> str:
    return getattr(settings, 'AUDIT_S3_BUCKET', None) or 'gazebo-audit-logging'


def publish_chain_anchor(*, s3_client=None) -> StockChainAnchor | None:
    """
    Publish current stock_chain_head as JSON to S3 and index in stock_chain_anchor.
    Returns None if head is empty or this head_entry was already anchored.
    """
    head = StockChainHead.objects.filter(pk=1).first()
    if head is None or head.head_entry_id is None or not head.head_hash:
        return None

    if StockChainAnchor.objects.filter(head_entry_id=head.head_entry_id).exists():
        return StockChainAnchor.objects.get(head_entry_id=head.head_entry_id)

    anchored_at = timezone.now()
    anchored_at_utc = anchored_at.astimezone(dt_timezone.utc)
    payload = {
        'event': 'stock_chain_anchor',
        'head_entry_id': head.head_entry_id,
        'head_hash': head.head_hash,
        'entry_count': head.entry_count,
        'anchored_at': anchored_at_utc.isoformat().replace('+00:00', 'Z'),
    }
    key = (
        'stock-ledger/anchors/'
        f'{anchored_at_utc.strftime("%Y/%m/%d")}/'
        f'entry-{head.head_entry_id}-{head.head_hash[:16]}.json'
    )
    body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')

    client = s3_client or _s3_client()
    put_kwargs = {
        'Bucket': _bucket(),
        'Key': key,
        'Body': body,
        'ContentType': 'application/json',
    }
    # Best-effort Object Lock; ignore if bucket has no lock config.
    try:
        resp = client.put_object(
            **put_kwargs,
            ObjectLockMode='COMPLIANCE',
            ObjectLockRetainUntilDate=datetime(
                anchored_at_utc.year + 7,
                anchored_at_utc.month,
                anchored_at_utc.day,
                tzinfo=dt_timezone.utc,
            ),
        )
    except ClientError:
        resp = client.put_object(**put_kwargs)

    version_id = resp.get('VersionId')
    with transaction.atomic():
        anchor, _ = StockChainAnchor.objects.get_or_create(
            head_entry_id=head.head_entry_id,
            defaults={
                'head_hash': head.head_hash,
                'entry_count': head.entry_count,
                's3_object_key': key,
                's3_version_id': version_id,
                'anchored_at': anchored_at,
            },
        )
    return anchor


def verify_chain_anchors(*, s3_client=None, limit: int | None = None) -> dict:
    """
    Re-read S3 JSON for each stock_chain_anchor and compare to ledger + local index.
    """
    qs = StockChainAnchor.objects.order_by('id')
    if limit is not None:
        qs = qs[:limit]
    anchors = list(qs)
    if not anchors:
        return {
            'ok': True,
            'checked': 0,
            'mismatch_count': 0,
            'mismatches': [],
        }

    client = s3_client or _s3_client()
    bucket = _bucket()

    mismatches: list[dict] = []
    checked = 0
    for anchor in anchors:
        checked += 1
        get_kwargs = {'Bucket': bucket, 'Key': anchor.s3_object_key}
        if anchor.s3_version_id:
            get_kwargs['VersionId'] = anchor.s3_version_id
        try:
            obj = client.get_object(**get_kwargs)
            payload = json.loads(obj['Body'].read().decode('utf-8'))
        except Exception as exc:
            mismatches.append({
                'anchor_id': anchor.id,
                'kind': 's3_read_error',
                'detail': str(exc),
            })
            continue

        entry = (
            StockEntry.objects
            .filter(pk=anchor.head_entry_id)
            .only('id', 'entry_hash')
            .first()
        )
        problems = []
        if payload.get('head_hash') != anchor.head_hash:
            problems.append('s3_vs_anchor_hash')
        if payload.get('head_entry_id') != anchor.head_entry_id:
            problems.append('s3_vs_anchor_entry_id')
        if entry is None:
            problems.append('entry_missing')
        elif entry.entry_hash != anchor.head_hash:
            problems.append('ledger_vs_anchor_hash')
        elif payload.get('head_hash') != entry.entry_hash:
            problems.append('s3_vs_ledger_hash')

        if problems:
            mismatches.append({
                'anchor_id': anchor.id,
                'head_entry_id': anchor.head_entry_id,
                'kind': 'mismatch',
                'problems': problems,
                's3_head_hash': payload.get('head_hash'),
                'anchor_head_hash': anchor.head_hash,
                'ledger_entry_hash': None if entry is None else entry.entry_hash,
            })

    return {
        'ok': not mismatches,
        'checked': checked,
        'mismatch_count': len(mismatches),
        'mismatches': mismatches,
    }
