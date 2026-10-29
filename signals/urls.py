from django.urls import path

from .views import SignalEligibilityView, SignalPurchasesView

app_name = "signals"

urlpatterns = [
    path("eligibility/", SignalEligibilityView.as_view(), name="eligibility"),
    path("purchases/", SignalPurchasesView.as_view(), name="purchases"),
]