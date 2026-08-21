"""Stable report semantics for analytics consumers; contains no OCR internals."""
from dataclasses import asdict, dataclass, field

from .models import DigitalSummary
from .models import UserAppPreference


@dataclass(frozen=True)
class ReportDataQuality:
    ingestion_source: str
    total_basis: str
    has_official_total: bool
    recognized_app_minutes: int
    official_total_minutes: int | None
    app_coverage_ratio: float | None
    extraction_confidence: str | None
    was_user_confirmed: bool
    supports_total_analysis: bool
    supports_app_analysis: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self):
        return asdict(self)


def report_data_quality(summary: DigitalSummary) -> ReportDataQuality:
    snapshot = summary.mobile_analytics_snapshot or {}
    apps = snapshot.get("apps") if isinstance(snapshot.get("apps"), list) else []
    recognized = sum(
        item.get("minutes", 0) for item in apps
        if isinstance(item, dict) and isinstance(item.get("minutes"), int) and item["minutes"] >= 0
    )
    basis = summary.total_basis or DigitalSummary.TOTAL_LEGACY
    explicit_bases = {DigitalSummary.TOTAL_OFFICIAL, DigitalSummary.TOTAL_USER, DigitalSummary.TOTAL_DEVICE}
    official = snapshot.get("total_minutes") if basis == DigitalSummary.TOTAL_OFFICIAL else None
    coverage = None
    warnings = []
    if official is not None and official > 0:
        coverage = round(recognized / official * 100, 1)
        if recognized > official:
            warnings.append("Recognized app durations exceed the official reported total.")
    elif official == 0 and recognized:
        warnings.append("Recognized app durations exist while the official reported total is zero.")
    if basis == DigitalSummary.TOTAL_APP_SUM:
        warnings.append("The summary compatibility total is derived from recognized apps, not an official total.")
    return ReportDataQuality(
        ingestion_source=summary.ingestion_source or DigitalSummary.SOURCE_LEGACY,
        total_basis=basis,
        has_official_total=basis == DigitalSummary.TOTAL_OFFICIAL and official is not None,
        recognized_app_minutes=recognized,
        official_total_minutes=official,
        app_coverage_ratio=coverage,
        extraction_confidence=snapshot.get("detection_confidence"),
        was_user_confirmed=bool(summary.was_user_confirmed),
        supports_total_analysis=basis in explicit_bases,
        supports_app_analysis=bool(apps),
        warnings=tuple(warnings),
    )


def reconcile_known_apps(user, apps):
    """Apply only whitespace/punctuation-equivalent user-specific known names."""
    if not user or not getattr(user, "is_authenticated", False):
        return apps
    preferences = UserAppPreference.objects.filter(user=user).only("normalized_app_name", "display_name")
    known = {}
    for preference in preferences:
        display = preference.display_name or preference.normalized_app_name
        key = "".join(character for character in display.casefold() if character.isalnum())
        if key:
            known.setdefault(key, display)
    reconciled = []
    for app in apps:
        item = dict(app)
        key = "".join(character for character in str(item.get("name", "")).casefold() if character.isalnum())
        if key in known:
            item["name"] = known[key]
        reconciled.append(item)
    return reconciled
