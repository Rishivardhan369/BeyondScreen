# BeyondScreen

BeyondScreen turns screen-time reflection into goal-linked actions, Momentum history, and weekly reviews.

## Local setup

1. Clone the repository and enter its directory.
2. Create and activate a Python 3.12+ virtual environment (`py -m venv .venv` on Windows).
3. Install dependencies with `python -m pip install -r requirements.txt`.
4. Copy `.env.example` values into your environment and keep `DJANGO_DEBUG=True` locally.
5. Run `python manage.py migrate`.
6. Start the application with `python manage.py runserver`.

OCR is optional. When native OCR tooling is unavailable, BeyondScreen keeps working and asks for manual screen-time minutes. Uploads are limited to one supported file under 10 MB per request. Deployment-level request rate limiting should be configured at the reverse proxy.

## Demo data

`python manage.py seed_demo_data` creates or refreshes the dedicated `beyondscreen_demo` account only. Use `--reset` to explicitly delete and rebuild data owned by that account. The command never runs automatically and must not be used as production startup logic.

## Production configuration

Set `DJANGO_DEBUG=False`, a strong `DJANGO_SECRET_KEY`, and `DJANGO_ALLOWED_HOSTS`. Secure cookies, HTTPS redirect, HSTS, MIME sniffing protection, referrer policy, and frame denial then use production-safe settings. Set `DJANGO_TRUST_PROXY_SSL_HEADER=True` only behind a trusted proxy that overwrites `X-Forwarded-Proto`.

Password-reset email uses the console backend by default. Configure the `EMAIL_*` variables from `.env.example` for SMTP; never commit credentials. Local SQLite remains the default. PostgreSQL is optional through the discrete `DB_*` variables and requires an installed PostgreSQL driver.

Persistent avatars/media require a durable `DJANGO_MEDIA_ROOT` and must be included in backup and restore planning. Report uploads are processed in-memory and are not retained as public files; database records store only existing metadata.

## Backup and deployment

Back up `db.sqlite3` for development data. In production, use database/provider-native backups and separately back up persistent media. A safe deployment order is: configure environment, install dependencies, take a backup, run `python manage.py migrate`, run `python manage.py collectstatic`, start the server, then perform a health check and authenticated smoke test.

Generated downloads are derived per request. Infrastructure should provide TLS, request-size enforcement, rate limiting, access logs that exclude sensitive bodies, and durable static/media serving.
