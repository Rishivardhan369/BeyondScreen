from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from allauth.account.models import EmailAddress
from allauth.core.context import request_context
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers.google.provider import GoogleProvider

from .models import DigitalSummary, UserProfile
from .social_auth import BeyondScreenSocialAccountAdapter


class GoogleAuthTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self):
        request = self.factory.post("/accounts/google/login/")
        request.session = self.client.session
        return request

    def _social_login(self, email, *, verified=True, uid="google-uid", user=None):
        user = user or User(email=email, first_name="Google", last_name="User")
        account = SocialAccount(provider="google", uid=uid, user=user, extra_data={"email": email, "email_verified": verified})
        provider = GoogleProvider(self._request(), app=SocialApp(provider="google", name="Google test", client_id="test", secret="test"))
        return SocialLogin(user=user, account=account, email_addresses=[EmailAddress(email=email, verified=verified, primary=True)], provider=provider)

    def test_login_and_registration_expose_google_action(self):
        self.assertContains(self.client.get(reverse("core:login")), "Continue with Google")
        self.assertContains(self.client.get(reverse("core:register")), "Sign up with Google")

    def test_missing_configuration_is_safe(self):
        response = self.client.get(reverse("core:google_auth_unavailable"), follow=True)
        self.assertRedirects(response, reverse("core:login"))
        self.assertContains(response, "not configured for this environment")

    @override_settings(
        GOOGLE_OAUTH_CONFIGURED=True,
        SOCIALACCOUNT_PROVIDERS={"google": {"SCOPE": ["openid", "email", "profile"], "EMAIL_AUTHENTICATION": True, "EMAIL_AUTHENTICATION_AUTO_CONNECT": True, "APPS": [{"client_id": "test-client", "secret": "test-secret", "key": ""}]}},
    )
    def test_configured_action_posts_to_allauth(self):
        response = self.client.get(reverse("core:login"))
        self.assertContains(response, 'method="post"')
        self.assertContains(response, reverse("google_login"))

    def test_new_google_user_and_profile_are_provisioned_verified(self):
        sociallogin = self._social_login("new-google@example.com")
        user = BeyondScreenSocialAccountAdapter().save_user(self._request(), sociallogin)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertIsNotNone(user.userprofile.email_verified_at)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(SocialAccount.objects.filter(user=user, provider="google", uid="google-uid").exists())

    def test_verified_existing_email_matches_without_duplicate(self):
        existing = User.objects.create_user("existing", "existing@example.com", "Strong-pass-123")
        EmailAddress.objects.create(user=existing, email=existing.email, verified=True, primary=True)
        SocialApp.objects.create(provider="google", name="Google test", client_id="test", secret="test")
        sociallogin = self._social_login(existing.email)
        match = BeyondScreenSocialAccountAdapter().authenticate_by_email(sociallogin)
        self.assertIsNotNone(match)
        self.assertEqual(match[0], existing)
        self.assertEqual(User.objects.filter(email__iexact=existing.email).count(), 1)

        request = self._request()
        with request_context(request):
            sociallogin.lookup()
            sociallogin._accept_login(request)
        self.assertTrue(SocialAccount.objects.filter(user=existing, provider="google", uid="google-uid").exists())
        existing.refresh_from_db()
        self.assertTrue(existing.has_usable_password())

    def test_unverified_email_claim_never_links(self):
        existing = User.objects.create_user("existing-unverified", "claim@example.com", "Strong-pass-123")
        EmailAddress.objects.create(user=existing, email=existing.email, verified=True, primary=True)
        sociallogin = self._social_login(existing.email, verified=False)
        self.assertIsNone(BeyondScreenSocialAccountAdapter().authenticate_by_email(sociallogin))

    def test_cancel_and_failure_pages_are_safe(self):
        cancelled = self.client.get(reverse("socialaccount_login_cancelled"))
        failure = self.client.get(reverse("socialaccount_login_error"))
        self.assertContains(cancelled, "No account changes were made")
        self.assertContains(failure, "could not be completed", status_code=401)

    def test_password_login_registration_and_logout_remain_working(self):
        response = self.client.post(reverse("core:register"), {"username": "password-user", "email": "password@example.com", "password1": "Strong-pass-123", "password2": "Strong-pass-123"})
        self.assertRedirects(response, reverse("core:dashboard"))
        address = EmailAddress.objects.get(user__username="password-user", email="password@example.com")
        self.assertFalse(address.verified)
        self.client.get(reverse("core:logout"))
        response = self.client.post(reverse("core:login"), {"username": "password-user", "password": "Strong-pass-123"})
        self.assertRedirects(response, reverse("core:dashboard"))
        response = self.client.get(reverse("core:logout"))
        self.assertRedirects(response, reverse("core:home"))

    def test_social_user_cannot_access_another_users_report(self):
        google_user = User.objects.create_user("google-owner", "google@example.com")
        SocialAccount.objects.create(user=google_user, provider="google", uid="owner-uid")
        other = User.objects.create_user("other-owner", "other@example.com")
        summary = DigitalSummary.objects.create(user=other, screen_time_minutes=20, wellness_score=80, category="Balanced", insight="Private")
        self.client.force_login(google_user)
        self.assertEqual(self.client.get(reverse("core:view_summary", args=[summary.pk])).status_code, 404)
