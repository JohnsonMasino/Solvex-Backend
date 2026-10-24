from django.urls import path

from .views import ProcessLoanRemindersView

urlpatterns = [
    path("process-reminders/", ProcessLoanRemindersView.as_view(), name="cron-loan-process-reminders"),
]