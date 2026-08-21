from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone


@receiver(user_logged_in)
def mark_trusted_google_email_verified(sender, request, user, **kwargs):
    """Mirror only allauth-verified Google identities into UserProfile."""
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount

    has_google = SocialAccount.objects.filter(user=user, provider="google").exists()
    has_verified_email = EmailAddress.objects.filter(user=user, email__iexact=user.email, verified=True).exists()
    if has_google and has_verified_email and user.userprofile.email_verified_at is None:
        user.userprofile.email_verified_at = timezone.now()
        user.userprofile.save(update_fields=["email_verified_at"])
