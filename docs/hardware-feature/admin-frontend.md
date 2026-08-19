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
| `last_username` / `last_seen_at` | Last used |
| `status` | `active` / `repair` / `retired` |
| `serial` | Detail only |

`GET /hardware/devices/:id/` — same object plus `identity_json`.

Admin-only: `POST /hardware/devices/`, `PATCH /hardware/devices/:id/` (`assigned_user_id` = “pick up device 3”).

## Device feed (Instagram)

Company wall: `GET /hardware/feed/?code=&user_id=&from=&to=&limit=&offset=`

One gun: `GET /hardware/devices/:id/posts/`

Post a photo/video (any signed-in user):

`POST /hardware/devices/:id/posts/`  
`Content-Type: multipart/form-data`

| Field | Required |
|---|---|
| `file` (or `image`) | yes — JPEG / PNG / WebP / GIF / MP4 / MOV / WebM, max 20 MB |
| `caption` | no |

Response `data`:

| Field | Meaning |
|---|---|
| `id` | Post PK |
| `media_url` | Presigned media |
| `kind` | `image` or `video` |
| `caption` | Text |
| `username` / `display_name` / `user_photo_url` | Who posted |
| `device_code` / `device_nickname` | Which gun |
| `created_at` | ISO datetime |

Delete (author or admin): `DELETE /hardware/devices/:id/posts/:post_id/`

## Suggested screens

1. **Guns** — `GET /hardware/devices/` grid, `cover_url` + `code`.
2. **Gun detail** — `GET /hardware/devices/GUN-01/` + `GET /hardware/devices/GUN-01/posts/` timeline.
3. **Feed** — `GET /hardware/feed/` newest first, all guns.
4. **Allocate** — `PATCH` `assigned_user_id`.

Usage log (who scanned, not photos): `GET /hardware/usage/` — admin only.
