# -*- coding: utf-8 -*-
"""
funding_db.py — base de datos local de funding de perpetuos, para todos los activos.

POR QUE EXISTE
    En los perpetuos el funding no es una comision: es una TRANSFERENCIA entre lados
    cada 8 horas. Es estructuralmente POSITIVO la mayor parte del tiempo, porque el
    apalancamiento comprador domina, asi que el que esta CORTO cobra y el que esta
    LARGO paga. Un backtest que lo ignora subestima el short y sobrestima el long.

    Ademas el funding es un TERMOMETRO DE POSICIONAMIENTO: cuando esta muy alto, hay
    mucha gente larga y apalancada pagando por estarlo. Eso lo hace candidato a filtro
    de entrada, no solo a costo.

EL BUG QUE CORRIGE
    El primer intento (bt_funding.py) trajo solo ~166 dias contra los 812 de las velas,
    asi que casi ningun trade caia dentro del tramo con datos y el aporte medido dio
    cero. La causa: el endpoint acepta limit=1000 pero DEVUELVE 500, y el bucle cortaba
    con `if len(L) < 1000: break` en la primera pagina. Aca se pagina por endTime hasta
    cubrir el rango pedido, sin confiar en el tamano de la respuesta.

QUE GUARDA
    Un archivo por simbolo en bt_cache/funding con [(ts_segundos, tasa)], donde la tasa
    es POR PERIODO de 8 h (0.0001 = 0.01%), no anualizada. Se refresca solo si el
    archivo no llega hasta hoy.

USO
    python -X utf8 funding_db.py                  # descarga todo y muestra el resumen
    python -X utf8 funding_db.py BTCUSDT ETHUSDT  # solo esos
    import funding_db; F = funding_db.cargar("BTCUSDT")
"""

from __future__ import annotations

import io
import json
import os
import statistics as st
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "bt_cache", "funding")
PERIODO = 8 * 3600           # el funding se liquida cada 8 h
PAGINAS = 8                  # 8 x 500 = 4.000 periodos ~ 3,6 anios
DIAS_MIN = 800               # lo que hace falta para cubrir el backtest

# El universo completo: las 18 del backtest mas todo lo que el motor puede operar.
UNIV = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT",
    "AVAXUSDT", "DOTUSDT", "LTCUSDT", "UNIUSDT", "XLMUSDT", "NEARUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "SUIUSDT", "AAVEUSDT", "BNBUSDT", "TRXUSDT", "BCHUSDT",
    "HBARUSDT", "TONUSDT", "TAOUSDT", "1000PEPEUSDT", "WLDUSDT", "GALAUSDT",
    "ENAUSDT", "INJUSDT", "FILUSDT", "ATOMUSDT", "ETCUSDT", "ICPUSDT", "SEIUSDT",
]


def _bajar(sym, paginas=PAGINAS):
    """
    Pagina hacia atras con endTime. NO se corta por el tamano de la respuesta: el
    endpoint acepta limit=1000 pero devuelve 500, y ese fue exactamente el bug que
    dejo la base con 166 dias. Se corta cuando no hay mas datos o no avanza.
    """
    out, end = {}, None
    for _ in range(paginas):
        u = "https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&limit=1000" % sym
        if end:
            u += "&endTime=%d" % end
        try:
            L = json.loads(urllib.request.urlopen(urllib.request.Request(
                u, headers={"User-Agent": "fdb"}), timeout=30).read())
        except Exception:
            break
        if not L:
            break
        antes = len(out)
        for x in L:
            out[int(x["fundingTime"]) // 1000] = float(x["fundingRate"])
        if len(out) == antes:                 # la pagina no aporto nada nuevo
            break
        end = int(L[0]["fundingTime"]) - 1
        time.sleep(0.06)
    return sorted(out.items())


def cargar(sym, refrescar=False):
    """Devuelve [(ts, tasa)] del cache, bajandolo si falta o si quedo viejo."""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "%s.json" % sym)
    F = None
    if os.path.exists(p) and not refrescar:
        try:
            F = json.load(io.open(p, encoding="utf-8"))
        except Exception:
            F = None
    # se rebaja si no llega hasta hace poco o si no cubre el rango del backtest
    if F:
        cubre = (F[-1][0] - F[0][0]) / 86400.0
        fresco = (time.time() - F[-1][0]) < 3 * 86400
        if cubre >= DIAS_MIN and fresco:
            return F
    F = _bajar(sym)
    if F:
        io.open(p, "w", encoding="utf-8").write(json.dumps(F))
    return F


def serie_pct(F, ventana=200):
    """
    Percentil movil de cada tasa contra las `ventana` anteriores. Es la forma correcta
    de usar el funding como filtro: el nivel absoluto no dice nada porque cada activo
    tiene su regimen propio, pero "esta alto PARA ESTE activo" si.
    """
    out = {}
    for i in range(len(F)):
        if i < 30:
            continue
        ref = [x[1] for x in F[max(0, i - ventana):i]]
        out[F[i][0]] = 100.0 * sum(1 for x in ref if x < F[i][1]) / len(ref)
    return out


def acumulado(F, t0, t1, lado):
    """
    Funding cobrado (positivo) o pagado (negativo) por una posicion abierta entre t0 y
    t1, en puntos porcentuales. Con funding positivo el CORTO cobra y el LARGO paga:
    de ahi el signo -lado.
    """
    import bisect
    ft = [x[0] for x in F]
    a = bisect.bisect_right(ft, t0)
    b = bisect.bisect_right(ft, t1)
    return sum(-lado * F[k][1] * 100.0 for k in range(a, b))


def main():
    syms = sys.argv[1:] or UNIV
    print("=" * 86)
    print("BASE DE FUNDING · %d activos" % len(syms))
    print("=" * 86)
    print("  %-14s %7s %10s %10s %9s %9s"
          % ("activo", "n", "desde", "hasta", "% positivo", "media/8h"))
    print("  " + "-" * 66)
    tot, pos_tot = [], 0
    ok = 0
    for s in syms:
        try:
            F = cargar(s)
        except Exception as e:
            print("  %-14s  error %r" % (s, e)); continue
        if not F:
            print("  %-14s  sin datos" % s); continue
        r = [x[1] for x in F]
        pos = 100.0 * sum(1 for x in r if x > 0) / len(r)
        dias = (F[-1][0] - F[0][0]) / 86400.0
        marca = "" if dias >= DIAS_MIN else "   <- corto"
        print("  %-14s %7d %10s %10s %8.1f%% %+8.4f%%%s"
              % (s, len(F),
                 time.strftime("%Y-%m-%d", time.gmtime(F[0][0])),
                 time.strftime("%Y-%m-%d", time.gmtime(F[-1][0])),
                 pos, 100 * sum(r) / len(r), marca))
        tot.extend(r); pos_tot += sum(1 for x in r if x > 0)
        ok += 1
    if tot:
        print()
        print("  TOTAL: %d periodos de %d activos" % (len(tot), ok))
        print("  %.1f%% POSITIVOS · media %+.4f%% por periodo de 8 h · mediana %+.4f%%"
              % (100.0 * pos_tot / len(tot), 100 * sum(tot) / len(tot),
                 100 * st.median(tot)))
        anual = (sum(tot) / len(tot)) * 3 * 365 * 100
        print("  equivale a %+.1f%% anual para el que esta CORTO (y -%.1f%% para el largo)"
              % (anual, abs(anual)))
        print()
        print("  LECTURA: con funding positivo el CORTO cobra y el LARGO paga. Si el")
        print("  porcentaje de positivos es alto, todo backtest que ignore el funding")
        print("  subestima el lado short y sobrestima el long.")


if __name__ == "__main__":
    main()
