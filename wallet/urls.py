from django.urls import path

from .views import (
    DepositCancelView, DepositDetailView, DepositListCreateView, TransactionListView,
    WalletConfigView, WalletSummaryView, WithdrawalCancelView, WithdrawalConfirmView,
    WithdrawalDetailView, WithdrawalListCreateView, WithdrawalResendOTPView,
)

app_name = "wallet"

urlpatterns = [
    path("", WalletSummaryView.as_view(), name="summary"),
    path("config/", WalletConfigView.as_view(), name="config"),
    path("deposits/", DepositListCreateView.as_view(), name="deposits"),
    path("deposits/<uuid:pk>/", DepositDetailView.as_view(), name="deposit-detail"),
    path("deposits/<uuid:pk>/cancel/", DepositCancelView.as_view(), name="deposit-cancel"),
    path("withdrawals/", WithdrawalListCreateView.as_view(), name="withdrawals"),
    path("withdrawals/<uuid:pk>/", WithdrawalDetailView.as_view(), name="withdrawal-detail"),
    path("withdrawals/<uuid:pk>/confirm/", WithdrawalConfirmView.as_view(), name="withdrawal-confirm"),
    path("withdrawals/<uuid:pk>/resend-otp/", WithdrawalResendOTPView.as_view(), name="withdrawal-resend-otp"),
    path("withdrawals/<uuid:pk>/cancel/", WithdrawalCancelView.as_view(), name="withdrawal-cancel"),
    path("transactions/", TransactionListView.as_view(), name="transactions"),
]
