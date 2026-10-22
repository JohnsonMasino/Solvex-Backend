from django.urls import path

from referral import views

urlpatterns = [
    path("", views.ReferralDashboardView.as_view(), name="referral-dashboard"),
    path("list/", views.ReferralListView.as_view(), name="referral-list"),
    path("earnings/", views.ReferralEarningsView.as_view(), name="referral-earnings"),
    path("validate/", views.ValidateReferralCodeView.as_view(), name="referral-validate"),
]
