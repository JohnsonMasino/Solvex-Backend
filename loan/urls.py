from django.urls import path

from .views import (
    LoanCancelView, LoanConfigView, LoanCurrentView, LoanDetailView, LoanEligibilityView,
    LoanListCreateView, LoanRepayView,
)

app_name = "loan"

urlpatterns = [
    path("config/", LoanConfigView.as_view(), name="config"),
    path("eligibility/", LoanEligibilityView.as_view(), name="eligibility"),
    path("current/", LoanCurrentView.as_view(), name="current"),
    path("", LoanListCreateView.as_view(), name="loans"),
    path("<uuid:pk>/", LoanDetailView.as_view(), name="loan-detail"),
    path("<uuid:pk>/cancel/", LoanCancelView.as_view(), name="loan-cancel"),
    path("<uuid:pk>/repay/", LoanRepayView.as_view(), name="loan-repay"),
]