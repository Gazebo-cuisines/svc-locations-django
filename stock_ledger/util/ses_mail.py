"""Send email via AWS SES (same credential pattern as S3)."""

from __future__ import annotations

import os
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

_LOGO_CID = 'gazebo-logo'
_DEFAULT_LOGO = (
    Path(__file__).resolve().parent.parent / 'assets' / 'gazebo-logo.png'
)


class SesMailError(Exception):
    pass


@lru_cache(maxsize=1)
def ses_client():
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
    return session.client('ses', region_name=region)


def ses_from_email() -> str:
    value = (
        os.getenv('SES_FROM_EMAIL')
        or getattr(settings, 'SES_FROM_EMAIL', None)
        or ''
    ).strip()
    if not value:
        raise SesMailError('SES_FROM_EMAIL is not configured.')
    return value


def logo_path() -> Path | None:
    override = (
        os.getenv('SES_LOGO_PATH')
        or getattr(settings, 'SES_LOGO_PATH', None)
        or ''
    ).strip()
    path = Path(override) if override else _DEFAULT_LOGO
    return path if path.is_file() else None


def _configuration_set() -> str | None:
    value = (
        os.getenv('SES_CONFIGURATION_SET')
        or getattr(settings, 'SES_CONFIGURATION_SET', None)
        or ''
    ).strip()
    return value or None


def send_email_with_attachment(
    *,
    to_addresses: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    filename: str,
    content: bytes,
    content_type: str = 'application/octet-stream',
    inline_logo: bool = True,
) -> str:
    """Send one SES message (HTML + plain + CSV). Returns MessageId."""
    if not to_addresses:
        raise SesMailError('No recipients.')

    # Flat multipart/mixed: alternative body (+ optional related logo) + file.
    # Nested related-as-outer + duplicate Content-Type caused silent drops (M365).
    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject.replace('\u2014', '-').replace('\u2013', '-')
    msg['From'] = ses_from_email()
    msg['To'] = ', '.join(to_addresses)

    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(body_text, 'plain', 'utf-8'))
    if body_html:
        alt.attach(MIMEText(body_html, 'html', 'utf-8'))

    path = logo_path() if (inline_logo and body_html) else None
    if path is not None:
        related = MIMEMultipart('related')
        related.attach(alt)
        img = MIMEImage(path.read_bytes(), _subtype='png')
        img.add_header('Content-ID', f'<{_LOGO_CID}>')
        img.add_header('Content-Disposition', 'inline', filename=path.name)
        related.attach(img)
        msg.attach(related)
    else:
        msg.attach(alt)

    part = MIMEBase('application', 'octet-stream')
    if '/' in content_type:
        main, _, sub = content_type.partition('/')
        part = MIMEBase(main or 'application', sub or 'octet-stream')
    part.set_payload(content)
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', 'attachment', filename=filename)
    msg.attach(part)

    send_kwargs: dict = {
        'Source': msg['From'],
        'Destinations': to_addresses,
        'RawMessage': {'Data': msg.as_bytes()},
    }
    config_set = _configuration_set()
    if config_set:
        send_kwargs['ConfigurationSetName'] = config_set

    try:
        resp = ses_client().send_raw_email(**send_kwargs)
    except ClientError as exc:
        raise SesMailError(exc.response['Error'].get('Message') or str(exc)) from exc
    return resp.get('MessageId') or ''
