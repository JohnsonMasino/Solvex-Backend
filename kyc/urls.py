from django.urls import path

from .views import (
    HistoryView, OptionsView, PrefillView, StatusView, SubmissionDetailView, SubmitView,
)

app_name = "kyc"

urlpatterns = [
    path("options/", OptionsView.as_view(), name="options"),
    path("status/", StatusView.as_view(), name="status"),
    path("prefill/", PrefillView.as_view(), name="prefill"),
    path("submit/", SubmitView.as_view(), name="submit"),
    path("submission/", SubmissionDetailView.as_view(), name="submission"),
    path("history/", HistoryView.as_view(), name="history"),
]
