# -*- coding: utf-8 -*-
"""CATALIZADORES (2026-08-07, pedido usuario "complementa con las APIs") — señal direccional/contexto por evento.

La dirección "a favor" no está en el precio (validado toda la sesión). El catalizador es la ÚNICA info nueva que puede
darla: un earnings, un unlock, un listing, una noticia MUEVEN el precio de un lado. Este módulo la junta.

FUENTES (lee las keys de Desktop\\data_keys.json; usa solo las que estén cargadas):
  - Finnhub (STOCKS): calendario de earnings + noticias por empresa/mercado.  [key: finnhub]  ← cargada
  - CoinMarketCal (CRIPTO): eventos con fecha (unlocks/listings/mainnet).      [key: coinmarketcal]  ← pendiente
  - CryptoPanic (CRIPTO): noticias con sentimiento por moneda.                 [key: cryptopanic]  ← pendiente

DISCIPLINA: se RECORDA y VALIDA forward antes de gatear (no cablear a ciegas). Cache generoso (los eventos cambian lento).
"""
import os
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta

_UA = {"User-Agent": "mozilla/5.0"}
_KEYS = {"data": None, "ts": 0.0}
_cache = {}

STOCKS = set("SKHYNIX SKHY SOXL SMCI MSTR COIN META GOOGL MU AAPL SNDK TSLA AMD NVDA SQQQ INTC TQQQ AMZN AVGO HOOD "
             "TSM ASML QQQ MSFT SPY QCOM ORCL PLTR SNOW BABA NFLX UBER JPM DIS RIVN DKNG CVX WMT SOFI ARM MRVL "
             "CRM ADBE V GS PYPL".split())
_KEYS_PATHS = [r"C:\Users\asusr\OneDrive\Desktop\data_keys.json",
               os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_keys.json")]


def _keys():
    now = time.time()
    if _KEYS["data"] is not None and now - _KEYS["ts"] < 300:
        return _KEYS["data"]
    d = {}
    for p in _KEYS_PATHS:
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            d = {k: v for k, v in raw.items() if not k.startswith("_") and v}
            break
        except Exception:
            continue
    _KEYS["data"] = d; _KEYS["ts"] = now
    return d


def _get(url):
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=12).read().decode())
    except Exception:
        return None


def _base(sym):
    return sym.replace("USDT", "")


# ---------------- Finnhub (stocks) ----------------
_earn = {"data": None, "ts": 0.0}


def _earnings_map():
    """{símbolo: fecha_earnings} para los próximos 10 días. Cache 6h."""
    now = time.time()
    if _earn["data"] is not None and now - _earn["ts"] < 6 * 3600:
        return _earn["data"]
    k = _keys().get("finnhub")
    m = {}
    if k:
        a = datetime.now(timezone.utc).date()
        b = a + timedelta(days=10)
        d = _get("https://finnhub.io/api/v1/calendar/earnings?from=%s&to=%s&token=%s" % (a, b, k))
        if isinstance(d, dict):
            for e in d.get("earningsCalendar", []):
                s = e.get("symbol"); dt = e.get("date")
                if s and dt and s not in m:
                    m[s] = dt
    _earn["data"] = m; _earn["ts"] = now
    return m


def _finnhub_news(base):
    k = _keys().get("finnhub")
    if not k:
        return 0
    a = (datetime.now(timezone.utc).date() - timedelta(days=2))
    b = datetime.now(timezone.utc).date()
    d = _get("https://finnhub.io/api/v1/company-news?symbol=%s&from=%s&to=%s&token=%s" % (base, a, b, k))
    return len(d) if isinstance(d, list) else 0


# ---------------- CRIPTO: noticias por RSS (SIN key, reemplaza CryptoPanic discontinuado) ----------------
_RSS = ["https://cointelegraph.com/rss", "https://www.coindesk.com/arc/outboundfeeds/rss/", "https://decrypt.co/feed"]
# nombre -> ticker base (para detectar menciones en titulares)
_NAMES = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP", "cardano": "ADA", "dogecoin": "DOGE",
          "avalanche": "AVAX", "chainlink": "LINK", "polkadot": "DOT", "polygon": "MATIC", "litecoin": "LTC",
          "shiba": "SHIB", "tron": "TRX", "cosmos": "ATOM", "aptos": "APT", "arbitrum": "ARB", "optimism": "OP",
          "sui": "SUI", "sei": "SEI", "injective": "INJ", "bittensor": "TAO", "near": "NEAR", "hedera": "HBAR",
          "uniswap": "UNI", "worldcoin": "WLD", "kaito": "KAITO", "pepe": "PEPE"}
_news_cache = {"data": None, "ts": 0.0}


def _crypto_news_counts():
    """{TICKER: nº de titulares recientes que lo mencionan} desde RSS. Cache 15min. Sin key."""
    import re
    now = time.time()
    if _news_cache["data"] is not None and now - _news_cache["ts"] < 900:
        return _news_cache["data"]
    heads = []
    for u in _RSS:
        try:
            raw = urllib.request.urlopen(urllib.request.Request(u, headers=_UA), timeout=10).read().decode(errors="replace")
            heads += re.findall(r"<title>(.*?)</title>", raw)[1:30]
        except Exception:
            continue
    counts = {}
    for h in heads:
        low = h.lower()
        found = set()
        for name, tk in _NAMES.items():
            if name in low:
                found.add(tk)
        for tk in re.findall(r"\b([A-Z]{2,6})\b", h):        # tickers en mayúscula del titular
            found.add(tk)
        for tk in found:
            counts[tk] = counts.get(tk, 0) + 1
    _news_cache["data"] = counts; _news_cache["ts"] = now
    return counts


def catalyst(symbol):
    """Señal de catalizador para el símbolo. {has_catalyst, earnings_in_days, news_2d, note}. Cache 1h."""
    now = time.time()
    c = _cache.get(symbol)
    if c and now - c[1] < 3600:
        return c[0]
    base = _base(symbol)
    res = {"has_catalyst": False, "earnings_in_days": None, "news_2d": 0, "note": ""}
    if base in STOCKS:
        em = _earnings_map()
        if base in em:
            try:
                days = (datetime.fromisoformat(em[base]).date() - datetime.now(timezone.utc).date()).days
                res["earnings_in_days"] = days
                if 0 <= days <= 3:
                    res["has_catalyst"] = True; res["note"] = "earnings en %dd (evento binario)" % days
            except Exception:
                pass
        res["news_2d"] = _finnhub_news(base)
        if res["news_2d"] >= 8:
            res["has_catalyst"] = True; res["note"] = (res["note"] + " · " if res["note"] else "") + "%d noticias 2d" % res["news_2d"]
    else:
        # CRIPTO: eventos CoinMarketCal (unlocks/listings) + menciones en titulares RSS
        cnt = _crypto_news_counts().get(base, 0)
        res["news_2d"] = cnt
        ev = _cmc_events().get(base)
        if ev is not None and 0 <= ev <= 7:
            res["has_catalyst"] = True; res["event_in_days"] = ev; res["note"] = "evento CMC en %dd" % ev
        if cnt >= 2:
            res["has_catalyst"] = True
            res["note"] = (res["note"] + " · " if res["note"] else "") + "%d menciones noticias" % cnt
    _cache[symbol] = (res, now)
    return res


# ---------------- CoinMarketCal v2 (eventos cripto: unlocks/listings/mainnet) ----------------
_cmc = {"data": None, "ts": 0.0}


def _cmc_events():
    """{TICKER: dias_al_evento} de CoinMarketCal v2. Best-effort (tier gratis limitado / rate-limit). Cache 3h."""
    now = time.time()
    if _cmc["data"] is not None and now - _cmc["ts"] < 3 * 3600:
        return _cmc["data"]
    import zlib
    k = _keys().get("coinmarketcal")
    m = {}
    if k:
        a = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        b = (datetime.now(timezone.utc) + timedelta(days=21)).strftime("%Y-%m-%d")
        u = "https://api.coinmarketcal.com/v2/events?dateRangeStart=%s&dateRangeEnd=%s&max=75" % (a, b)
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                u, headers={"x-api-key": k, "Accept": "application/json", "Accept-Encoding": "deflate, gzip"}), timeout=12)
            raw = r.read()
            for f in (lambda x: zlib.decompress(x), lambda x: zlib.decompress(x, -15), lambda x: __import__("gzip").decompress(x), lambda x: x):
                try:
                    raw = f(raw); break
                except Exception:
                    continue
            for e in (json.loads(raw.decode(errors="replace")).get("body", []) or []):
                try:
                    days = (datetime.fromisoformat(str(e.get("date_event"))[:10]).date() - datetime.now(timezone.utc).date()).days
                except Exception:
                    continue
                for co in (e.get("coins") or []):
                    s = (co.get("symbol") or "").upper()
                    if s and (s not in m or days < m[s]):
                        m[s] = days
        except Exception:
            pass
    _cmc["data"] = m; _cmc["ts"] = now
    return m


EARN_BLOCK_DAYS = 2   # no abrir swing de acción con earnings en <=2 días (evento binario impredecible)


def earnings_block(symbol):
    """True si es acción con earnings inminente (<=EARN_BLOCK_DAYS d) -> NO abrir (riesgo binario). Fail-open. Cache 6h."""
    base = _base(symbol)
    if base not in STOCKS:
        return False, ""
    try:
        em = _earnings_map()
        if base in em:
            days = (datetime.fromisoformat(em[base]).date() - datetime.now(timezone.utc).date()).days
            if 0 <= days <= EARN_BLOCK_DAYS:
                return True, "earnings en %dd (evento binario, no swing)" % days
    except Exception:
        pass
    return False, ""


if __name__ == "__main__":
    print("keys cargadas:", list(_keys().keys()))
    print("earnings_block NVDA:", earnings_block("NVDAUSDT"))
    print("cmc eventos:", len(_cmc_events()))
    for s in ["NVDAUSDT", "AMDUSDT", "COINUSDT", "BTCUSDT", "SOLUSDT"]:
        print("%-10s %s" % (s, catalyst(s)))
