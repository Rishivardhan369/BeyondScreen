# BeyondScreen

BeyondScreen turns recorded phone activity into an explainable Usage Assessment, goal-aware Actionable Inputs, a concrete Goal Rescue, and visible Momentum over time. It supports manual screen-time entry plus optional Android, iOS, generic OCR, CSV, and text report parsing.

## Product flow

- **Mobile Analytics** stores only normalized metrics such as screen time, app usage, pickups, notifications, sessions, and reliable timing data.
- **Usage Assessment** compares recorded days with the user's own history. Missing days are never treated as zero.
- **Actionable Inputs** offer up to three deterministic, explainable suggestions connected to current usage and Goal DNA.
- **Goal Rescue** selects one concrete action from the active primary goal that can be completed and recorded in **Momentum**.
- **Goal Progress**, **Weekly Review**, and **Personal Insights** show health, milestones, consistency, app patterns, and longer-term trends.
- Weekly CSV/PDF downloads and the personal JSON export remain user-scoped.

## Local setup

1. Clone the repository and enter its directory.
2. Create and activate a Python 3.12+ virtual environment (`py -m venv .venv` on Windows).
3. Install dependencies with `python -m pip install -r requirements.txt`.
4. Copy `.env.example` values into your environment and keep `DJANGO_DEBUG=True` locally.
5. Run `python manage.py migrate`.
6. Start the application with `python manage.py runserver`.

OCR is optional. Manual minutes are enough to create a report. For local image OCR, install the optional `pytesseract` Python package and the native Tesseract executable separately; without them, BeyondScreen fails safely back to manual entry. Uploads are limited to one supported file under 10 MB per request. PDF input currently uses the same manual fallback because no PDF text parser is required at runtime.

## Demo data

`python manage.py seed_demo_data` creates or refreshes the dedicated `beyondscreen_demo` account only. It includes Android/iOS-style analytics, app/category history, interaction metrics, Usage Signals, Actionable Inputs, Rescue outcomes, Momentum, and several weeks of review data. Use `--reset` to explicitly delete and rebuild only that account. The command never runs automatically and is disabled when `DEBUG=False`.

## Production configuration

Set `DJANGO_DEBUG=False`, a strong `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, and a durable `DJANGO_STATIC_ROOT`. Secure cookies, HTTPS redirect, HSTS, MIME sniffing protection, referrer policy, and frame denial then use production-safe settings. Set `DJANGO_TRUST_PROXY_SSL_HEADER=True` only behind a trusted proxy that overwrites `X-Forwarded-Proto`.

Password-reset email uses the console backend by default. Configure the `EMAIL_*` variables from `.env.example` for SMTP; never commit credentials. Local SQLite remains the default. PostgreSQL is optional through the discrete `DB_*` variables and requires an installed PostgreSQL driver.

Persistent avatars/media require a durable `DJANGO_MEDIA_ROOT` and must be included in backup and restore planning. Report uploads are processed in-memory and are not retained as public files; database records store only existing metadata.

## Backup and deployment

Back up `db.sqlite3` for development data. In production, use database/provider-native backups and separately back up persistent media. A safe deployment order is: configure environment, install dependencies, take a backup, run `python manage.py migrate`, run `python manage.py collectstatic`, start the server, then perform a health check and authenticated smoke test.

Generated downloads are derived per request. Infrastructure should provide TLS, request-size enforcement, rate limiting, access logs that exclude sensitive bodies, and durable static/media serving. A minimal deployment probe is available at `/health/` and returns only `{"status": "ok"}`.
