# -*- coding: utf-8 -*-
"""__VOL_OBJETIVO_2026_09_22__ — el multiplicador de tamaño por volatilidad, en vivo.

QUE ES
    multiplicador(d) = min(3,0 ; 1,0 % / desvío de los 60 días anteriores)

    aplicado al retorno diario de la cartera pre-registrada (prima hold 8 + ruptura lenta,
    riesgo normalizado). Es la única mejora del proyecto que superó una vara pedida:

        sin objetivo ... Sharpe 0,73 · DD −36,0 % · g_max 26,4 %/año
        con objetivo ... Sharpe 1,02 · DD −26,4 % · g_max 51,8 %/año   (+96 %)
        tope 1× ........ Sharpe 0,85 · DD −24,7 % · g_max 36,1 %/año   (+37 %, sin apalancar)

    Robustez: ventanas 30/60/90/120 dan 0,98-1,05; las dos mitades positivas (1,23 y 0,82).
    Funding MEDIDO (no supuesto): −0,58 %/año, porque la cartera es casi neutral y la pata
    corta cobra lo que paga la larga. Evidencia: adenda de
    forward_test/PREREG_prima_coinbase_20260922.md

LO QUE ESTE MODULO NO AUTORIZA
    Es el multiplicador de la cartera SOMBRA pre-registrada. NO es una instrucción para las
    operaciones intradía del usuario: aquella cartera aguanta 8 días y ésta cierra en horas, y
    la ley de la cadencia mínima dice que no son comparables. Se sirve como INFORMACIÓN — qué
    tan agitado está el mercado contra su propia norma — y no toca ningún brazo ni ningún flag.

    Y todo esto se midió en la misma ventana donde se descubrió la prima: sólo el forward
    decide. Séptima condición del pre-registro: si en forward el objetivo no mejora, se opera
    el tope 1×.

Sin dependencias fuera de la stdlib.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import threading
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache_prima")
UA = {"User-Agent": "automaton-volobj"}

OBJETIVO = 1.0        # % diario, congelado por el pre-registro
VENTANA = 60          # días, congelado
TOPE = 3.0            # congelado

# la canasta sobre la que se mide la volatilidad de la cartera
CESTA = ("BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "DOT", "ATOM", "UNI",
         "DOGE", "LTC", "LINK", "FIL", "AAVE", "BCH", "ETC")

_lock = threading.Lock()
_memo = {"ts": 0.0, "datos": None}
REFRESCO_S = 1800


def _cierres(cur, dias=VENTANA + 40):
    """Cierres diarios de Binance, cacheados 6 h."""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "cl_%s.json" % cur)
    if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < 6 * 3600:
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    try:
        req = urllib.request.Request(
            "https://api.binance.com/api/v3/klines?symbol=%sUSDT&interval=1d&limit=%d"
            % (cur, dias), headers=UA)
        filas = json.loads(urllib.request.urlopen(req, timeout=30).read())
        out = {datetime.fromtimestamp(f[0] / 1000, timezone.utc).date().isoformat(): float(f[4])
               for f in filas}
    except Exception:
        return {}
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(out, fh)
    except OSError:
        pass
    return out


def estado(forzar=False):
    """
    {'multiplicador', 'vol_60d', 'vol_percentil', 'regimen', 'multiplicador_tope1'}

    `vol_percentil` ubica la volatilidad de hoy dentro de su propia historia reciente: es lo
    que hace legible el número (0,6× no dice nada solo; "vol en el percentil 85" sí).
    """
    with _lock:
        if not forzar and _memo["datos"] and (time.time() - _memo["ts"]) < REFRESCO_S:
            return _memo["datos"]

    series = {c: _cierres(c) for c in CESTA}
    fechas = sorted(set.intersection(*[set(v) for v in series.values() if v])
                    ) if any(series.values()) else []
    if len(fechas) < VENTANA + 5:
        return {"error": "sin historia suficiente", "multiplicador": None}

    # retorno diario de la canasta equiponderada — proxy de la vol de la cartera
    rets = []
    for i in range(1, len(fechas)):
        f, fa = fechas[i], fechas[i - 1]
        v = [100.0 * (series[c][f] / series[c][fa] - 1.0)
             for c in CESTA if series.get(c, {}).get(fa)]
        if v:
            rets.append((f, statistics.fmean(v)))

    if len(rets) < VENTANA + 1:
        return {"error": "sin retornos suficientes", "multiplicador": None}

    vol = statistics.pstdev([r for _f, r in rets[-VENTANA:]])
    mult = min(TOPE, OBJETIVO / vol) if vol else None

    # percentil de la vol actual contra las ventanas anteriores disponibles
    vols = []
    for j in range(VENTANA, len(rets)):
        vols.append(statistics.pstdev([r for _f, r in rets[j - VENTANA:j]]))
    pct = 100.0 * sum(1 for x in vols if x < vol) / len(vols) if vols else None

    d = {"multiplicador": round(mult, 2) if mult else None,
         "multiplicador_tope1": round(min(1.0, mult), 2) if mult else None,
         "vol_60d_pct_dia": round(vol, 3),
         "vol_percentil": round(pct, 0) if pct is not None else None,
         "regimen": ("CALMA" if mult and mult > 1.3 else
                     "NORMAL" if mult and mult >= 0.8 else "AGITADO"),
         "objetivo_pct_dia": OBJETIVO, "ventana_dias": VENTANA, "tope": TOPE,
         "dias": len(rets), "hasta": rets[-1][0],
         "aviso": ("multiplicador de la cartera SOMBRA pre-registrada (aguanta 8 dias). "
                   "NO es una instruccion para operaciones intradia: la ley de la cadencia "
                   "minima dice que no son comparables."),
         "ts": time.time()}
    with _lock:
        _memo["ts"], _memo["datos"] = time.time(), d
    return d


def campo_cadena():
    """'VO:<mult>:<regimen inicial>' para incrustar en /api/tv_niveles. ~10 caracteres."""
    d = estado()
    if not d or not d.get("multiplicador"):
        return None
    return "VO:%.2f:%s" % (d["multiplicador"], (d["regimen"] or "-")[0])


if __name__ == "__main__":
    d = estado(forzar=True)
    if d.get("error"):
        print("error: %s" % d["error"])
    else:
        print("volatilidad 60 d de la canasta : %.3f %%/dia  (percentil %.0f)"
              % (d["vol_60d_pct_dia"], d["vol_percentil"] or 0))
        print("multiplicador (tope 3x) ....... %.2fx   -> regimen %s"
              % (d["multiplicador"], d["regimen"]))
        print("multiplicador sin apalancar ... %.2fx" % d["multiplicador_tope1"])
        print("datos hasta %s (%d dias)" % (d["hasta"], d["dias"]))
