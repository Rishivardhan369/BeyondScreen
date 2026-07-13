from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("postcard/download/<str:file_format>/", views.download_postcard, name="download_postcard"),
]
