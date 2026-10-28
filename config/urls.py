from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from django.http import JsonResponse

# JSON 404/500 for URLs that never reach DRF (active when DEBUG=False)
handler404 = "core.views.json_404"
handler500 = "core.views.json_500"

def root(request):
    return JsonResponse({"status": "ok", "service": "solvex-backend"})


urlpatterns = [
    path("", root, name="root"),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),                    # health
    path("api/auth/", include("authentication.urls")),

    # --- uncomment each pair as the app is built ---
    path("api/profile/", include("profiles.urls")),
    path("api/kyc/", include("kyc.urls")),
    path("api/wallet/", include("wallet.urls")),
    path("api/loans/", include("loan.urls")),
    path("api/referrals/", include("referral.urls")),
    path("api/trades/", include("trade.urls")),
    path("api/signals/", include("signals.urls")),

    # Cron (each app owns its cron_urls.py)
    path("api/cron/auth/", include("authentication.cron_urls")),
    path("api/cron/wallet/", include("wallet.cron_urls")),
    path("api/cron/loans/", include("loan.cron_urls")),

    # Docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]