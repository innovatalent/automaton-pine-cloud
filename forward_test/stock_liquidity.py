# -*- coding: utf-8 -*-
"""NIVELES DE LIQUIDEZ de ACCIONES (pedido usuario 2026-09-03: "conseguir pools de liquidez para acciones"). Los imanes
REALES de una acción NO están en el token de Bybit (perp fino) sino en el mercado subyacente (NASDAQ/NYSE): el OPEN INTEREST
de opciones por strike (muros de OI / gamma) + MAX PAIN + el perfil de volumen del subyacente. Fuente = yfinance (GRATIS,
ya instalado). Mapea el token (HOODUSDT) → ticker real (HOOD), agrega OI de los vencimientos cercanos, y devuelve los
niveles cargados ARRIBA y ABAJO del precio con su 'load' (OI) → los mejores targets/imanes. Cache 10min (yfinance es lento)."""
import time
import warnings
warnings.filterwarnings("ignore")

# token Bybit → ticker real US
STOCK_MAP = {
    "NVDAUSDT": "NVDA", "TSLAUSDT": "TSLA", "AAPLUSDT": "AAPL", "METAUSDT": "META", "MSTRUSDT": "MSTR",
    "COINUSDT": "COIN", "TQQQUSDT": "TQQQ", "SOXLUSDT": "SOXL", "SNDKUSDT": "SNDK", "PLTRUSDT": "PLTR",
    "AMDUSDT": "AMD", "GOOGLUSDT": "GOOGL", "AMZNUSDT": "AMZN", "MSFTUSDT": "MSFT", "HOODUSDT": "HOOD",
    "MUUSDT": "MU", "NFLXUSDT": "NFLX", "AVGOUSDT": "AVGO",
}
_CACHE = {}
_TTL = 600.0


def _max_pain(strikes, call_oi, put_oi):
    """Max pain = strike que MINIMIZA el valor total de las opciones ITM (donde más contratos expiran sin valor)."""
    best = None; best_pain = None
    for K in strikes:
        pain = 0.0
        for s in strikes:
            if s < K:
                pain += call_oi.get(s, 0) * (K - s)      # calls ITM
            elif s > K:
                pain += put_oi.get(s, 0) * (s - K)       # puts ITM
        if best_pain is None or pain < best_pain:
            best_pain = pain; best = K
    return best


def liquidity_levels(sym):
    """Niveles de liquidez (imanes de OI de opciones) para una acción. Dict con spot, max_pain, arriba/abajo (strikes con
    más OI + distancia %), magnet_up/down (el más cargado a cada lado), y load_above/below (OI total a cada lado). None si
    no es acción o falla. Los strikes con más OI = donde el precio tiende a ser imantado/pinneado (gamma)."""
    ticker = STOCK_MAP.get(sym)
    if not ticker:
        return None
    hit = _CACHE.get(sym)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    res = None
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        h = t.history(period="1d", interval="5m")
        spot = float(h["Close"].iloc[-1]) if len(h) else None
        exps = list(t.options or [])[:2]                 # 2 vencimientos más cercanos
        if not spot or not exps:
            raise ValueError("sin spot/opciones")
        oi = {}; call_oi = {}; put_oi = {}
        for e in exps:
            oc = t.option_chain(e)
            for df, store in ((oc.calls, call_oi), (oc.puts, put_oi)):
                for _, r in df.iterrows():
                    k = float(r["strike"]); o = int(r["openInterest"] or 0)
                    oi[k] = oi.get(k, 0) + o
                    store[k] = store.get(k, 0) + o
        strikes = sorted(oi)
        mp = _max_pain(strikes, call_oi, put_oi)
        above = sorted([(k, oi[k]) for k in strikes if k > spot], key=lambda x: -x[1])[:4]
        below = sorted([(k, oi[k]) for k in strikes if k < spot], key=lambda x: -x[1])[:4]
        load_above = sum(oi[k] for k in strikes if k > spot)
        load_below = sum(oi[k] for k in strikes if k < spot)
        def _fmt(lst):
            return [{"px": round(k, 2), "oi": v, "dist_pct": round((k - spot) / spot * 100, 2)} for k, v in lst]
        res = {"ticker": ticker, "spot": round(spot, 2), "max_pain": mp,
               "above": _fmt(above), "below": _fmt(below),
               "magnet_up": _fmt(above[:1])[0] if above else None,
               "magnet_down": _fmt(below[:1])[0] if below else None,
               "load_above": load_above, "load_below": load_below,
               "liq_bias": ("SHORT" if load_below > load_above * 1.15 else ("LONG" if load_above > load_below * 1.15 else None))}
    except Exception as e:
        res = None
    _CACHE[sym] = (time.time(), res)
    return res


if __name__ == "__main__":
    for s in ["HOODUSDT", "NVDAUSDT", "TSLAUSDT"]:
        r = liquidity_levels(s)
        if not r:
            print(s, "sin data"); continue
        print("\n=== %s (%s) spot=%.2f · max_pain=%s · sesgo OI=%s ==="% (s.replace("USDT",""), r["ticker"], r["spot"], r["max_pain"], r["liq_bias"]))
        print("  imanes ARRIBA (OI):", [(x["px"], x["oi"], "%+.1f%%"%x["dist_pct"]) for x in r["above"]])
        print("  imanes ABAJO  (OI):", [(x["px"], x["oi"], "%+.1f%%"%x["dist_pct"]) for x in r["below"]])
        print("  OI total arriba=%d vs abajo=%d"%(r["load_above"], r["load_below"]))
