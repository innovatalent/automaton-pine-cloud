# -*- coding: utf-8 -*-
"""FILTRO DIRECCIONAL POR HTF (2026-08-07, VALIDADO en market_history) — SIN API KEY (klines públicas Bybit).

Hallazgo validado (retorno 48h, net direccional):
  - FADEAR una extensión de 12h que corrió A FAVOR del HTF de 72h (blowoff/agotamiento) = +0.525% WR57%  -> el swing ganador
  - Rango HTF (flat): fadear extensiones leves = +0.152% WR53%
  - Extensión CONTRA el HTF (pullback): perseguirla/fadear-con-HTF = -0.315% WR46%  -> BLOQUEAR

Regla operativa: la DIRECCIÓN FAVORECIDA es fadear la extensión (reversión hacia la liquidez), SALVO cuando la extensión
va contra el HTF (ahí no hay edge -> bloquear ambos lados). Bloquea perseguir el movimiento (lo que muere).

favored_dir(symbol) -> {"favored": "LONG"/"SHORT"/None, "block": bool, "ext": %, "htf": %, "regime": str}
htf_blocks(symbol, side) -> (True/False, motivo). Cache 15min (el HTF se mueve lento). DISCIPLINA: validado OOS.
"""
import json
import time
import urllib.request

_UA = {"User-Agent": "mozilla/5.0"}
_BYBIT = "https://api.bybit.com"
_cache = {}
_TTL = 900.0

EXT_MIN = 0.8      # |mov 12h| >= 0.8% = extensión clara (lo que se fadea)
HTF_TREND = 3.0    # |mov 72h| >= 3% = HTF en tendencia (si no, rango)


def _klines_1h(sym, limit=74):
    try:
        u = "%s/v5/market/kline?category=linear&symbol=%s&interval=60&limit=%d" % (_BYBIT, sym, limit)
        d = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=_UA), timeout=10).read().decode())
        if d.get("retCode") == 0:
            # list viene DESC (más nuevo primero); close = idx 4
            return [float(k[4]) for k in d["result"]["list"]]
    except Exception:
        pass
    return None


def favored_dir(symbol):
    now = time.time()
    c = _cache.get(symbol)
    if c and now - c[1] < _TTL:
        return c[0]
    kl = _klines_1h(symbol)
    res = {"favored": None, "block": False, "ext": None, "htf": None, "regime": "sin_datos"}
    if kl and len(kl) >= 73:
        px = kl[0]                       # close más reciente
        p12 = kl[12] if len(kl) > 12 else px
        p72 = kl[72] if len(kl) > 72 else px
        ext = (px - p12) / p12 * 100.0 if p12 else 0.0
        htf = (px - p72) / p72 * 100.0 if p72 else 0.0
        res["ext"] = round(ext, 2); res["htf"] = round(htf, 2)
        if abs(ext) >= EXT_MIN:
            fade = "SHORT" if ext > 0 else "LONG"          # fadear la extensión (reversión)
            if abs(htf) < HTF_TREND:
                res["favored"] = fade; res["regime"] = "rango(fade leve)"
            elif (ext > 0) == (htf > 0):                    # extensión A FAVOR del HTF = blowoff -> fade fuerte
                res["favored"] = fade; res["regime"] = "blowoff(fade fuerte)"
            else:                                           # extensión CONTRA el HTF (pullback) -> sin edge -> bloquear
                res["block"] = True; res["regime"] = "pullback-contra-HTF(block)"
    _cache[symbol] = (res, now)
    return res


def htf_blocks(symbol, side):
    """True si el HTF dice NO operar ese lado. Fail-open (sin datos -> no bloquea)."""
    try:
        d = favored_dir(symbol)
    except Exception:
        return False, ""
    if d.get("block"):
        return True, "pullback contra-HTF (ext %.1f%% vs htf %.1f%%, sin edge)" % (d.get("ext") or 0, d.get("htf") or 0)
    fav = d.get("favored")
    if fav and side != fav:
        return True, "contra la reversion HTF (favorecido %s: %s; ext %.1f%%/htf %.1f%%)" % (
            fav, d.get("regime"), d.get("ext") or 0, d.get("htf") or 0)
    return False, ""


if __name__ == "__main__":
    for s in ["BTCUSDT", "SOLUSDT", "TAOUSDT", "DOGEUSDT", "XRPUSDT", "SUIUSDT"]:
        print("%-10s %s" % (s, favored_dir(s)))
