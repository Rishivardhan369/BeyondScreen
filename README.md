# BeyondScreen

BeyondScreen turns recorded phone activity into an explainable Usage Assessment, goal-aware Actionable Inputs, a concrete Goal Rescue, and visible Momentum over time. It supports manual screen-time entry plus optional Android, iOS, generic OCR, CSV, and text report parsing.

## Product flow

- **Mobile Analytics** stores only normalized metrics such as screen time, app usage, pickups, notifications, sessions, and reliable timing data.
- **Usage Assessment** compares recorded days with the user's own history. Missing days are never treated as zero.
- **Actionable Inputs** offer up to three deterministic, explainable suggestions connected to current usage and Goal DNA.
- **Goal Rescue** selects one concrete action from the active primary goal that can be completed and recorded in **Momentum**.
- **Goal Progress**, **Weekly Review**, and **Personal Insights** show health, milestones, consistency, app patterns, and longer-term trends.
- Weekly CSV/PDF downloads and the personal JSON export remain user-scoped.
- **App Preferences** let each user assign a neutral category/purpose and optional Goal link without rewriting frozen history.
- **Targets** support optional overall, app, and category daily limits chosen by the user.
- **Monthly Review** adds long-term recorded totals, averages, apps, interaction metrics, Momentum, and Rescue outcomes with CSV/PDF exports.
- **Reminders and Notifications** are opt-in, timezone/quiet-hour aware, and dispatched by an explicit scheduler command.
- **Device Sync** pairs revocable companion credentials and accepts idempotent, versioned aggregate analytics.
- The native Kotlin Android companion source lives in `mobile/android/`.

## Local setup

1. Clone the repository and enter its directory.
2. Create and activate a Python 3.12+ virtual environment (`py -m venv .venv` on Windows).
3. Install dependencies with `python -m pip install -r requirements.txt`.
4. Copy `.env.example` values into your environment and keep `DJANGO_DEBUG=True` locally.
5. Run `python manage.py migrate`.
6. Start the application with `python manage.py runserver`.

Manual minutes remain enough to create a report. The required Python OCR bridge (`pytesseract`) is installed by `requirements.txt`; image OCR additionally needs the native Tesseract executable. BeyondScreen resolves it from `TESSERACT_CMD`, the system `PATH`, or standard Windows installation folders, in that order. Run `python manage.py check_ocr` for a privacy-safe runtime diagnostic. If native OCR is unavailable, the UI explains the limitation and preserves manual entry. PNG, JPEG, and WEBP screenshots are content-validated, processed in memory, and never stored with raw OCR text. Partial results open an editable confirmation screen instead of being discarded. Uploads remain limited to one supported file under 10 MB per request. PDF input uses manual fallback because no reliable PDF parser is required at runtime.

## Demo data

`python manage.py seed_demo_data` creates or refreshes the dedicated `beyondscreen_demo` account only. It includes Android/iOS-style analytics, app/category history, interaction metrics, Usage Signals, Actionable Inputs, Rescue outcomes, Momentum, and several weeks of review data. Use `--reset` to explicitly delete and rebuild only that account. The command never runs automatically and is disabled when `DEBUG=False`.

## Production configuration

Set `DJANGO_DEBUG=False`, a strong `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, and a durable `DJANGO_STATIC_ROOT`. Secure cookies, HTTPS redirect, HSTS, MIME sniffing protection, referrer policy, and frame denial then use production-safe settings. Set `DJANGO_TRUST_PROXY_SSL_HEADER=True` only behind a trusted proxy that overwrites `X-Forwarded-Proto`.

Password-reset email uses the console backend by default. Configure the `EMAIL_*` variables from `.env.example` for SMTP; never commit credentials. Local SQLite remains the default. PostgreSQL is optional through the discrete `DB_*` variables and requires an installed PostgreSQL driver.

### Google Sign-In

Google Sign-In is optional and uses django-allauth's Google OAuth 2.0/OpenID Connect provider. Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in the environment; when either is absent, password login remains available and the Google action shows a safe configuration message. Only `openid`, `email`, and `profile` scopes are requested, and access tokens are not stored.

Configure the local Google authorized JavaScript origin as `http://127.0.0.1:8000` and the exact authorized redirect URI as `http://127.0.0.1:8000/accounts/google/login/callback/`. In production use `https://YOUR_DOMAIN` and `https://YOUR_DOMAIN/accounts/google/login/callback/`. Google-verified email can securely authenticate and connect to an existing account with the same email; unverified provider claims are never used for linking. Password registrations retain BeyondScreen's existing verification flow.

Persistent avatars/media require a durable `DJANGO_MEDIA_ROOT` and must be included in backup and restore planning. Report uploads are processed in-memory and are not retained as public files; database records store only existing metadata.

## Backup and deployment

Back up `db.sqlite3` for development data. In production, use database/provider-native backups and separately back up persistent media. A safe deployment order is: configure environment, install dependencies, take a backup, run `python manage.py migrate`, run `python manage.py collectstatic`, start the server, then perform a health check and authenticated smoke test.

Generated downloads are derived per request. Infrastructure should provide TLS, request-size enforcement, rate limiting, access logs that exclude sensitive bodies, and durable static/media serving. A minimal deployment probe is available at `/health/` and returns only `{"status": "ok"}`.

`/ready/` performs a minimal database query and reports only `ready` or `unavailable`. Request responses include a correlation ID; application logs exclude request bodies, secrets, raw OCR, notification content, and analytics payloads.

## Scheduled operations and retention

Run these commands through cron, Task Scheduler, or the deployment scheduler:

- `python manage.py process_reminders` at a suitable short interval.
- `python manage.py cleanup_pairing_codes` daily.
- `python manage.py cleanup_notifications` daily or weekly.

Read notifications older than 180 days may be removed. Unread notifications and historical user summaries are not silently purged. Expired pairing records are temporary; device credentials remain until rotation, revocation, or account deletion. Job status is recorded without private payloads.

## Backup and restore

For SQLite development, stop writers, copy `db.sqlite3`, and validate the copy with SQLite integrity/table-count checks before migration. Restore only while the application is stopped, after separately preserving the current file. Production PostgreSQL should use provider/native point-in-time backups and tested restore procedures. Back up persistent media separately; uploaded analytics reports are processed transiently, while avatars may be persistent.

## Device sync and Android

The versioned API contract and privacy guarantees are documented in `docs/device-sync-api.md`. Device pairing requires explicit disclosure consent, pairing codes expire after ten minutes and are single-use, credentials are hashed at rest, and retries are idempotent.

Open `mobile/android/` with Android Studio, SDK 35, and JDK 17. Supply `BEYONDSCREEN_API_URL` as a Gradle property and local/CI signing configuration; no keystore or secret belongs in Git. Release traffic is HTTPS-only. Usage access is required for app-duration aggregates, notification-listener access is optional and counts events without reading content, and unsupported pickup metrics remain null. WorkManager supports conservative daily sync and retry. Native iOS collection is not included because Apple Screen Time APIs require entitlement approval; iOS remains supported as an API platform and by web screenshot parsing.

Real SMTP, FCM, production database, and deployment-provider credentials are environment-owned. A disabled push-provider boundary and local Android reminders allow safe development without cloud credentials.
