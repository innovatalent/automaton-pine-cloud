# -*- coding: utf-8 -*-
"""LIQUIDEZ DEL ORDER BOOK (pedido usuario 2026-09-03: sumar order book depth de cripto para más precisión). Trae el libro
de Bybit (bids/asks, limit 200) y compara la LIQUIDEZ DE REPOSO por debajo (bids = soporte) vs por encima (asks = resistencia)
dentro de una banda ±%, + los MUROS más grandes a cada lado. OJO conceptual: el order book = liquidez que ABSORBE (soporte/
resistencia), distinta del IMÁN de liquidaciones que el precio HUNTEA (eso = coinglass/liq_magnets). Se usa como confirmación
de estructura + niveles de muro, NO como 'target a buscar'. Cache 20s (el libro cambia rápido y se puede spoofear)."""
import json, time
import urllib.request as _ur

_CACHE = {}
_TTL = 20.0


def depth(sym, band=0.05):
    """Devuelve {mid, below, above (notional en la banda), bid_wall, ask_wall, imbalance, ob_bias}. None si falla.
    imbalance>0 = más bids (soporte, lean LONG) · <0 = más asks (resistencia, lean SHORT). ob_bias con umbral 0.25."""
    hit = _CACHE.get(sym)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    res = None
    try:
        d = json.load(_ur.urlopen(_ur.Request(
            "https://api.bybit.com/v5/market/orderbook?category=linear&symbol=%s&limit=200" % sym,
            headers={"User-Agent": "x"}), timeout=8))
        r = d.get("result", {})
        bids = [[float(p), float(s)] for p, s in r.get("b", [])]
        asks = [[float(p), float(s)] for p, s in r.get("a", [])]
        if not bids or not asks:
            raise ValueError("libro vacío")
        mid = (bids[0][0] + asks[0][0]) / 2.0
        lo = mid * (1 - band); hi = mid * (1 + band)
        below = sum(p * s for p, s in bids if p >= lo)         # notional de bids en la banda (soporte abajo)
        above = sum(p * s for p, s in asks if p <= hi)         # notional de asks en la banda (resistencia arriba)

        def _wall(book):
            if not book:
                return None
            top = max(book, key=lambda x: x[0] * x[1])
            return {"px": round(top[0], 6), "notional": round(top[0] * top[1]), "dist_pct": round((top[0] - mid) / mid * 100, 2)}
        imb = round((below - above) / (below + above), 2) if (below + above) > 0 else 0.0
        res = {"mid": round(mid, 6), "below": round(below), "above": round(above),
               "bid_wall": _wall([b for b in bids if b[0] >= lo]), "ask_wall": _wall([a for a in asks if a[0] <= hi]),
               "imbalance": imb, "ob_bias": ("LONG" if imb >= 0.25 else ("SHORT" if imb <= -0.25 else None))}
    except Exception:
        res = None
    _CACHE[sym] = (time.time(), res)
    return res


if __name__ == "__main__":
    for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TAOUSDT"]:
        r = depth(s)
        if not r:
            print(s, "sin libro"); continue
        print("%-8s mid=%.4g | soporte(bids)=%d vs resist(asks)=%d · imbalance=%+.2f → lean=%s | muro bid=%s ask=%s" % (
            s.replace("USDT", ""), r["mid"], r["below"], r["above"], r["imbalance"], r["ob_bias"],
            (r["bid_wall"] or {}).get("px"), (r["ask_wall"] or {}).get("px")))
