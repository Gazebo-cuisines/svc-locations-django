# Follow-up — Daily closing stock email (AWS SES)

**Depends on:** `GET /stock/reports/closing-stock/` (report APIs chunk)  
**Scope:** this backend + FE settings UI (other app)  
**Not in report APIs PR**

---

## Goal

Every day, email yesterday’s closing stock report to addresses configured in the frontend.

---

## Locked decisions

| Decision | Choice |
|----------|--------|
| Transport | **AWS SES** via `boto3` (`send_raw_email`) |
| Body | **HTML** (Gazebo Cloud branding + logo) + plain-text fallback |
| Attachment | **CSV** (stdlib `csv`) — same columns as closing-stock JSON |
| Logo | Inline CID from `stock_ledger/assets/gazebo-logo.png` (override: `SES_LOGO_PATH`) |
| As-of date | Previous calendar day end (`effective_at <= yesterday 23:59:59`) |
| Recipients | DB list, managed by FE (no hard-coded emails) |
| Schedule | External cron / EventBridge → `manage.py email_closing_stock_report` |
| Query | Reuse `closing_balances_as_of` from `stock_ledger/util/reports.py` |

---

## Backend (this repo)

### 1. Model

`StockReportEmailRecipient`

| Field | Notes |
|-------|--------|
| `email` | unique, validated |
| `is_active` | default true |
| `created_at` / `updated_at` | audit |

Optional later: `report_type` if more reports share the list. Start with closing-stock only.

### 2. APIs (FE settings)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/stock/reports/email-recipients/` | List |
| `POST` | `/stock/reports/email-recipients/` | Add `{ "email": "…" }` |
| `PATCH` | `/stock/reports/email-recipients/:id/` | Toggle `is_active` |
| `DELETE` | `/stock/reports/email-recipients/:id/` | Remove |

Auth: admin / stock-report write grant (match existing RBAC pattern).

### 3. SES helper

`stock_ledger/util/ses_mail.py` (or `core/ses.py`):

- `boto3` client `ses`, region = `AWS_DEFAULT_REGION` (already `eu-west-2`)
- From: env `SES_FROM_EMAIL` (verified identity in SES)
- Credentials: same as S3 (`AWS_PROFILE` / instance role) — already used elsewhere

### 4. Management command

```bash
python manage.py email_closing_stock_report
# optional: --as-of=2026-08-28  --dry-run
```

Steps:

1. Resolve `as_of` (default: yesterday in app TZ)
2. Call `closing_balances_as_of(as_of)`
3. Build CSV in memory
4. Load active recipients; if none → exit 0, log skip
5. SES send: subject `Closing stock as of {as_of}`, body short text, attach CSV
6. Exit non-zero on SES failure (so cron alerts)

### 5. Env

```
SES_FROM_EMAIL=noreply@your-verified-domain.com
AWS_DEFAULT_REGION=eu-west-2   # already set
```

SES sandbox: verify from + each recipient until production access.

Set in `.env`:

```
SES_FROM_EMAIL=noreply@your-verified-domain.com
```

### 6. Cron (ops, not code)

Example daily 06:00 UTC:

```
0 6 * * * cd /app && python manage.py email_closing_stock_report
```

Or EventBridge → ECS/Lambda runner. Same command.

---

## Frontend (other app)

Simple settings page:

- List recipients
- Add email
- Enable/disable or delete
- Optional: “Send test” later (not required for v1)

Calls the recipient CRUD APIs above. No cron in the browser.

---

## Out of scope (v1)

- PDF / Excel (openpyxl later if asked)
- Per-user timezone / multiple report schedules
- Goods-in / goods-out scheduled mail
- Inline HTML tables
- Celery (cron + management command is enough)

---

## Acceptance

1. Add 2 emails in FE → both in DB active  
2. Run command with `--as-of` → both get CSV matching API for that date  
3. Disable one → only active gets mail  
4. No recipients → no SES call, exit 0  
5. Bad SES config → exit ≠ 0  
