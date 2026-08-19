from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import DevicePairingCode, MaintenanceJobRun

class Command(BaseCommand):
    help = "Remove expired/consumed device pairing codes older than one day."
    def handle(self, *args, **options):
        now = timezone.now(); count, _ = DevicePairingCode.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
        MaintenanceJobRun.objects.update_or_create(job_name="cleanup_pairing_codes", defaults={"last_run_at": now, "last_success_at": now, "processed_count": count, "status": "success", "error_code": ""})
        self.stdout.write(self.style.SUCCESS(f"Removed {count} pairing code record(s)."))
