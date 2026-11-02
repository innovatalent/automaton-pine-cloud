# -*- coding: utf-8 -*-
"""CONTEXTO DE MERCADO / CORRELACIONES (pedido usuario 2026-09-03): las cripto se deben correlacionar con BITCOIN + su
DOMINANCIA (matriz Cryptosharks), y los stocks con los índices (QQQ/SPY) + el dólar (DXY). Determina el SESGO de las altcoins
y el régimen risk-on/off para que el panel/alertas/bot operen a favor del contexto, no en contra. Cache 5min.

MATRIZ DOMINANCIA BTC (imagen del usuario):
  DOM↑ & BTC↑ → alts BAJAN (dinero a BTC)        DOM↓ & BTC↑ → ALTSEASON (alts SUBEN)
  DOM↑ & BTC↓ → ALTDUMP (alts bajan muy rápido)  DOM↓ & BTC↓ → alts lateral
  DOM↑ & BTC lateral → alts lateral (acumulación) DOM↓ & BTC lateral → alts SUBEN
Proxy de dominancia (sin API extra): BTC outperforma a la canasta de alts = dominancia SUBE; alts outperforman = BAJA."""
import json, time
import urllib.request as _ur

_CACHE = {"ts": 0.0, "data": None}
_TTL = 300.0
_ALTS = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT", "LTCUSDT"]
# stocks/ETF (proxy de índice tech) + oro como refugio
_STOCK_PROXY = ["TQQQUSDT", "NVDAUSDT", "MSFTUSDT", "AAPLUSDT", "METAUSDT", "AMZNUSDT"]
# COMMODITIES / metales — NO son altcoins: no se les aplica la matriz de dominancia BTC (correlacionan con el dólar/refugio,
# no con la rotación cripto). Se dejan NEUTRO hasta tener un feed de DXY.
_COMMODITIES = {"XAG", "XAU", "SILVER", "GOLD", "CL", "WTI", "OIL", "USOIL", "UKOIL", "NGAS", "COPPER", "PLATINUM", "XPT", "XPD"}
_STOCK_SET = {"TQQQ", "NVDA", "TSLA", "AAPL", "META", "MSTR", "COIN", "SOXL", "SNDK", "PLTR", "AMD", "GOOGL", "AMZN",
              "MSFT", "HOOD", "MU", "NFLX", "AVGO", "SKHYNIX"}


def _kl(sym, interval="60", limit=30):
    try:
        d = json.load(_ur.urlopen(_ur.Request(
            "https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=%s&limit=%d" % (sym, interval, limit),
            headers={"User-Agent": "x"}), timeout=10))
        L = list(reversed(d.get("result", {}).get("list", []) or []))
        return [float(r[4]) for r in L]
    except Exception:
        return None


def _ret(c, bars):
    if not c or len(c) < bars + 1:
        return None
    return (c[-1] - c[-1 - bars]) / c[-1 - bars] * 100.0


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    return (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2) if n else None


def _dir3(x, thr):
    return "SUBE" if (x is not None and x > thr) else ("BAJA" if (x is not None and x < -thr) else "LATERAL")


def _alt_matrix(dom, btc):
    """Devuelve (alt_bias LONG/SHORT/NEUTRO, efecto). Réplica de la matriz de la imagen."""
    if dom == "SUBE":
        if btc == "SUBE":
            return ("SHORT", "alts BAJAN (dinero rota a BTC)")
        if btc == "BAJA":
            return ("SHORT", "ALTDUMP (alts caen muy rápido)")
        return ("NEUTRO", "alts lateral (acumulación)")
    if dom == "BAJA":
        if btc == "SUBE":
            return ("LONG", "ALTSEASON (alts suben fuerte)")
        if btc == "BAJA":
            return ("NEUTRO", "alts lateral")
        return ("LONG", "alts suben (rotación desde BTC)")
    return ("NEUTRO", "dominancia lateral, sin sesgo claro")


_DXY_CACHE = {"ts": 0.0, "dir": None, "chg": None, "level": None}


def _dxy():
    """DXY (índice dólar, DX-Y.NYB vía yfinance). Devuelve (dir SUBE/BAJA/LATERAL, chg% ~24h, nivel). Cache 15min. El dólar es
    el driver macro de metales/petróleo/tech (correlaciones que pasó el usuario 2026-09-03). Fail-soft (dir None si falla)."""
    if time.time() - _DXY_CACHE["ts"] < 900 and _DXY_CACHE["dir"] is not None:
        return (_DXY_CACHE["dir"], _DXY_CACHE["chg"], _DXY_CACHE["level"])
    d, chg, lvl = None, None, None
    try:
        import yfinance as yf
        h = yf.Ticker("DX-Y.NYB").history(period="5d", interval="1h")
        c = [float(x) for x in h["Close"].dropna().tolist()]
        if len(c) >= 24:
            lvl = round(c[-1], 2)
            chg = round((c[-1] - c[-24]) / c[-24] * 100.0, 2)   # ~24h
            d = "SUBE" if chg > 0.25 else ("BAJA" if chg < -0.25 else "LATERAL")
    except Exception:
        d = None
    _DXY_CACHE.update({"ts": time.time(), "dir": d, "chg": chg, "level": lvl})
    return (d, chg, lvl)


def context():
    """Contexto de mercado. {btc_dir, btc_4h, btc_24h, dom_dir, dom_delta, alt_bias, alt_efecto, stock_regime, stock_avg,
    dxy_dir, dxy_chg, dxy_level}. El DXY (dólar) es el driver de metales/petróleo/tech (correlaciones del usuario)."""
    if _CACHE["data"] is not None and time.time() - _CACHE["ts"] < _TTL:
        return _CACHE["data"]
    out = {}
    try:
        kb = _kl("BTCUSDT", "60", 30)
        btc4 = _ret(kb, 4); btc24 = _ret(kb, 24)
        alt_rets = [_ret(_kl(s), 4) for s in _ALTS]
        alt_avg = _median(alt_rets)
        dom_delta = (btc4 - alt_avg) if (btc4 is not None and alt_avg is not None) else 0.0
        btc_dir = _dir3(btc4, 0.4)
        dom_dir = "SUBE" if dom_delta > 0.2 else ("BAJA" if dom_delta < -0.2 else "LATERAL")
        alt_bias, efecto = _alt_matrix(dom_dir, btc_dir)
        # stocks / índice tech (proxy) + risk regime
        st_rets = [_ret(_kl(s), 4) for s in _STOCK_PROXY]
        st_avg = _median(st_rets)
        stock_regime = _dir3(st_avg, 0.3)   # risk-on si el índice tech sube
        _dxd, _dxc, _dxl = _dxy()            # DÓLAR (driver macro de metales/petróleo/tech)
        # ★ INTERMARKET (usuario 2026-09-03): estado REAL de XAU/CL/tech vs lo que PREDICE el DXY (inverso) → confirma/DIVERGE.
        # Bibliografía (Murphy): divergencias = señal de giro / cambio de régimen. CL↑ con DXY↑ = SHOCK DE OFERTA (petróleo).
        _xau = _ret(_kl("XAUUSDT"), 4); _cl = _ret(_kl("CLUSDT"), 4)

        def _st8(a_ret, dxd):                # (dir del activo, 'confirma'/'DIVERGE'/'neutro' vs el dólar inverso, %4h)
            ad = _dir3(a_ret, 0.15)          # umbral bajo → movimientos chicos ya muestran dirección (verde/rojo)
            _p = round(a_ret, 2) if a_ret is not None else None
            if ad == "LATERAL" or not dxd or dxd == "LATERAL":
                return (ad, "neutro", _p)
            exp = "BAJA" if dxd == "SUBE" else "SUBE"   # el dólar mueve INVERSO a metal/petróleo/tech
            return (ad, "confirma" if ad == exp else "DIVERGE", _p)
        _xs = _st8(_xau, _dxd); _cs = _st8(_cl, _dxd); _ts = _st8(st_avg, _dxd)
        _inote = None                        # interpretación cross-asset (qué pasa con el resto), según la bibliografía
        if _dxd == "SUBE":
            if _cs[1] == "DIVERGE":
                _inote = "⚠ CL SUBE con dólar FUERTE = SHOCK DE OFERTA (geopolítica); CL↑ presiona TQQQ↓ y no frena a XAU"
            elif _ts[1] == "DIVERGE":
                _inote = "⚠ TQQQ SUBE pese al dólar fuerte = risk-on fuerte pero frágil (divergencia = posible giro); XAU/CL igual presionados"
            else:
                _inote = "dólar FUERTE domina → risk-OFF: XAU↓, CL↓, TQQQ↓ (correlación normal)"
        elif _dxd == "BAJA":
            if _xs[1] == "DIVERGE":
                _inote = "⚠ XAU NO sube con dólar débil = debilidad propia del oro (posible giro); TQQQ/CL sí favorecidos"
            elif _cs[1] == "DIVERGE":
                _inote = "⚠ CL NO sube con dólar débil = demanda floja (¿recesión?); XAU/TQQQ sí favorecidos"
            else:
                _inote = "dólar DÉBIL → risk-ON: XAU↑, CL↑, TQQQ↑ (exportadoras/commodities favorecidas)"
        out = {"btc_dir": btc_dir, "btc_4h": round(btc4, 2) if btc4 is not None else None,
               "btc_24h": round(btc24, 2) if btc24 is not None else None,
               "dom_dir": dom_dir, "dom_delta": round(dom_delta, 2), "alt_bias": alt_bias, "alt_efecto": efecto,
               "stock_regime": stock_regime, "stock_avg": round(st_avg, 2) if st_avg is not None else None,
               "dxy_dir": _dxd, "dxy_chg": _dxc, "dxy_level": _dxl,
               "intermarket": {"XAU": _xs, "CL": _cs, "tech": _ts, "note": _inote}}
    except Exception as e:
        out = {"err": str(e)[:80]}
    _CACHE["data"] = out; _CACHE["ts"] = time.time()
    return out


def bias_for(symbol):
    """Sesgo de contexto para un símbolo: cripto alt → alt_bias de la matriz; BTC → su propia dirección; stock → risk regime.
    Devuelve (bias LONG/SHORT/NEUTRO, nota)."""
    ctx = context()
    if not ctx or ctx.get("err"):
        return (None, None)
    s = symbol.replace("USDT", "").replace(".P", "")
    _dxy_dir = ctx.get("dxy_dir")
    _dxinv = {"SUBE": "SHORT", "BAJA": "LONG"}.get(_dxy_dir)   # dólar INVERSO (sube el dólar → baja el activo): metales/petróleo/tech
    _dxtxt = "dólar %s (%+.2f%%)" % (_dxy_dir, ctx.get("dxy_chg") or 0) if _dxy_dir else "dólar s/d"
    if symbol == "BTCUSDT":
        return ({"SUBE": "LONG", "BAJA": "SHORT"}.get(ctx["btc_dir"], "NEUTRO"), "BTC %s (%.2f%% 4h)" % (ctx["btc_dir"], ctx["btc_4h"] or 0))
    # ★ CORRELACIONES DXY (usuario 2026-09-03, investigó): el DÓLAR es el driver macro. Metales inverso FUERTE (−0.8),
    # petróleo inverso moderado (ojo shocks de oferta), tech/Nasdaq inverso (−0.42) + régimen del índice.
    if s in ("XAU", "XAG", "GOLD", "SILVER", "XPT", "XPD"):    # metales preciosos: inverso fuerte al dólar
        return (_dxinv, "%s → metal inverso (corr −0.8)" % _dxtxt) if _dxinv else (None, "dólar lateral → metal sin sesgo")
    if s in ("CL", "WTI", "OIL", "USOIL", "UKOIL", "NGAS"):    # petróleo/energía: inverso moderado, puede invertir por oferta
        return (_dxinv, "%s → petróleo inverso (ojo shocks de oferta)" % _dxtxt) if _dxinv else (None, "dólar lateral")
    if s in _STOCK_SET:                          # tech/Nasdaq: INVERSO al dólar (−0.42) + régimen del índice tech
        _reg = {"SUBE": "LONG", "BAJA": "SHORT"}.get(ctx["stock_regime"])
        if _dxinv and _reg and _dxinv == _reg:                 # confluencia dólar + índice = sesgo fuerte
            return (_reg, "índice tech %s + %s (confluyen)" % (ctx["stock_regime"], _dxtxt))
        if _reg:                                               # el régimen manda; el dólar matiza
            return (_reg, "índice tech %s (%s)" % (ctx["stock_regime"], _dxtxt))
        return (_dxinv, "%s → tech inverso" % _dxtxt) if _dxinv else (None, "sin sesgo claro")
    # cripto alt → matriz de dominancia BTC
    return (ctx["alt_bias"] if ctx["alt_bias"] != "NEUTRO" else None, ctx["alt_efecto"])


if __name__ == "__main__":
    c = context()
    print("=== CONTEXTO DE MERCADO ===")
    print("  BTC: %s (4h %.2f%% / 24h %.2f%%)" % (c.get("btc_dir"), c.get("btc_4h") or 0, c.get("btc_24h") or 0))
    print("  DOMINANCIA: %s (BTC vs alts 4h = %+.2f%%)" % (c.get("dom_dir"), c.get("dom_delta") or 0))
    print("  -> SESGO ALTCOINS: %s (%s)" % (c.get("alt_bias"), c.get("alt_efecto")))
    print("  STOCKS/indice tech: %s (%.2f%% 4h)" % (c.get("stock_regime"), c.get("stock_avg") or 0))
    for s in ["ETHUSDT", "SOLUSDT", "BTCUSDT", "TQQQUSDT"]:
        print("  bias_for(%s):" % s.replace("USDT", ""), bias_for(s))
