from django.urls import path

from .views import SignalEligibilityView

app_name = "signals"

urlpatterns = [
    path("eligibility/", SignalEligibilityView.as_view(), name="eligibility"),
]