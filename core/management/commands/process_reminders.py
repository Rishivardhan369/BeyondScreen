from django.core.management.base import BaseCommand
from core.platform_services import dispatch_due_reminders

class Command(BaseCommand):
    help = "Dispatch due, user-enabled reminders. Intended for a production scheduler."
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Processed {dispatch_due_reminders()} reminder(s)."))
