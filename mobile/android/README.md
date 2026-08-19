# BeyondScreen Android companion

Native Kotlin/AndroidX companion for aggregate Mobile Analytics sync. It uses `UsageStatsManager`, optional notification-event counting (content is never read), encrypted device-token storage, WorkManager retry/idempotency, manual preview/sync, and user-controlled periodic sync.

Open this directory in Android Studio with Android SDK 35 and JDK 17. The repository intentionally contains no keystore, signing key, `local.properties`, Gradle cache, or credential. Set `BEYONDSCREEN_API_URL=https://your-host/` as a Gradle property for release builds; release manifests reject cleartext traffic. Configure signing locally or in CI.

Usage access is user-granted in Android Settings. Notification listener access is optional. Exact pickups are sent as `null` because Android does not expose a reliable general pickup counter through UsageStats. iOS ingestion is supported by the backend schema, but native Screen Time collection requires Apple entitlements not available to this repository.
