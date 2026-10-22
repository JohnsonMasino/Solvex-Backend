from django.urls import path

from .views import CloseExpiredTradesView, CloseWeekendTradesView

urlpatterns = [
    path("close-expired/", CloseExpiredTradesView.as_view(), name="cron-trades-close-expired"),
    path("close-weekend/", CloseWeekendTradesView.as_view(), name="cron-trades-close-weekend"),
]
