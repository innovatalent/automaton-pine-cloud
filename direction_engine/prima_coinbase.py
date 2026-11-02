# -*- coding: utf-8 -*-
"""__PRIMA_VIVA_2026_09_22__ — la prima de Coinbase, en vivo, para el indicador.

QUE ES
    prima(d) = (cierre Coinbase USD / cierre Binance USDT − 1) × 100

    No es el precio del activo: es DONDE se esta comprando (demanda de EEUU contra
    demanda global). Es el unico indicador NO derivado del precio que sobrevivio la
    validacion del 2026-09-22: 15 de 15 monedas fuera de muestra positivas, la pata
    corta tambien gana (+1,91 %/op), y correlacion +0,20 con la ruptura lenta.
    Evidencia completa en docs/INVESTIGACION_AUTOMATIZAR_RENTABLE_20260922.md (Adenda 26).

LO QUE ESTE MODULO NO AUTORIZA — importa, porque se mide y esta escrito
    La prima rinde a 8 DIAS de tenencia (+3,04 %/op) y se desarma al acortar
    (+0,43 %/op a 1 dia). Medida contra las 2.420 operaciones cerradas de los brazos
    (que aguantan ~3 h), la diferencia entre operar A FAVOR y EN CONTRA de la prima es
    −0,074 %/op con t −0,42: CERO EFECTO (forward_test/bt_prima_gate_20260922.py).

    Por eso esto se sirve como CONTEXTO y no como filtro de entrada. Quien lo use para
    decidir una operacion de 5 o 15 minutos esta usando una senial de 8 dias fuera de
    su horizonte, que es exactamente el error que la ley de la cadencia minima describe.

DEPENDENCIAS: ninguna fuera de la libreria estandar. A proposito: es codigo de
produccion y no tiene que arrastrar los scripts de investigacion.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache_prima")
UA = {"User-Agent": "automaton-prima"}
VENTANA = 90                  # dias del z-score, congelado por el pre-registro
UMBRAL = 1.5                  # |z| a partir del cual hay señal, congelado

# las 17 con par USD en Coinbase y USDT en Binance (las que se midieron)
MONEDAS = ("BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "DOT", "ATOM", "UNI", "MATIC",
           "DOGE", "LTC", "LINK", "FIL", "AAVE", "BCH", "ETC")

_lock = threading.Lock()
_memo = {"ts": 0.0, "datos": {}}
REFRESCO_S = 900              # 15 minutos: es una señal diaria, no hace falta mas


def _json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _historia(cur):
    """{fecha: prima %} de los ultimos ~200 dias. Cache en disco de 12 h."""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "%s.json" % cur)
    if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < 12 * 3600:
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    out = {}
    try:
        bn = _json("https://api.binance.com/api/v3/klines"
                   "?symbol=%sUSDT&interval=1d&limit=%d" % (cur, VENTANA + 110))
        cierres_bn = {datetime.fromtimestamp(f[0] / 1000, timezone.utc).date().isoformat():
                      float(f[4]) for f in bn}
        ini = (datetime.now(timezone.utc) - timedelta(days=VENTANA + 110)).isoformat()
        cb = _json("https://api.exchange.coinbase.com/products/%s-USD/candles"
                   "?granularity=86400&start=%s&end=%s"
                   % (cur, ini, datetime.now(timezone.utc).isoformat()), timeout=35)
        for f in cb:                                  # [time, low, high, open, close, vol]
            d = datetime.fromtimestamp(f[0], timezone.utc).date().isoformat()
            b = cierres_bn.get(d)
            if b:
                out[d] = 100.0 * (float(f[4]) / b - 1.0)
    except Exception:
        if os.path.exists(p):                         # si falla la red, sirve lo viejo
            try:
                with open(p, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                return {}
        return {}
    if out:
        try:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(out, fh)
        except OSError:
            pass
    return out


def _prima_ahora(cur):
    """La prima de este instante, con los dos tickers en vivo."""
    try:
        cb = _json("https://api.exchange.coinbase.com/products/%s-USD/ticker" % cur)
        bn = _json("https://api.binance.com/api/v3/ticker/price?symbol=%sUSDT" % cur)
        c, b = float(cb["price"]), float(bn["price"])
        return 100.0 * (c / b - 1.0) if b else None
    except Exception:
        return None


def _z(historia, actual):
    """z-score CAUSAL: la media y el desvio salen de los dias ANTERIORES, no del actual."""
    fechas = sorted(historia)[-VENTANA:]
    if len(fechas) < 40 or actual is None:
        return None
    v = [historia[f] for f in fechas]
    m, s = statistics.fmean(v), statistics.pstdev(v)
    if not s:
        return None
    return (actual - m) / s


def estado(forzar=False):
    """
    {moneda: {'prima': %, 'z': float, 'senal': 'LONG'|'SHORT'|'', 'n': dias}}

    Cacheado 15 minutos. Si una moneda falla, no aparece; nunca revienta al que llama.
    """
    with _lock:
        if not forzar and (time.time() - _memo["ts"]) < REFRESCO_S and _memo["datos"]:
            return _memo["datos"]
    out = {}
    for cur in MONEDAS:
        h = _historia(cur)
        if len(h) < 40:
            continue
        a = _prima_ahora(cur)
        if a is None:
            a = h[max(h)]                             # sin ticker, el ultimo cierre
        z = _z(h, a)
        if z is None:
            continue
        out[cur] = {"prima": round(a, 4), "z": round(z, 2), "n": len(h),
                    "senal": ("LONG" if z >= UMBRAL else
                              "SHORT" if z <= -UMBRAL else "")}
    with _lock:
        _memo["ts"], _memo["datos"] = time.time(), out
    return out


def campo_cadena(cur):
    """
    El campo que se incrusta en /api/tv_niveles: 'PR:<z>:<L|S|->'.

    Corto a proposito: la cadena incrustada es el costo dominante de tokens del
    indicador. Solo las 17 monedas medidas lo tienen; el resto no lleva el campo.
    """
    d = estado().get((cur or "").upper().replace("USDT", ""))
    if not d:
        return None
    return "PR:%.2f:%s" % (d["z"], d["senal"][:1] or "-")


if __name__ == "__main__":
    for k, v in sorted(estado(forzar=True).items(), key=lambda x: -abs(x[1]["z"])):
        print("%-6s prima %+7.4f %%  z %+6.2f  %s  (%d dias)"
              % (k, v["prima"], v["z"], v["senal"] or "-", v["n"]))
