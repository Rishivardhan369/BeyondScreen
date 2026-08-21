from django.conf import settings


def google_auth(request):
    return {"google_oauth_configured": settings.GOOGLE_OAUTH_CONFIGURED}
