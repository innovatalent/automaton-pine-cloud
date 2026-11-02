# -*- coding: utf-8 -*-
"""IMANES DE LIQUIDACION (port del Pine del usuario "Liquidation Magnets [Top 10]" + LuxAlgo, 2026-08-02).

Replica la logica: barras con apertura de posiciones apalancadas (ΔOI + volumen + delta/CVD sobre percentil, o rango
>= x*ATR) proyectan PRECIOS DE LIQUIDACION a 100x/50x/25x (long = liq ABAJO, short = liq ARRIBA, con margen de
mantenimiento). Se clusterizan pools cercanos (suma peso), se descartan los ya barridos, y se rankean. El precio TIENDE
a ir a buscar el pool mas pesado/cercano = el IMAN. Devuelve la DIRECCION (hacia el iman) para todas las estrategias.
Datos: Bybit publico (klines + open-interest). Sin OI -> cae a volumen/CVD.
Uso: from liq_magnets import magnets; magnets("BTCUSDT")
"""
import json
import urllib.request

BASE = "https://api.bybit.com"
LEVS = (100, 50, 25)         # apalancamientos proyectados (10x off por defecto, como el Pine)
MM = 0.004                   # margen de mantenimiento 0.4% (acerca la liq a la entrada)
BASE_PCT = 85                # percentil de actividad para barra base
VOL_MULT = 2.5               # disparo secundario: rango >= 2.5*ATR
CLUS_PCT = 0.20              # agrupar niveles a <0.2%
MIN_RNG = 0.001              # rango minimo de barra base (0.1%)


def _get(path, params):
    try:
        q = "&".join("%s=%s" % (k, v) for k, v in params.items())
        with urllib.request.urlopen("%s%s?%s" % (BASE, path, q), timeout=12) as r:
            return json.loads(r.read().decode()).get("result", {})
    except Exception:
        return {}


def _percentile(vals, pct):
    s = sorted(vals)
    if not s:
        return 0.0
    i = min(len(s) - 1, int(len(s) * pct / 100.0))
    return s[i]


def _oi_binance(symbol, interval, bars):
    """OI histórico de Binance futures (sumOpenInterest en la coin base) por timestamp. {} si no lista el símbolo
    (ej acciones tokenizadas como TQQQ solo están en Bybit). Para AGREGAR con Bybit y acercarse al mapa de Coinglass."""
    per = {"1": "5m", "3": "5m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "120": "2h", "240": "4h"}.get(str(interval), "15m")
    try:
        u = "https://fapi.binance.com/futures/data/openInterestHist?symbol=%s&period=%s&limit=%d" % (symbol, per, min(bars, 500))
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as r:
            data = json.loads(r.read().decode())
        return {int(x["timestamp"]) // 1000: float(x["sumOpenInterest"]) for x in data if x.get("sumOpenInterest")}
    except Exception:
        return {}


def magnets(symbol, interval="15", bars=200, topn=8):
    """Calcula los imanes de liquidacion + la direccion dominante. Devuelve dict o None."""
    k = _get("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": interval, "limit": bars})
    kl = k.get("list", [])
    if len(kl) < 40:
        return None
    kl = kl[::-1]   # cronologico. cada item: [start,open,high,low,close,volume,turnover]
    o = [float(x[1]) for x in kl]; h = [float(x[2]) for x in kl]
    lo = [float(x[3]) for x in kl]; c = [float(x[4]) for x in kl]; v = [float(x[5]) for x in kl]
    px = c[-1]
    # OI (opcional)
    oi_map = {}
    imap = {"1": "1min", "5": "5min", "15": "15min", "30": "30min", "60": "1h"}.get(str(interval), "15min")
    oid = _get("/v5/market/open-interest", {"category": "linear", "symbol": symbol, "intervalTime": imap, "limit": bars})
    for it in oid.get("list", []):
        try:
            oi_map[int(it["timestamp"]) // 1000] = float(it["openInterest"])
        except Exception:
            pass
    # AGREGAR OI de Binance (2026-08-28, "reforzar niveles"): suma el OI de otro exchange grande → el imán se acerca al
    # mapa AGREGADO de Coinglass. Solo suma donde el símbolo existe (majors); tokenizadas (TQQQ) = solo Bybit, queda igual.
    exchanges = ["Bybit"]
    _bn = _oi_binance(symbol, interval, bars)
    if _bn:
        exchanges.append("Binance")
        for _ts, _v in _bn.items():
            oi_map[_ts] = oi_map.get(_ts, 0.0) + _v
    oi_series = [oi_map.get(int(x[0]) // 1000) for x in kl] if oi_map else [None] * len(kl)

    # ATR14 + medias
    trs = [h[i] - lo[i] for i in range(len(kl))]
    atr = sum(trs[-14:]) / min(14, len(trs))
    vol_sma = sum(v[-100:]) / min(100, len(v)) or 1e-9
    # peso por barra (ΔOI*volRel si hay OI; si no |delta| o volumen) + delta (direccion agresion)
    weights = []; deltas = []
    for i in range(len(kl)):
        rng = h[i] - lo[i]
        buy = v[i] * ((c[i] - lo[i]) / rng) if rng > 0 else v[i] * 0.5
        sel = v[i] * ((h[i] - c[i]) / rng) if rng > 0 else v[i] * 0.5
        delta = buy - sel
        deltas.append(delta)
        doi = 0.0
        if oi_series[i] is not None and i > 0 and oi_series[i - 1] is not None:
            doi = max(oi_series[i] - oi_series[i - 1], 0.0)
        vol_rel = v[i] / vol_sma
        w = (doi * vol_rel) if doi > 0 else (v[i])
        weights.append(w)
    w_thr = _percentile(weights[-min(len(weights), 300):], BASE_PCT)

    # generar niveles de liquidacion de barras base (no las ya-atravesadas por barras posteriores = barridas)
    lvls = []   # (price, weight, is_long_liq)
    for i in range(len(kl) - 1):
        rng_pct = (h[i] - lo[i]) / c[i] if c[i] > 0 else 0
        if rng_pct < MIN_RNG:
            continue
        peso_hit = weights[i] > 0 and weights[i] >= w_thr
        volat_hit = (h[i] - lo[i]) >= VOL_MULT * atr
        if not (peso_hit or volat_hit):
            continue
        w_eff = max(weights[i], w_thr)
        base = (h[i] + lo[i] + c[i]) / 3.0            # HLC3
        agg_buy = deltas[i] >= 0                       # compra agresiva -> abren LONGS -> liq ABAJO
        for lev in LEVS:
            if agg_buy:
                p = base * (1 - 1.0 / lev + MM)         # liq de longs (abajo)
                isl = True
            else:
                p = base * (1 + 1.0 / lev - MM)         # liq de shorts (arriba)
                isl = False
            if p <= 0:
                continue
            swept = any(lo[j] <= p <= h[j] for j in range(i + 1, len(kl)))   # barrido despues -> consumido
            if not swept:
                lvls.append((p, w_eff, isl))
    if not lvls:
        return {"symbol": symbol, "px": px, "dir": "NEUTRO", "up": [], "down": []}

    # clustering (pools): fusiona a <CLUS_PCT%
    lvls.sort(key=lambda z: z[0])
    pools = []
    used = [False] * len(lvls)
    for i in range(len(lvls)):
        if used[i]:
            continue
        sp = lvls[i][0] * lvls[i][1]; sw = lvls[i][1]; used[i] = True
        for j in range(i + 1, len(lvls)):
            if not used[j] and abs(lvls[j][0] - lvls[i][0]) / lvls[i][0] * 100 <= CLUS_PCT:
                sp += lvls[j][0] * lvls[j][1]; sw += lvls[j][1]; used[j] = True
        pools.append({"px": sp / sw, "w": sw})
    for p in pools:
        p["dist_pct"] = round((p["px"] - px) / px * 100, 2)
    tot = sum(p["w"] for p in pools) or 1e-9
    for p in pools:
        p["wrel"] = round(p["w"] / (tot / len(pools)), 1)
    up = sorted([p for p in pools if p["px"] > px], key=lambda p: -p["w"])[:topn]
    down = sorted([p for p in pools if p["px"] < px], key=lambda p: -p["w"])[:topn]

    # DIRECCION dominante = el LADO con MÁS liquidación TOTAL (suma de pesos arriba vs abajo). ANTES usaba pull=w/dist,
    # que sobre-pesaba la CERCANÍA y elegía un cluster chico pegado en vez del GRANDE lejos (ej SKHYNIX: el pool gordo
    # está ARRIBA ~1300 pero elegía uno chico abajo → dir SHORT errado). Ahora manda el TAMAÑO = lo que muestra Coinglass.
    sum_up = sum(p["w"] for p in pools if p["px"] > px)
    sum_dn = sum(p["w"] for p in pools if p["px"] < px)
    direction = "LONG" if sum_up > sum_dn else ("SHORT" if sum_dn > sum_up else "NEUTRO")
    if direction == "NEUTRO":
        best = max(pools, key=lambda p: p["w"])
    else:
        _domUp = direction == "LONG"
        best = max([p for p in pools if (p["px"] > px) == _domUp], key=lambda p: p["w"] / (max(abs(p["dist_pct"]), 0.3) ** 0.35))
    return {"symbol": symbol, "px": px, "dir": direction, "sum_up": round(sum_up, 1), "sum_dn": round(sum_dn, 1),
            "magnet_px": round(best["px"], 8), "magnet_dist": best["dist_pct"], "magnet_wrel": best["wrel"],
            "up": [{"px": round(p["px"], 8), "dist": p["dist_pct"], "wrel": p["wrel"]} for p in up[:5]],
            "down": [{"px": round(p["px"], 8), "dist": p["dist_pct"], "wrel": p["wrel"]} for p in down[:5]],
            "has_oi": bool(oi_map), "exchanges": exchanges, "n_ex": len(exchanges)}


if __name__ == "__main__":
    import sys
    for s in (sys.argv[1:] or ["BTCUSDT", "SOLUSDT", "COTIUSDT"]):
        m = magnets(s)
        if not m:
            print("%-10s sin datos" % s); continue
        print("%-10s px=%.6g -> IMAN %s (dist %+.2f%% peso x%.1f, OI=%s)" % (
            s, m["px"], m["dir"], m.get("magnet_dist", 0), m.get("magnet_wrel", 0), m["has_oi"]))
        print("   arriba:", ["%.6g(%+.2f%%,x%.1f)" % (p["px"], p["dist"], p["wrel"]) for p in m["up"][:3]])
        print("   abajo :", ["%.6g(%+.2f%%,x%.1f)" % (p["px"], p["dist"], p["wrel"]) for p in m["down"][:3]])
