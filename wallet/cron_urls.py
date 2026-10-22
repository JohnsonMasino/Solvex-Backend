from django.urls import path

from .views import ExpirePendingWithdrawalsView

urlpatterns = [
    path("expire-pending/", ExpirePendingWithdrawalsView.as_view(), name="cron-wallet-expire-pending"),
]
