from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import InAppNotification, MaintenanceJobRun

class Command(BaseCommand):
    help = "Remove read notifications older than 180 days; unread notifications are preserved."
    def handle(self, *args, **options):
        now = timezone.now(); count, _ = InAppNotification.objects.filter(read_at__isnull=False, created_at__lt=now - timedelta(days=180)).delete()
        MaintenanceJobRun.objects.update_or_create(job_name="cleanup_notifications", defaults={"last_run_at": now, "last_success_at": now, "processed_count": count, "status": "success", "error_code": ""})
        self.stdout.write(self.style.SUCCESS(f"Removed {count} old read notification(s)."))
