from django.urls import path

from . import views

urlpatterns = [
    path("cleanup/", views.CleanupCronView.as_view(), name="cron-auth-cleanup"),
]
