"""
wallet/rates.py - live USD prices for the wallet assets (USDT, BTC, ETH).

Used for BOTH deposits (asset -> USD) and withdrawals (USD -> USDT).

All providers below are free and need NO API key. They are tried in order and each one only
fills the assets that earlier providers did not return, so a single dead provider never blocks
a request:

    1. coingecko      one request for all assets
    2. cryptocompare  one request for all assets
    3. coinbase       spot price, one request per asset
    4. kraken         one request for all assets
    5. bitstamp       ticker, one request per asset

Results are cached in Django's cache:

    RATE_CACHE_SECONDS      (default 30)   a quote younger than this is served without calling an API
    RATE_MAX_STALE_SECONDS  (default 300)  if every provider is down, the last good quote is still
                                           used for this long; after that callers get 503 rate_unavailable
    RATE_API_TIMEOUT        (default 4)    per-request timeout in seconds
    RATE_PROVIDERS          (optional)     list/tuple of provider names to change the order or drop some
    COINGECKO_API_KEY       (optional)     CoinGecko demo key, sent as x-cg-demo-api-key (not required)

All of these are optional settings (read with getattr), so nothing breaks if they are missing.
"""
import json
import logging
from datetime import datetime, timezone as dt_tz
from decimal import Decimal
from typing import NamedTuple, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

from core.exceptions import BusinessError

from .models import Asset

logger = logging.getLogger(__name__)

RATE_Q = Decimal("0.000001")

_COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
_COINGECKO_IDS = {Asset.USDT: "tether", Asset.BTC: "bitcoin", Asset.ETH: "ethereum"}

_CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/pricemulti"

_COINBASE_URL = "https://api.coinbase.com/v2/prices/{symbol}-USD/spot"

_KRAKEN_URL = "https://api.kraken.com/0/public/Ticker"
_KRAKEN_PAIRS = {Asset.USDT: "USDTUSD", Asset.BTC: "XBTUSD", Asset.ETH: "ETHUSD"}
# Kraken sometimes answers with its legacy key names, so accept both.
_KRAKEN_RESULT_KEYS = {
    Asset.USDT: ("USDTZUSD", "USDTUSD"),
    Asset.BTC: ("XXBTZUSD", "XBTUSD"),
    Asset.ETH: ("XETHZUSD", "ETHUSD"),
}

_BITSTAMP_URL = "https://www.bitstamp.net/api/v2/ticker/{symbol}usd/"

_CACHE_KEY = "wallet:usd_rate:{asset}"
_BACKOFF_KEY = "wallet:usd_rate:backoff"
_BACKOFF_SECONDS = 10  # after a failed refresh, don't hammer dead providers on every request


class Quote(NamedTuple):
    rate: Decimal          # USD per 1 unit of the asset
    fetched_at: datetime   # timezone-aware (UTC) time the price was fetched from the provider
    source: str = ""       # which provider served it (for logs / debugging)


# --------------------------------------------------------------------------- #
# Settings helpers
# --------------------------------------------------------------------------- #
def refresh_seconds() -> int:
    return int(getattr(settings, "RATE_CACHE_SECONDS", 30))


def _max_stale() -> int:
    return max(int(getattr(settings, "RATE_MAX_STALE_SECONDS", 300)), refresh_seconds())


def _timeout() -> float:
    return float(getattr(settings, "RATE_API_TIMEOUT", 4))


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _http_json(url, headers=None):
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "SolvexMarketInc/1.0", **(headers or {})},
    )
    with urlopen(request, timeout=_timeout()) as response:
        # Parse numbers as Decimal so prices never pass through binary floats.
        return json.loads(response.read().decode("utf-8"), parse_float=Decimal, parse_int=Decimal)


# --------------------------------------------------------------------------- #
# Providers: each takes a list of assets and returns {asset: Decimal USD price}.
# They may return fewer assets than asked for; they raise only if the whole call failed.
# --------------------------------------------------------------------------- #
def _from_coingecko(assets) -> dict:
    ids = ",".join(_COINGECKO_IDS[a] for a in assets)
    url = f"{_COINGECKO_URL}?{urlencode({'ids': ids, 'vs_currencies': 'usd'})}"
    key = getattr(settings, "COINGECKO_API_KEY", "")
    payload = _http_json(url, {"x-cg-demo-api-key": key} if key else None)
    found = {}
    for asset in assets:
        try:
            found[asset] = Decimal(str(payload[_COINGECKO_IDS[asset]]["usd"]))
        except Exception:
            continue
    return found


def _from_cryptocompare(assets) -> dict:
    symbols = ",".join(a.value.upper() for a in assets)
    url = f"{_CRYPTOCOMPARE_URL}?{urlencode({'fsyms': symbols, 'tsyms': 'USD'})}"
    payload = _http_json(url)
    found = {}
    for asset in assets:
        try:
            found[asset] = Decimal(str(payload[asset.value.upper()]["USD"]))
        except Exception:
            continue
    return found


def _from_coinbase(assets) -> dict:
    found = {}
    last_exc = None
    for asset in assets:
        try:
            payload = _http_json(_COINBASE_URL.format(symbol=asset.value.upper()))
            found[asset] = Decimal(str(payload["data"]["amount"]))
        except Exception as exc:
            last_exc = exc
    if not found and last_exc is not None:
        raise last_exc
    return found


def _from_kraken(assets) -> dict:
    pairs = ",".join(_KRAKEN_PAIRS[a] for a in assets)
    payload = _http_json(f"{_KRAKEN_URL}?{urlencode({'pair': pairs})}")
    result = payload.get("result") or {}
    found = {}
    for asset in assets:
        for key in _KRAKEN_RESULT_KEYS[asset]:
            if key in result:
                try:
                    found[asset] = Decimal(str(result[key]["c"][0]))  # last trade price
                except Exception:
                    pass
                break
    return found


def _from_bitstamp(assets) -> dict:
    found = {}
    last_exc = None
    for asset in assets:
        try:
            payload = _http_json(_BITSTAMP_URL.format(symbol=asset.value.lower()))
            found[asset] = Decimal(str(payload["last"]))
        except Exception as exc:
            last_exc = exc
    if not found and last_exc is not None:
        raise last_exc
    return found


_PROVIDERS = {
    "coingecko": _from_coingecko,
    "cryptocompare": _from_cryptocompare,
    "coinbase": _from_coinbase,
    "kraken": _from_kraken,
    "bitstamp": _from_bitstamp,
}
_DEFAULT_ORDER = ("coingecko", "cryptocompare", "coinbase", "kraken", "bitstamp")


def _provider_order():
    order = getattr(settings, "RATE_PROVIDERS", None) or _DEFAULT_ORDER
    return [name for name in order if name in _PROVIDERS]


def _valid(asset, rate) -> bool:
    if rate is None or rate <= 0:
        return False
    if asset == Asset.USDT and not (Decimal("0.5") < rate < Decimal("1.5")):
        return False  # a "USDT = $50" response is bad data, not a market move
    return True


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def _cached(asset) -> Optional[Quote]:
    data = cache.get(_CACHE_KEY.format(asset=asset.value))
    if not data:
        return None
    try:
        return Quote(
            Decimal(data["rate"]),
            datetime.fromisoformat(data["fetched_at"]),
            data.get("source", ""),
        )
    except Exception:
        return None


def _age(quote: Quote) -> float:
    return (datetime.now(dt_tz.utc) - quote.fetched_at).total_seconds()


def _refresh() -> bool:
    """Fetch every asset once (walking the providers in order) and store the results.
    Returns True if all assets were updated."""
    assets = list(Asset)
    found, source = {}, {}

    for name in _provider_order():
        missing = [a for a in assets if a not in found]
        if not missing:
            break
        try:
            got = _PROVIDERS[name](missing)
        except Exception as exc:
            logger.warning("Rate provider %s failed: %s", name, exc)
            continue
        for asset, rate in got.items():
            if asset in missing and _valid(asset, rate):
                found[asset] = rate
                source[asset] = name

    fetched_at = datetime.now(dt_tz.utc).isoformat()
    for asset, rate in found.items():
        cache.set(
            _CACHE_KEY.format(asset=asset.value),
            {"rate": format(rate.quantize(RATE_Q), "f"), "fetched_at": fetched_at, "source": source[asset]},
            timeout=_max_stale(),
        )

    ok = len(found) == len(assets)
    if not ok:
        missing_names = ", ".join(a.value for a in assets if a not in found)
        logger.warning("No provider returned a usable rate for: %s", missing_names)
        cache.set(_BACKOFF_KEY, 1, _BACKOFF_SECONDS)
    return ok


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def get_quote(asset) -> Quote:
    """Current USD price of 1 unit of `asset`. Raises 503 `rate_unavailable` if no usable price exists."""
    asset = Asset(asset)
    quote = _cached(asset)
    if quote is not None and _age(quote) <= refresh_seconds():
        return quote

    if not cache.get(_BACKOFF_KEY):
        _refresh()
        quote = _cached(asset)

    if quote is not None and _age(quote) <= _max_stale():
        return quote  # fresh, or the last good price while providers are down
    raise BusinessError(
        "rate_unavailable",
        f"The live {asset.label} rate is temporarily unavailable. Please try again in a moment.",
        status=503,
    )


def get_quote_or_none(asset) -> Optional[Quote]:
    try:
        return get_quote(asset)
    except BusinessError:
        return None