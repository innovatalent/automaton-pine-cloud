# -*- coding: utf-8 -*-
"""CAPA ② POSICIONAMIENTO — "fade la multitud" (informe 2026-09-03, la pieza que la literatura marca como el COMBUSTIBLE).
Bibliografía: funding extremo precede reversiones (Granger); rising OI + funding alto = posiciones frágiles → short-squeeze;
long/short ratio en extremo = multitud offside. Datos PÚBLICOS de Bybit (funding/OI/account-ratio, sin key). Cache 5min.

Devuelve el lado a FADEAR (contra la multitud) + fuerza. NO es predicción directa — es el combustible que confirma un fade en
un LUGAR (①) dentro del CONTEXTO (④), gatillado por ③. Fail-soft (None si falla)."""
import json, time
import urllib.request as _ur

_CACHE = {}
_TTL = 300.0
# umbrales (funding Bybit = por 8h; baseline ~+0.01%). Extremos relativos.
FUND_HI = 0.0004      # funding > +0.04%/8h = longs muy crowded (pagan caro) → fade SHORT
FUND_LO = -0.0001     # funding < 0 = shorts crowded (pagan) → fade LONG
LS_HI = 0.62          # buyRatio > 0.62 = multitud LONG
LS_LO = 0.42          # buyRatio < 0.42 = multitud SHORT (relativo: retail suele ser net-long)


def _get(url):
    return json.load(_ur.urlopen(_ur.Request(url, headers={"User-Agent": "x"}), timeout=10))


def positioning(symbol):
    """{crowd: LONG/SHORT/None (a dónde está la multitud), fade_side: SHORT/LONG/None (a favor, contra la multitud),
    funding_pct, ls_buy, oi_trend_pct, strength 0-3, note}. strength = cuántas señales alinean (funding+ls+oi frágil)."""
    now = time.time(); hit = _CACHE.get(symbol)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    out = None
    try:
        fr = None
        try:
            f = _get("https://api.bybit.com/v5/market/funding/history?category=linear&symbol=%s&limit=1" % symbol)
            _l = f.get("result", {}).get("list") or []
            fr = float(_l[0]["fundingRate"]) if _l else None
        except Exception:
            fr = None
        oi_trend = None
        try:
            oi = _get("https://api.bybit.com/v5/market/open-interest?category=linear&symbol=%s&intervalTime=1h&limit=7" % symbol)
            ol = [float(x["openInterest"]) for x in (oi.get("result", {}).get("list") or [])]
            if len(ol) >= 6:                       # list viene nuevo→viejo; ol[0]=ahora
                oi_trend = (ol[0] - ol[5]) / ol[5] * 100.0 if ol[5] else None
        except Exception:
            oi_trend = None
        ls_buy = None
        try:
            ls = _get("https://api.bybit.com/v5/market/account-ratio?category=linear&symbol=%s&period=1h&limit=1" % symbol)
            _ll = ls.get("result", {}).get("list") or []
            ls_buy = float(_ll[0]["buyRatio"]) if _ll else None
        except Exception:
            ls_buy = None
        # multitud LONG vs SHORT
        long_sig = int(fr is not None and fr >= FUND_HI) + int(ls_buy is not None and ls_buy >= LS_HI)
        short_sig = int(fr is not None and fr <= FUND_LO) + int(ls_buy is not None and ls_buy <= LS_LO)
        crowd = "LONG" if long_sig > short_sig else ("SHORT" if short_sig > long_sig else None)
        fade_side = "SHORT" if crowd == "LONG" else ("LONG" if crowd == "SHORT" else None)
        # fuerza: señales alineadas + OI subiendo con la multitud = posiciones frágiles (combustible extra)
        strength = max(long_sig, short_sig)
        if oi_trend is not None and oi_trend > 1.0 and crowd:
            strength += 1                          # OI subiendo = más posiciones que liquidar cuando gire
        note = None
        if crowd:
            note = "multitud %s (funding %+.3f%%%s%s) → fade %s" % (
                crowd, (fr or 0) * 100, ", L/S %.2f" % ls_buy if ls_buy is not None else "",
                ", OI +%.1f%%" % oi_trend if (oi_trend and oi_trend > 1.0) else "", fade_side)
        out = {"crowd": crowd, "fade_side": fade_side, "funding_pct": round((fr or 0) * 100, 4) if fr is not None else None,
               "ls_buy": round(ls_buy, 3) if ls_buy is not None else None,
               "oi_trend_pct": round(oi_trend, 2) if oi_trend is not None else None,
               "strength": strength, "note": note}
    except Exception:
        out = None
    _CACHE[symbol] = (now, out)
    return out


if __name__ == "__main__":
    for s in ["ETHUSDT", "BTCUSDT", "SOLUSDT", "DOGEUSDT", "TAOUSDT"]:
        p = positioning(s)
        if p:
            print("%-8s crowd=%-5s fade=%-5s str=%d | funding=%s%% L/S=%s OI=%s%% | %s" % (
                s.replace("USDT", ""), p["crowd"], p["fade_side"], p["strength"], p["funding_pct"], p["ls_buy"], p["oi_trend_pct"], p["note"] or ""))
