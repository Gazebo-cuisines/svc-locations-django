# Admin website — scanner guns

Auth: `Authorization: Bearer {id_token}` from `POST /auth/login/`.
Envelope: `{ status, message, data }`.
Presigned `*_url` fields expire in ~1 hour — refetch, do not cache the URL.

`:id` is sticker code (`GUN-01`) **or** serial (`26202524703110`).

## Device cards (list)

`GET /hardware/devices/`

Optional query: `location_id`, `assigned_user_id`.

Each row:

| Field | Use on the card |
|---|---|
| `code` | Title, e.g. GUN-01 |
| `nickname` | Subtitle |
| `cover_url` | Hero photo (latest post), or placeholder if null |
| `post_count` | Badge |
| `assigned_display_name` | “Picked up by…” |
| `presence` | `online` (green, seen &lt; 30s) / `idle` (amber, &lt; 5 min) / `offline` (grey) |
| `last_screen` | Current app screen (`goods_out`, `idle`, …) |
| `last_username` / `last_seen_at` | Last used |
| `last_ip` | Current Wi‑Fi IP |
| `status` | `active` / `repair` / `retired` |
| `serial` | Detail only |

`GET /hardware/devices/:id/` — same object plus `identity_json`.

Admin-only: `POST /hardware/devices/`, `PATCH /hardware/devices/:id/` (`assigned_user_id` = “pick up device 3”).

## Send a popup to a gun

Admin: `POST /hardware/devices/:id/messages/` `{ "title", "body" }` (`title` required).

Admin list (delivered vs acked): `GET /hardware/devices/:id/messages/`.

The gun does **not** get a push. It sees new rows on the next heartbeat (`data.messages[]`). After the operator taps OK: `POST /hardware/messages/:id/ack/` with `X-Device-Serial`.

## Warehouse app (gun)

Every ~15s while the app is open:

`POST /hardware/heartbeat/`

| Header | Required |
|---|---|
| `Authorization` | yes |
| `X-Device-Serial` | yes — omit on PCs; this route 400s without it |
| `X-Device-Ip` | no |
| `X-Device-Nickname` | no |

Body: `{ "screen": "goods_out" }` (`screen` optional). Typical: `idle` / `login` / `goods_in` / `goods_out` / `remaining` / `offline_queue`.

Response `data`: device object (`presence`, `last_screen`, …) plus `messages[]` (unacked, cap 20). First return sets `delivered_at`.

If `messages` is non-empty, show an Alert; on OK:

`POST /hardware/messages/:id/ack/` with the same serial header.

Force-update (no auth): `GET /app/version/?platform=android`

CI after S3 APK upload: `PUT /app/version/` with `Authorization: Bearer {APP_VERSION_API_TOKEN}` (not a user login).

```json
{ "platform": "android", "min_version": "1.0.1", "latest_version": "1.0.1" }
```

This sets `min_version` immediately — old kiosk builds show Update required until MDM installs the new APK. Set the same token as GitHub secret `APP_VERSION_API_TOKEN` in server `.env`.

## Device feed (Instagram)

Company wall: `GET /hardware/feed/?code=&user_id=&from=&to=&limit=&offset=`

One gun: `GET /hardware/devices/:id/posts/`

Post a photo/video (any signed-in user):

`POST /hardware/devices/:id/posts/`  
`Content-Type: multipart/form-data`

| Field | Required |
|---|---|
| `file` (or `image`) | yes — JPEG / PNG / WebP / GIF / MP4 / MOV / WebM, max 20 MB. Images are converted to compressed WebP before S3. |
| `caption` | no |

Response `data`:

| Field | Meaning |
|---|---|
| `id` | Post PK |
| `media_url` | Presigned media |
| `kind` | `image` or `video` |
| `content_type` | Stored MIME (`image/webp` for photos) |
| `metadata` | JSON: original format/bytes, stored size, width, height, EXIF (GPS stripped) |
| `caption` | Text |
| `username` / `display_name` / `user_photo_url` | Who posted |
| `device_code` / `device_nickname` | Which gun |
| `created_at` | ISO datetime |

Delete (author or admin): `DELETE /hardware/devices/:id/posts/:post_id/`

## Suggested screens

1. **Guns** — `GET /hardware/devices/` grid, `cover_url` + `code` + presence colour.
2. **Gun detail** — `GET /hardware/devices/GUN-01/` + posts + messages; send `{ title, body }`.
3. **Feed** — `GET /hardware/feed/` newest first, all guns.
4. **Allocate** — `PATCH` `assigned_user_id`.

Usage log (who scanned, not photos): `GET /hardware/usage/` — admin only.
