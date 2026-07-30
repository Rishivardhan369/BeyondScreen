from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("goals/new/", views.goal_onboarding, name="goal_onboarding"),
    path("goals/confirm/", views.goal_confirmation, name="goal_confirmation"),
    path("summary/", views.summary, name="summary"),
    path("postcard/download/<str:file_format>/", views.download_postcard, name="download_postcard"),
    # Dashboard
    path("dashboard/", views.dashboard, name="dashboard"),
    path("postcard/delete/<int:postcard_id>/", views.delete_postcard, name="delete_postcard"),
    path("postcard/view/<int:postcard_id>/", views.view_postcard, name="view_postcard"),
    # Profile
    path("profile/", views.profile, name="profile"),
    path("profile/edit/", views.edit_profile, name="edit_profile"),
    path("password/change/", views.change_password, name="change_password"),
    # Authentication
    path("login/", views.user_login, name="login"),
    path("register/", views.register, name="register"),
    path("logout/", views.user_logout, name="logout"),
    # History
    path("history/", views.history, name="history"),
    path("postcard-history/", views.postcard_history, name="postcard_history"),
    path("history/<int:summary_id>/", views.view_summary, name="view_summary"),
    path("postcard/download/<int:postcard_id>/<str:file_format>/", views.download_postcard_by_id, name="download_postcard_by_id"),
]
