r"""
bitunix.py -- Fuente de datos Bitunix (publica) + selector de universo por CONDICIONES.

Universo = activos que CUMPLEN: rango amplio (24h) + alto volumen. Prioridad acciones/commodities,
luego cripto. Solo endpoints publicos (UA header). stdlib. Sin claves.
"""
import time
import json
import urllib.parse
import urllib.request

TICKERS_URL = "https://fapi.bitunix.com/api/v1/futures/market/tickers"
KLINE_URL = "https://fapi.bitunix.com/api/v1/futures/market/kline"
_UA = {"User-Agent": "Mozilla/5.0"}

# Bases de ACCIONES y COMMODITIES conocidas en Bitunix (para priorizar). Lo que no este aca = cripto.
EQUITY = {
    "TSLA", "NVDA", "AAPL", "AMZN", "META", "MSTR", "COIN", "GOOGL", "GOOG", "MSFT", "NFLX",
    "AMD", "PLTR", "HOOD", "GME", "SPX", "SPY", "QQQ", "SQQQ", "TQQQ", "DKNG", "BRKB", "BABA",
    "DIS", "INTC", "MU", "ARM", "SMCI", "AVGO", "CRM", "UBER", "ABNB", "PYPL", "SHOP", "SOFI",
    "RIVN", "LCID", "NIO", "MARA", "RIOT", "CRWD", "SNOW", "NKE", "BA", "JPM", "V", "MA",
    "WMT", "KO", "PEP", "XOM", "CVX", "GS", "AMC", "PFE", "ORCL", "ADBE", "QCOM",
    "SNDK", "SKHYNIX", "SKHY", "SPCX", "SAMSUNG", "CRCL", "BZ", "SOXL", "TSM", "ASML",
}
# OJO: NO poner tickers de 1 letra (T=AT&T, F=Ford) porque chocan con criptos homonimas
# (T=Threshold) y las mal-clasifican como accion. Se dejan fuera a proposito.
COMMODITY = {
    "XAU", "XAG", "GOLD", "SILVER", "OIL", "USOIL", "WTI", "BRENT", "NATGAS", "GAS", "UKOIL",
    "COPPER", "HG", "XPT", "XPD", "PLATINUM", "PALLADIUM", "UNG", "USO",
    "CL", "BZ", "NG", "XPD", "XCU",   # CL=crudo WTI, BZ=Brent, NG=natgas (evita que caigan como cripto)
}
# MEMECOINS a EXCLUIR (pedido usuario 2026-07-21: el dashboard NO opera memecoins).
# Se matchea la base (y sin prefijo numerico tipo 1000PEPE -> PEPE).
MEMECOIN = {
    "DOGE", "SHIB", "PEPE", "BONK", "WIF", "FLOKI", "PUMPFUN", "PUMP", "PENGU", "BOME", "MEW",
    "POPCAT", "MOG", "BRETT", "TURBO", "NEIRO", "GOAT", "PNUT", "ACT", "MOODENG", "FARTCOIN",
    "CHILLGUY", "CHILL", "PONKE", "MICHI", "BILLY", "WEN", "SLERF", "MYRO", "DEGEN", "TOSHI",
    "MUMU", "RETARDIO", "GIGA", "SPX6900", "WOJAK", "LADYS", "BABYDOGE", "ELON", "SNEK", "TROLL",
    "MELANIA", "TRUMP", "DADDY", "MOTHER", "ANDY", "APU", "HOPPY", "KEKIUS", "DOGS", "MEME",
    "BABYDOGE", "AIDOGE", "SAMO", "SUNDOG", "CAT", "MOODENGSOL", "HIPPO", "GME", "AI16Z",
}


def _is_meme(base):
    b = base[4:] if base.startswith("1000") else base
    return (base in MEMECOIN) or (b in MEMECOIN)


# TOP CRIPTO POR MARKET CAP (ordenado ~por mcap, pedido usuario 2026-07-21: "solo top 20 por market cap").
# Deja los establecidos y saca tokens nuevos volatiles (BANK/HEMI/AKE/DEXE...). Sin memes (DOGE/SHIB fuera).
# TAO se PINEA aparte (rank ~24, es el activo del usuario) para no cortarlo.
TOP_MCAP = [
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "TRX", "TON", "LINK", "AVAX",
    "DOT", "BCH", "SUI", "LTC", "NEAR", "UNI", "XLM", "APT", "ICP", "HBAR",
    "ATOM", "ETC", "FIL", "ARB", "OP", "INJ", "AAVE", "RENDER", "VET", "ONDO",
]
PIN_CRYPTO = ["TAO"]   # siempre incluidas si estan disponibles (activos del usuario)


_SUFFIX = ("USDT", "USDC", "USD")


def _base(sym):
    for suf in _SUFFIX:
        if sym.endswith(suf):
            return sym[:-len(suf)]
    return sym


def _cls(base):
    if base in EQUITY:
        return "accion"
    if base in COMMODITY:
        return "commodity"
    return "cripto"


_tc = {"d": None, "ts": 0.0}


def tickers():
    now = time.time()
    if _tc["d"] and now - _tc["ts"] < 15:
        return _tc["d"]
    try:
        req = urllib.request.Request(TICKERS_URL, headers=_UA)
        j = json.loads(urllib.request.urlopen(req, timeout=12).read().decode())
        _tc["d"] = j.get("data") or []
        _tc["ts"] = now
    except Exception:
        pass
    return _tc["d"] or []


def select_universe(max_total=28, n_priority=16, min_range=3.0, min_quotevol=0.0, max_range=30.0,
                    all_priority=False, crypto_top=None, exclude_meme=True, min_crypto_vol=5000000.0,
                    mcap_only=True, min_crypto_vol_mcap=1000000.0):
    """Devuelve (symbols, detalle).
    Modo clasico: min_range <= rango 24h <= max_range y quoteVol >= min_quotevol; prioriza acciones/commodities.
    Pedido usuario 2026-07-20:
      all_priority=True  -> TODAS las acciones y commodities disponibles (sin filtro de rango/volumen).
      crypto_top=N       -> solo las N criptos con mayor volumen operado (quoteVol)."""
    rows = []
    for t in tickers():
        sym = t.get("symbol", "")
        if not sym:
            continue
        try:
            hi = float(t.get("high") or 0)
            lo = float(t.get("low") or 0)
            qv = float(t.get("quoteVol") or 0)
            last = float(t.get("lastPrice") or t.get("last") or 0)
        except Exception:
            continue
        if lo <= 0 or last <= 0:
            continue
        rng = (hi - lo) / lo * 100.0
        rows.append({"symbol": sym, "base": _base(sym), "range": rng, "qv": qv,
                     "cls": _cls(_base(sym)), "last": last})
    eqcom = [r for r in rows if r["cls"] in ("accion", "commodity")]
    crall = [r for r in rows if r["cls"] == "cripto" and not (exclude_meme and _is_meme(r["base"]))
             and r["qv"] >= min_crypto_vol]
    if all_priority:
        # TODAS las acciones y commodities con dato valido (sin filtro rango/volumen)
        pri = sorted(eqcom, key=lambda r: -r["qv"])
    else:
        elig = [r for r in eqcom if min_range <= r["range"] <= max_range and r["qv"] >= min_quotevol]
        pri = sorted(elig, key=lambda r: -r["qv"])[:n_priority]
    if crypto_top is not None and mcap_only:
        # SOLO top-N cripto por MARKET CAP (lista curada TOP_MCAP + pineadas), 1 mercado por base.
        # Piso de volumen MENOR (whitelist ya garantiza calidad; en Bitunix los large-caps tienen poco vol).
        crm = [r for r in rows if r["cls"] == "cripto" and not (exclude_meme and _is_meme(r["base"]))
               and r["qv"] >= min_crypto_vol_mcap]
        bydict = {}
        for r in sorted(crm, key=lambda r: -r["qv"]):   # mejor mercado (mayor vol) por base
            bydict.setdefault(r["base"], r)
        cry = []
        used = set()
        for base in PIN_CRYPTO + [b for b in TOP_MCAP if b not in PIN_CRYPTO]:
            if base in bydict and base not in used:
                cry.append(bydict[base]); used.add(base)
                if len(cry) >= crypto_top:
                    break
        uni = pri + cry
    elif crypto_top is not None:
        # top N criptos por volumen operado (quoteVol), 1 mercado por base (evita BTCUSDT+BTCUSDC dup)
        seen = set()
        cry = []
        for r in sorted(crall, key=lambda r: -r["qv"]):
            if r["base"] in seen:
                continue
            seen.add(r["base"])
            cry.append(r)
            if len(cry) >= crypto_top:
                break
        uni = pri + cry
    else:
        elig_c = [r for r in crall if min_range <= r["range"] <= max_range and r["qv"] >= min_quotevol]
        cry = sorted(elig_c, key=lambda r: -r["qv"])
        uni = pri[:n_priority]
        uni += cry[:max(0, max_total - len(uni))]
    return [r["symbol"] for r in uni], uni


def klines(symbol, interval="5m", limit=500):
    """Klines publicas Bitunix -> [[ms,o,h,l,c,v],...] SOLO velas cerradas (descarta la ultima)."""
    try:
        req = urllib.request.Request(
            KLINE_URL + "?" + urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": limit}),
            headers=_UA)
        j = json.loads(urllib.request.urlopen(req, timeout=12).read().decode())
        if j.get("code") != 0 or not j.get("data"):
            return []
        out = []
        for k in j["data"]:
            try:
                out.append([int(k["time"]), float(k["open"]), float(k["high"]),
                            float(k["low"]), float(k["close"]), float(k.get("baseVol", 0) or 0)])
            except Exception:
                continue
        out.sort(key=lambda x: x[0])
        return out[:-1]
    except Exception:
        return []


BYBIT_KLINE = "https://api.bybit.com/v5/market/kline"
_BYBIT_IV = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}


def klines_bybit(symbol, interval="5m", limit=500):
    """Klines publicas BYBIT v5 (linear perp) -> [[ms,o,h,l,c,v],...] SOLO velas cerradas (descarta la
    ultima). Misma fuente que el grafico del usuario en TradingView (para que el CVD/divergencia calce)."""
    iv = _BYBIT_IV.get(interval, str(interval).replace("m", ""))
    try:
        req = urllib.request.Request(
            BYBIT_KLINE + "?" + urllib.parse.urlencode(
                {"category": "linear", "symbol": symbol, "interval": iv, "limit": min(1000, limit)}),
            headers=_UA)
        j = json.loads(urllib.request.urlopen(req, timeout=12).read().decode())
        if j.get("retCode") != 0:
            return []
        lst = (j.get("result") or {}).get("list") or []
        out = []
        for k in lst:
            try:
                out.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
            except Exception:
                continue
        out.sort(key=lambda x: x[0])   # Bybit devuelve newest-first -> ordenar oldest->newest
        return out[:-1]                # descarta la vela en formacion
    except Exception:
        return []


def klines_smart(symbol, interval="5m", limit=500):
    """Fuente de klines para el DETECTOR DE DIVERGENCIAS: BYBIT para cripto (= lo que el usuario ve en
    TradingView), Bitunix para acciones/commodities (Bybit no las lista). Fallback a Bitunix si Bybit
    falla o trae pocas velas."""
    if _cls(_base(symbol)) == "cripto":
        kb = klines_bybit(symbol, interval, limit)
        if len(kb) >= 60:
            return kb
    return klines(symbol, interval, limit)


def prices(symbols):
    m = {t.get("symbol"): t for t in tickers()}
    out = {}
    for s in symbols:
        t = m.get(s)
        if not t:
            continue
        try:
            out[s] = float(t.get("lastPrice") or t.get("last"))
        except Exception:
            pass
    return out


def ticker(symbol):
    for t in tickers():
        if t.get("symbol") == symbol:
            return t
    return {}


DEPTH_URL = "https://fapi.bitunix.com/api/v1/futures/market/depth"


def depth(symbol, limit=50):
    """Order book publico -> (bids, asks) como [[precio,size],...]. Para muros de liquidez."""
    try:
        req = urllib.request.Request(
            DEPTH_URL + "?" + urllib.parse.urlencode({"symbol": symbol, "limit": limit}), headers=_UA)
        j = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
        d = j.get("data") or {}
        bids = [[float(p), float(s)] for p, s in (d.get("bids") or [])]
        asks = [[float(p), float(s)] for p, s in (d.get("asks") or [])]
        return bids, asks
    except Exception:
        return [], []


if __name__ == "__main__":
    syms, det = select_universe()
    print("universo (%d):" % len(syms))
    for r in det:
        print("  %-14s %-10s rango=%.1f%%  vol=%.4g" % (r["symbol"], r["cls"], r["range"], r["qv"]))
    # test klines + compat con cvd_strategy
    import cvd_strategy as cs
    for s in syms[:3]:
        kl = klines(s)
        r = cs.analyze(kl) if kl else None
        print("  %-14s velas=%s analyze=%s" % (s, len(kl) if kl else 0, (r and (r["divergence"] or r["entry"])) if r else "sin datos"))
