"""Convert uploads to compressed WebP before S3. Images only — not video/PDF."""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from PIL.ExifTags import TAGS

MAX_UPLOAD = 20 * 1024 * 1024
MAX_EDGE = 2048
WEBP_QUALITY = 80
_SKIP_EXIF = {'GPSInfo', 'MakerNote', 'UserComment', 'PrintImageMatching'}


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _exif_json(image: Image.Image) -> dict:
    try:
        raw = image.getexif()
    except Exception:
        return {}
    if not raw:
        return {}
    out = {}
    for tag_id, value in raw.items():
        name = TAGS.get(tag_id, str(tag_id))
        if name in _SKIP_EXIF or isinstance(value, bytes):
            continue
        out[name] = _jsonable(value)
    return out


def prepare_webp(uploaded_file, *, max_upload: int = MAX_UPLOAD) -> tuple[bytes, dict]:
    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > max_upload:
        mb = max_upload // (1024 * 1024)
        raise ValueError(f'Image is too large. Use a file under {mb} MB.')
    raw = uploaded_file.read()
    if len(raw) > max_upload:
        mb = max_upload // (1024 * 1024)
        raise ValueError(f'Image is too large. Use a file under {mb} MB.')
    try:
        image = Image.open(BytesIO(raw))
        image.load()
    except UnidentifiedImageError as exc:
        raise ValueError('File is not a valid image.') from exc

    original_format = (image.format or '').upper()
    meta = {
        'original_format': original_format or None,
        'original_content_type': (getattr(uploaded_file, 'content_type', None) or '')
        .split(';', 1)[0]
        .strip()
        .lower()
        or None,
        'original_bytes': len(raw),
        'exif': _exif_json(image),
    }
    image = ImageOps.exif_transpose(image)
    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGBA' if image.mode in ('P', 'LA') or 'A' in image.mode else 'RGB')
    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    quality = WEBP_QUALITY
    body = b''
    for quality in (WEBP_QUALITY, 70, 60):
        buf = BytesIO()
        image.save(buf, format='WEBP', quality=quality, method=6)
        body = buf.getvalue()
        if len(body) <= MAX_UPLOAD:
            break
    meta.update({
        'stored_format': 'WEBP',
        'stored_bytes': len(body),
        'width': image.width,
        'height': image.height,
        'quality': quality,
    })
    return body, meta


def prepare_webp_bytes(raw: bytes, *, content_type: str = '', max_upload: int = MAX_UPLOAD) -> tuple[bytes, dict]:
    buf = BytesIO(raw)
    buf.seek(0)
    buf.content_type = content_type  # type: ignore[attr-defined]
    buf.size = len(raw)  # type: ignore[attr-defined]
    return prepare_webp(buf, max_upload=max_upload)


def s3_image_args(body: bytes, meta: dict) -> dict:
    return {
        'Body': body,
        'ContentType': 'image/webp',
        'ServerSideEncryption': 'AES256',
        'Metadata': {
            'width': str(meta['width']),
            'height': str(meta['height']),
            'original-format': str(meta.get('original_format') or 'unknown')[:32],
        },
    }
