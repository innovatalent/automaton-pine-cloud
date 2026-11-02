# -*- coding: utf-8 -*-
"""FUERZA DEL EMPUJE por VOLUMEN vs MEDIA 7d (hipótesis usuario 2026-09-03): el volumen actual comparado con la MEDIA de
los últimos 7 días distingue si el empuje en una dirección es FUERTE o no. CLAVE para acciones/ETF (NVDA, TQQQ…): la media
se toma SOLO en horario de mercado (Wall Street L-V 13:30-20:00 UTC) — con mercado cerrado el token no se mueve y diluiría
la media. Para cripto = media 24/7. Devuelve ratio (vol actual / media), fuerte si ≥1.2, + dirección del delta y su magnitud
vs la media (delta_ratio). Fuente Bybit 1h. Cache 8min. Se usa como CRITERIO de confirmación (empuje fuerte a favor = mejor)."""
import json, time
import urllib.request as _ur
import datetime as _dt

_CACHE = {}
_TTL = 480.0
WS = (13.5, 20.0)   # Wall Street 9:30-16:00 NY = 13:30-20:00 UTC


def _kl(sym, interval="60", limit=200):
    try:
        d = json.load(_ur.urlopen(_ur.Request(
            "https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=%s&limit=%d" % (sym, interval, limit),
            headers={"User-Agent": "x"}), timeout=12))
        L = list(reversed(d.get("result", {}).get("list", []) or []))
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in L]
    except Exception:
        return None


def _in_ws(ts_ms):
    u = _dt.datetime.utcfromtimestamp(ts_ms / 1000)
    if u.weekday() >= 5:
        return False
    hm = u.hour + u.minute / 60.0
    return WS[0] <= hm < WS[1]


def _same_slot(ts_ms, ref_ms):
    """2026-09-04 (usuario: acciones 24h, 'la clave es ver el flujo'): la media de una ACCIÓN se toma en la MISMA franja horaria
    (±1 h UTC, días hábiles) de la vela actual → de noche se compara contra su volumen normal de noche, no contra Wall Street
    (antes: media SOLO WS → de noche daba 'volumen flojo 0.2x' por construcción y castigaba a TQQQ fuera de sesión)."""
    u = _dt.datetime.utcfromtimestamp(ts_ms / 1000)
    if u.weekday() >= 5:
        return False
    r = _dt.datetime.utcfromtimestamp(ref_ms / 1000)
    dh = abs(u.hour - r.hour)
    return min(dh, 24 - dh) <= 1


def _delta(b):
    rng = b[2] - b[3]
    return b[5] * ((b[4] - b[3]) - (b[2] - b[4])) / rng if rng > 0 else 0.0


def push(sym, is_stock=False):
    """Fuerza del empuje: vol actual vs media 7d (acciones=solo horario WS). None si no hay data."""
    hit = _CACHE.get(sym)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    res = None
    try:
        K = _kl(sym, "60", 200)
        if K and len(K) >= 40:
            now = K[-1][0]; cut = now - 7 * 86400000
            _last = K[-2]                        # última vela CERRADA (K[-1] está en formación = parcial, la excluyo)
            sample = [b for b in K[:-1] if b[0] >= cut and (_same_slot(b[0], _last[0]) if is_stock else True)]
            if len(sample) >= 8:
                vols = [b[5] for b in sample]
                avg = sum(vols) / len(vols)
                cur = _last[5]
                ratio = (cur / avg) if avg > 0 else 0.0
                dels = [abs(_delta(b)) for b in sample]
                davg = (sum(dels) / len(dels)) or 1e-9
                dcur = _delta(_last)
                res = {"avg_vol": round(avg), "cur_vol": round(cur), "ratio": round(ratio, 2),
                       "strong": ratio >= 1.2, "delta_dir": ("LONG" if dcur > 0 else "SHORT"),
                       "delta_ratio": round(abs(dcur) / davg, 2), "n": len(sample), "session_only": is_stock, "baseline": ("misma franja ±1h" if is_stock else "24/7")}
    except Exception:
        res = None
    _CACHE[sym] = (time.time(), res)
    return res


if __name__ == "__main__":
    for s, st in [("TQQQUSDT", True), ("NVDAUSDT", True), ("HOODUSDT", True), ("BTCUSDT", False), ("TAOUSDT", False)]:
        r = push(s, st)
        if not r:
            print(s, "sin data"); continue
        print("%-8s %s ratio=%.2f %s · delta %s x%.1f · media7d=%d (n=%d %s)" % (
            s.replace("USDT", ""), "📈FUERTE" if r["strong"] else "débil", r["ratio"],
            ("↑" if r["ratio"] >= 1 else "↓"), r["delta_dir"], r["delta_ratio"], r["avg_vol"], r["n"],
            "solo-WS" if r["session_only"] else "24/7"))
