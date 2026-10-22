from django.urls import path

from .views import (
    ActiveTradesView,
    TradeDetailView,
    TradeListCreateView,
    TradeSummaryView,
    TradingConfigView,
)

urlpatterns = [
    path("", TradeListCreateView.as_view(), name="trade-list-create"),
    path("config/", TradingConfigView.as_view(), name="trade-config"),
    path("summary/", TradeSummaryView.as_view(), name="trade-summary"),
    path("active/", ActiveTradesView.as_view(), name="trade-active"),
    path("<str:trade_id>/", TradeDetailView.as_view(), name="trade-detail"),
]
