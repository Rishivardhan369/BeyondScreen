"""Minimal push-provider boundary; production credentials remain environment-owned."""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class PushProvider:
    def send(self, *, device, title, body):
        raise NotImplementedError

class DisabledPushProvider(PushProvider):
    def send(self, *, device, title, body):
        return {"accepted": False, "reason": "push_not_configured"}

def get_push_provider():
    # Real FCM delivery intentionally requires a deployment-owned provider and
    # credentials. Local Android reminders remain available without it.
    return DisabledPushProvider()
