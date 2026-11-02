# -*- coding: utf-8 -*-
"""
corr_btc.py — correlacion viva de cada alt con BTC, para saber si el CONTEXTO aplica.

POR QUE EXISTE
    _auto_verdict suma +10 o resta -14 de confianza segun el contexto de mercado
    (dominancia, rotacion, sesgo de alts). Eso vale si la alt SIGUE al mercado. Si
    esta desacoplada, el contexto describe otra cosa.

    Caso real 2026-09-08: DOT subio 11,46% con BTC plano (-0,77%) y su correlacion
    de 48 h habia caido de 0,52 a 0,39. El panel igual lo mostraba con el contexto
    heredado como si aplicara.

LO MEDIDO (12.600 eventos, 30 alts, retorno a 24 h)
    Correlacion entre lo que hizo BTC en 24 h y lo que hace la alt DESPUES:

        corr alta  (>=0.6)   +0.129   el contexto predice
        corr media (0.3-0.6) +0.283   el contexto predice MAS
        corr baja  (<0.3)    -0.035   el contexto deja de predecir
        corr muy baja(<0.15) -0.189   el contexto predice al REVES

    Conclusion: por debajo de 0.3 el contexto de BTC no aporta. NO se invierte el
    signo (eso seria sobreajustar un -0.189 sobre 482 eventos): se NEUTRALIZA, que
    es la lectura conservadora y la que el dato respalda.

USO
    python -X utf8 corr_btc.py            # tabla del roster
    corr(sym) -> {'corr':..., 'aplica_contexto':bool, ...}
"""
import json
import math
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

VENTANA = 48          # barras de 1h para la correlacion viva
PREVIA = 168          # ventana previa, para detectar el CAMBIO
UMBRAL = 0.30         # por debajo, el contexto de BTC no aplica
_TTL = 900.0
_C = {}


def _kl(sym, lim=260):
    u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=1h&limit=%d"
         % (sym, lim))
    try:
        L = json.loads(urllib.request.urlopen(urllib.request.Request(
            u, headers={"User-Agent": "corr"}), timeout=15).read())
        return {int(k[0]): float(k[4]) for k in L}
    except Exception:
        return None


def _corr(a, b):
    if len(a) < 8:
        return 0.0
    ma = sum(a) / len(a); mb = sum(b) / len(b)
    ca = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return ca / va if va > 0 else 0.0


def corr(sym, ref="BTCUSDT"):
    """Correlacion de 48 h con BTC y su cambio contra las 168 h previas."""
    key = (str(sym).upper(), ref)
    hit = _C.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    U = _kl(key[0]); B = _kl(ref)
    if not U or not B:
        return None
    ts = sorted(set(U) & set(B))
    if len(ts) < PREVIA + VENTANA:
        return None
    ru = [math.log(U[ts[i]] / U[ts[i - 1]]) for i in range(1, len(ts))]
    rb = [math.log(B[ts[i]] / B[ts[i - 1]]) for i in range(1, len(ts))]
    c48 = _corr(ru[-VENTANA:], rb[-VENTANA:])
    cprev = _corr(ru[-(PREVIA + VENTANA):-VENTANA], rb[-(PREVIA + VENTANA):-VENTANA])
    out = {"symbol": key[0], "corr": round(c48, 3), "corr_previa": round(cprev, 3),
           "cambio": round(c48 - cprev, 3),
           "aplica_contexto": bool(c48 >= UMBRAL),
           "umbral": UMBRAL,
           "nota": ("correlacion %.2f: el contexto de BTC aplica" % c48) if c48 >= UMBRAL
                   else ("DESACOPLADA (corr %.2f < %.2f): el contexto de BTC NO aplica "
                         "y se neutraliza (medido: corr(BTC,alt fwd) cae a -0.04 y a "
                         "-0.19 bajo 0.15)" % (c48, UMBRAL))}
    _C[key] = (time.time(), out)
    return out


def main():
    try:
        import mom_paper as MP
        syms = sorted(set(MP.CRYPTO))
    except Exception:
        syms = ["DOTUSDT", "LINKUSDT", "SOLUSDT"]
    print("=" * 62)
    print("CORRELACION VIVA con BTC (48h vs 168h previas)")
    print("=" * 62)
    print("  %-9s %7s %8s %8s   %s" % ("alt", "corr48", "previa", "cambio", "contexto"))
    print("  " + "-" * 54)
    des = []
    for s in syms:
        if s == "BTCUSDT":
            continue
        r = corr(s)
        if not r:
            continue
        if not r["aplica_contexto"]:
            des.append(s)
        print("  %-9s %7.3f %8.3f %+8.3f   %s"
              % (s.replace("USDT", ""), r["corr"], r["corr_previa"], r["cambio"],
                 "aplica" if r["aplica_contexto"] else "NO APLICA (desacoplada)"))
    print()
    print("  %d de %d desacopladas: el contexto de BTC no se les aplica."
          % (len(des), len(syms) - 1))


if __name__ == "__main__":
    main()
