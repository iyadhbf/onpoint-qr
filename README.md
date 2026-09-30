# AG CONSULTING /qrcode · Local Clone

Standalone local clone of the observed AG CONSULTING admin dashboard. It is intentionally disconnected from the live website. Data is persisted in the local `ag_consulting.sqlite3` database.

## Run

Install dependencies once, then start the Flask server:

```powershell
c:/python314/python.exe -m pip install -r requirements.txt
c:/python314/python.exe server.py
```

Open http://127.0.0.1:4173.

## Render deployment

The included `render.yaml` is configured for the service name `onpoint-qr`, which produces `onpoint-qr.onrender.com` when available. Push this folder to a GitHub repository, create a Render Blueprint from that repository, and deploy it. Render will install the dependencies and run Gunicorn on its assigned port.

The free Render filesystem is ephemeral, so SQLite data can be lost during a service restart or redeploy. For production data, attach a managed Postgres database and move backups to durable object storage before relying on the public deployment.

## Production configuration

Set `DATABASE_URL` to the Render Postgres connection string. The app uses PostgreSQL whenever that variable is present and keeps SQLite only for local development. Set `ONPOINT_SEED_DATA=false` for a clean trial database; the admin reset endpoint is `POST /api/admin/reset`.

For durable backups, configure `BACKUP_BUCKET`, `BACKUP_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` for S3, Cloudflare R2, or another S3-compatible service. Backups are uploaded before writes and resets.

Set `ONPOINT_ADMIN_PASSWORD`, `ONPOINT_SALES_PASSWORD`, and `ONPOINT_VIEWER_PASSWORD` in Render before sharing the public URL. Add a custom domain in Render under the service settings, then point its DNS CNAME to the Render-provided hostname.

## Dynamic features

- Plaques, orders, messages, metrics, lots, and stock load from SQLite through `/api/dashboard`.
- Plaque configuration is saved with `PATCH /api/plaques/<code>`.
- Batch creation uses `POST /api/plaques` and generates collision-checked `AGC-XXXXXX` codes.
- Each code has high-error-correction PNG, SVG, and A6-sized PDF assets at `/api/plaques/<code>/qr`, `/qr.svg`, and `/qr.pdf`.
- Scanning `/qrcode/<code>` records timestamp, device, referrer, and optional proxy-provided country/city before redirecting.
- `/api/analytics?days=30` returns daily scans, devices, referrers, and locations.
- The app creates rolling backups in `backups/` before writes and supports admin-only JSON export/import.

## Local roles

These demonstration credentials are local-only and must be changed before deployment:

- `admin` / `onpoint-admin`: full access, backups, export/import
- `sales` / `onpoint-sales`: create and edit plaques
- `viewer` / `onpoint-viewer`: read-only dashboard and QR downloads

The seeded records are local demonstration data. No action is sent to the original website.
