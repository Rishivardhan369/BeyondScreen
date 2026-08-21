from django.utils import timezone
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class BeyondScreenSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Bridge trusted provider verification into the existing profile state."""

    @staticmethod
    def _has_verified_email(sociallogin):
        return any(address.verified for address in sociallogin.email_addresses)

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        if sociallogin.account.provider == "google" and self._has_verified_email(sociallogin):
            profile = user.userprofile
            if profile.email_verified_at is None:
                profile.email_verified_at = timezone.now()
                profile.save(update_fields=["email_verified_at"])
        return user

    def pre_social_login(self, request, sociallogin):
        super().pre_social_login(request, sociallogin)
        user = sociallogin.user
        if user.pk and sociallogin.account.provider == "google" and self._has_verified_email(sociallogin):
            profile = user.userprofile
            if profile.email_verified_at is None:
                profile.email_verified_at = timezone.now()
                profile.save(update_fields=["email_verified_at"])
