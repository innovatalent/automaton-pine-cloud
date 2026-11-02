# -*- coding: utf-8 -*-
"""PESO DE LIQUIDEZ ARRIBA vs ABAJO → IMÁN + VELOCIDAD (usuario 2026-09-03: "hay muchos activos sin liquidez; descargá los
datos de arriba y abajo, calculá cuánto pesa cada uno, definí dónde está el imán del precio — no solo a qué distancia está
el target sino qué tan pesado es respecto a su contraparte, para saber con qué VELOCIDAD lo buscará").

Universal: usa el PERFIL DE VOLUMEN (histograma de volumen por precio) de klines Bybit → funciona para TODOS los activos (se
van los 'sin liquidez'). Cada nodo de volumen = liquidez/interés donde el precio es atraído. Se pondera por REACH (decae con
la distancia: exp(-|d|/k) — un nivel lejano pesa menos, memoria project_liquidity_reach_weight). Suma la masa ARRIBA vs ABAJO
del precio → el lado MÁS PESADO = el imán (price seeks liquidity). ratio = pesado/liviano = qué tan desbalanceado → VELOCIDAD
(más desbalance + más cerca = lo busca más rápido). target = el nodo de alto volumen (HVN) más cercano del lado del imán."""
import json, math, time
import urllib.request as _ur

_CACHE = {}
_TTL = 60.0
REACH_K = 1.6          # decaimiento del reach por % de distancia (1.6% → peso ~0.37)
NBINS = 100


def _kl(sym, interval="5", limit=288):
    try:
        d = json.load(_ur.urlopen(_ur.Request(
            "https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=%s&limit=%d" % (sym, interval, limit),
            headers={"User-Agent": "x"}), timeout=10))
        L = list(reversed(d.get("result", {}).get("list", []) or []))
        return [[float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in L]   # high, low, close, vol
    except Exception:
        return None


def weigh(symbol, interval="5", limit=288):
    """Devuelve el peso de liquidez arriba/abajo, el imán y la velocidad. Cache 60s. None si no hay datos.
    {magnet:'ABAJO'/'ARRIBA'/'EQUILIBRADO', side:'SHORT'/'LONG'/None, mass_above, mass_below, ratio, target_px, target_dist,
     target_weight, speed:'rápido'/'medio'/'lento', px}."""
    key = (symbol, interval)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    out = None
    try:
        K = _kl(symbol, interval, limit)
        if K and len(K) >= 30:
            px = K[-1][2]
            hi_all = max(k[0] for k in K); lo_all = min(k[1] for k in K)
            span = hi_all - lo_all
            if span > 0 and px > 0:
                binsz = span / NBINS
                vol_bin = [0.0] * NBINS
                for h, l, c, v in K:                       # reparte el volumen de la vela por su rango (uniforme)
                    b0 = max(0, min(NBINS - 1, int((l - lo_all) / binsz)))
                    b1 = max(0, min(NBINS - 1, int((h - lo_all) / binsz)))
                    nb = b1 - b0 + 1
                    for b in range(b0, b1 + 1):
                        vol_bin[b] += v / nb
                mass_above = 0.0; mass_below = 0.0
                best_above = (0.0, None); best_below = (0.0, None)     # (peso, precio) del nodo más pesado por lado (reach-weighted)
                for b in range(NBINS):
                    p = lo_all + (b + 0.5) * binsz
                    dist = (p - px) / px * 100.0
                    w = vol_bin[b] * math.exp(-abs(dist) / REACH_K)    # reach-weight: cae con la distancia
                    if p > px:
                        mass_above += w
                        if w > best_above[0]:
                            best_above = (w, p)
                    elif p < px:
                        mass_below += w
                        if w > best_below[0]:
                            best_below = (w, p)
                tot = mass_above + mass_below or 1e-9
                pa = mass_above / tot; pb = mass_below / tot
                if pb > pa * 1.15:
                    magnet = "ABAJO"; side = "SHORT"; tw, tpx = best_below
                elif pa > pb * 1.15:
                    magnet = "ARRIBA"; side = "LONG"; tw, tpx = best_above
                else:
                    magnet = "EQUILIBRADO"; side = None; tw, tpx = (None, None)
                ratio = round(max(mass_above, mass_below) / (min(mass_above, mass_below) + 1e-9), 2)
                tdist = round((tpx - px) / px * 100.0, 2) if tpx else None
                # VELOCIDAD: más desbalance (ratio) + más cerca (|tdist|) = lo busca más rápido
                speed = "lento"
                if side and tdist is not None:
                    prox = abs(tdist)
                    if ratio >= 1.8 and prox <= 1.5:
                        speed = "rápido"
                    elif ratio >= 1.35:
                        speed = "medio"
                out = {"magnet": magnet, "side": side, "mass_above": round(pa * 100), "mass_below": round(pb * 100),
                       "ratio": ratio, "target_px": round(tpx, 6) if tpx else None, "target_dist": tdist,
                       "speed": speed, "px": px}
    except Exception:
        out = None
    _CACHE[key] = (now, out)
    return out


if __name__ == "__main__":
    for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "HBARUSDT", "TAOUSDT"]:
        w = weigh(s)
        if w:
            print("%-8s iman %-11s (abajo %d%% / arriba %d%%, %sx) target %s (%s%%) -> busca %s" % (
                s.replace("USDT", ""), w["magnet"], w["mass_below"], w["mass_above"], w["ratio"],
                w["target_px"], (("+" if (w["target_dist"] or 0) >= 0 else "") + str(w["target_dist"])) if w["target_dist"] is not None else "—", w["speed"]))
