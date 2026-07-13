from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .services import generate_postcard


class HomeViewTests(TestCase):
    def test_home_page_loads_all_available_choices(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Screen time today")
        self.assertContains(response, "Calm")
        self.assertContains(response, "Be more present")

    def test_valid_submission_creates_postcard_and_downloads(self):
        response = self.client.post(
            reverse("core:home"),
            {"screen_time": 245, "mood": "Tired", "goal": "Presence"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Here’s a note for")
        self.assertContains(response, "4h 05m")
        self.assertContains(response, "Download PNG")
        self.assertContains(response, "Copy entire postcard")

        png_response = self.client.get(reverse("core:download_postcard", args=["png"]))
        self.assertEqual(png_response.status_code, 200)
        self.assertEqual(png_response["Content-Type"], "image/png")
        self.assertTrue(png_response.content.startswith(b"\x89PNG"))

        pdf_response = self.client.get(reverse("core:download_postcard", args=["pdf"]))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))

    def test_invalid_submission_shows_errors(self):
        response = self.client.post(reverse("core:home"), {"mood": "", "goal": ""})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")

    def test_unsupported_upload_type_shows_error(self):
        response = self.client.post(
            reverse("core:home"),
            {
                "file": SimpleUploadedFile("report.exe", b"not a report"),
                "mood": "Happy",
                "goal": "Study",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a CSV, PDF, PNG, JPG, or text file.")

    def test_download_requires_a_generated_postcard(self):
        response = self.client.get(reverse("core:download_postcard", args=["png"]))

        self.assertEqual(response.status_code, 404)


class PostcardEngineTests(TestCase):
    def test_engine_has_required_content_variety(self):
        moods = ("Happy", "Calm", "Neutral", "Stressed", "Tired")
        goals = ("Study", "Fitness", "Better Sleep", "Productivity", "Presence")
        reflections = {
            generate_postcard(mood=mood, goal=goal, screen_time=None, has_report=has_report)["reflection"]
            for mood in moods
            for goal in goals
            for has_report in (False, True)
        }
        postcards = [
            generate_postcard(mood="Calm", goal="Study", screen_time=None, has_report=False)
            for _ in range(100)
        ]

        self.assertEqual(len(reflections), 50)
        self.assertEqual(len({postcard["haiku"] for postcard in postcards}), 100)
        self.assertEqual(len({postcard["action"] for postcard in postcards}), 100)
        self.assertEqual(len({postcard["pledge"] for postcard in postcards}), 100)
