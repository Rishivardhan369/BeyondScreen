from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase


class ToastTemplateTests(TestCase):
    def test_base_template_renders_success_message_as_toast(self):
        request = RequestFactory().get("/")
        SessionMiddleware(lambda response: response).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        request._messages.add(level=25, message="Test message")

        rendered = render_to_string(
            "base.html",
            {"messages": list(get_messages(request))},
            request=request,
        )

        self.assertIn("toast-container", rendered)
        self.assertIn("toast-success", rendered)
        self.assertIn("Test message", rendered)
