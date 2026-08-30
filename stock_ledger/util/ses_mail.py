"""Send email via AWS SES (same credential pattern as S3)."""

from __future__ import annotations

import os
from email.mime.application import MIMEApplication
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


def send_email_with_attachment(
    *,
    to_addresses: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    filename: str,
    content: bytes,
    content_type: str = 'text/csv',
    inline_logo: bool = True,
) -> str:
    """Send one SES message (HTML + plain + CSV). Returns MessageId."""
    if not to_addresses:
        raise SesMailError('No recipients.')

    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject
    msg['From'] = ses_from_email()
    msg['To'] = ', '.join(to_addresses)

    related = MIMEMultipart('related')
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(body_text, 'plain', 'utf-8'))
    if body_html:
        alt.attach(MIMEText(body_html, 'html', 'utf-8'))
    related.attach(alt)

    if inline_logo and body_html:
        path = logo_path()
        if path is not None:
            img = MIMEImage(path.read_bytes(), _subtype='png')
            img.add_header('Content-ID', f'<{_LOGO_CID}>')
            img.add_header('Content-Disposition', 'inline', filename=path.name)
            related.attach(img)

    msg.attach(related)

    part = MIMEApplication(content)
    part.add_header('Content-Type', content_type)
    part.add_header('Content-Disposition', 'attachment', filename=filename)
    msg.attach(part)

    try:
        resp = ses_client().send_raw_email(
            Source=msg['From'],
            Destinations=to_addresses,
            RawMessage={'Data': msg.as_bytes()},
        )
    except ClientError as exc:
        raise SesMailError(exc.response['Error'].get('Message') or str(exc)) from exc
    return resp.get('MessageId') or ''
