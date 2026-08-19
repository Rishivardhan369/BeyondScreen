# Device sync API v1

All endpoints return JSON. Production clients must use HTTPS. Credentials are random per-device bearer tokens, shown only in the successful pairing response and stored hashed by the server.

## Pair

The signed-in web user explicitly accepts the device analytics disclosure and creates a single-use, 10-minute code at `/profile/devices/`. The companion sends `POST /api/v1/pair/` with `pairing_code`, `consent_version`, `name`, `platform`, `app_version`, and optional model/OS strings. A successful response contains `device_id`, `device_token`, and `schema_version`. Used, expired, invalid, or throttled codes are rejected.

## Sync

`POST /api/v1/mobile-analytics/` requires `Authorization: Bearer <device token>` and a JSON payload no larger than 256 KiB:

```json
{
  "schema_version": 1,
  "device_report_id": "stable-on-retry",
  "report_date": "2026-08-19",
  "timezone": "Asia/Kolkata",
  "total_minutes": 240,
  "apps": [{"name": "Example", "package": "com.example", "minutes": 35, "category": null}],
  "pickups": null,
  "notifications": 42,
  "sessions": null,
  "longest_session_minutes": 25,
  "first_use_time": "08:10",
  "last_use_time": "23:05",
  "source_type": "android_device_sync"
}
```

Unknown optional values are `null` or omitted, never fabricated. Identity comes only from the device token; payload user identifiers are ignored. `(device, device_report_id)` is database-unique, so retries return the existing summary. Separate manual or multi-device reports are preserved and daily analytics aggregate them using existing recorded-day rules.

Success returns `accepted`, `summary_id`, `assessment_generated`, `idempotent`, and `sync_timestamp`. Unsupported schemas return HTTP 426 with supported versions. Revoked/invalid credentials return 401. Invalid payloads return stable generic error codes. Raw failed payloads are not retained.

## Rotation and compatibility

`POST /api/v1/device/token/rotate/` requires the active credential, immediately replaces it, and returns the new token once. `/api/v1/compatibility/` exposes only safe schema/app compatibility values. Web revocation immediately invalidates future API access. Synced history is retained unless the user separately confirms deletion of attributable device data.

The API stores aggregate durations/counts only. It does not accept notification bodies, messages, contacts, photos, location, keystrokes, or browser history.
