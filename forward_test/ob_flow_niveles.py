# -*- coding: utf-8 -*-
"""
ob_flow_niveles.py — order blocks y estado de flujo, para exportar al grafico.

DE DONDE SALE
    El usuario quiere sumar a la confluencia los ORDER BLOCKS y las señales de su
    indicador "OB 3D Confluence Engine PRO · Flow", que combina order block,
    IFVG/BPR, presion por OHLC, open interest y RVOL.

    Ese indicador corre DENTRO de TradingView, y Pine no puede mandar datos hacia
    afuera. Asi que la integracion correcta es al reves: el bot calcula lo
    equivalente de este lado y lo exporta junto con el resto de los niveles.

★★ ADVERTENCIA QUE HAY QUE LEER ANTES DE USAR ESTO ★★
    El order block YA SE MIDIO EN ESTA CASA, dos veces, y las dos veces perdio:

      · como GATILLO: corregidos dos sesgos de anticipacion, da lo MISMO que
        entrar al azar (bt_ob_short.py).
      · como NIVEL: contra un placebo apareado del mismo barrio, el rechazo
        inmediato del OB real es MENOR que el del placebo, con t -3,91 dentro de
        muestra y -2,09 en los activos reservados, o sea que REPLICA
        (bt_ob_niveles2.py, biblioteca 15).

    Por eso todo lo de este modulo es PARA MOSTRAR, nunca para puntuar. Se dibuja
    porque el usuario quiere verlo y porque ver la coincidencia informa; no se le
    da puntaje porque la evidencia dice que no lo merece.

    Lo mismo con el flujo: en nuestras mediciones el order flow es TIMING, no
    direccion (AUC ~0.50 para anticipar la vela grande).

QUE CALCULA
    zonas_ob(sym)   order blocks CAUSALES vigentes cerca del precio, con su lado,
                    borde superior/inferior y una puntuacion de calidad.
    flujo(sym)      cambio de open interest, matriz precio x OI (LONG BUILD /
                    SHORT BUILD / SHORT COVER / LONG UNWIND), RVOL y presion por
                    OHLC (el "delta proxy" del indicador: NO es delta real).

USO
    from ob_flow_niveles import zonas_ob, flujo
"""

from __future__ import annotations

import io
import json
import math
import os
import sys
import time
import urllib.request

# The optional third-party credit banner includes Unicode unsupported by the
# dashboard's Windows cp1252 stdout. A failed import otherwise hides every OB.
os.environ.setdefault("SMC_CREDIT", "0")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

TS, OP, HI, LO, CL, VOL = 0, 1, 2, 3, 4, 5
UA = {"User-Agent": "automaton-ob"}
TTL = 600.0                  # 10 min: se exporta cada 30, no hace falta mas
VELAS = 600                  # ventana que ve el detector causal
CERCA_PCT = 4.0              # solo OB a menos de esto del precio
MAX_OB = 4
OI_VENTANA = 10              # velas de 5 m para el cambio de OI
OI_MIN_PCT = 0.10
RVOL_LEN = 50

_CACHE = {}


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def klines5m(sym, limite=VELAS):
    """Velas de 5 m del perpetuo. Devuelve [] si el simbolo no existe alla."""
    k = ("k", sym, limite)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    out = []
    try:
        L = _get("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=5m&limit=%d"
                 % (sym, limite))
        out = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])]
               for x in L]
    except Exception:
        out = []
    _CACHE[k] = (time.time(), out)
    return out


# ------------------------------- ORDER BLOCKS -------------------------------
def zonas_ob(sym, cerca_pct=CERCA_PCT, maximo=MAX_OB, closed_only=False):
    """
    Order blocks CAUSALES vigentes cerca del precio.

    Se reusa `bt_ob_short.ob_zonas`, que ya tiene corregidos los DOS sesgos de
    anticipacion: la ventana deslizante y el `visible_desde` (una zona no existe
    hasta que la ventana que la detecta termina).

    Devuelve [{lado, top, bottom, centro, dist_pct, edad, score}] ordenado por
    cercania. `score` es de CALIDAD DESCRIPTIVA (frescura + tamaño), no una
    probabilidad: el OB no paso ninguna prueba de robustez.
    """
    k = ("ob", sym, cerca_pct, maximo, closed_only)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]

    K = klines5m(sym)
    if closed_only:
        cutoff_ms = int(time.time() * 1000)
        # Always drop the last bar returned by this snapshot. Its cached OHLC
        # may be partial even when the wall clock has since passed its close.
        last_open = max((bar[TS] for bar in K), default=0)
        K = [bar for bar in K if bar[TS] < last_open and bar[TS] + 300_000 <= cutoff_ms]
    out = []
    if len(K) >= 300:
        try:
            import bt_ob_short as OBS
        except Exception:
            OBS = None
        if OBS is not None:
            px = K[-1][CL]
            n = len(K)
            for lado, sgn in (("LONG", 1), ("SHORT", -1)):
                try:
                    zs = OBS.ob_zonas(K, lado, 5)
                except Exception:
                    zs = []
                for j, top, bot, visible in zs:
                    if visible > n:            # todavia no era visible
                        continue
                    # vigente: no invalidada por un cierre del otro lado
                    roto = False
                    # Export validity is evaluated using all known closes since
                    # origin; visibility gates when it may be traded, not whether
                    # an already broken block can be resurrected at detection.
                    validity_start = j + 1 if closed_only else max(int(visible), j + 1)
                    for t in range(validity_start, n):
                        if (sgn > 0 and K[t][CL] < bot) or (sgn < 0 and K[t][CL] > top):
                            roto = True
                            break
                    if roto:
                        continue
                    centro = (top + bot) / 2.0
                    d = 100.0 * (centro - px) / px if px else 0.0
                    if abs(d) > cerca_pct:
                        continue
                    edad = n - j
                    ancho = 100.0 * (top - bot) / px if px else 0.0
                    # descriptivo: mas fresco y mas fino = mas "limpio"
                    sc = max(0, min(100, int(round(
                        100.0 - min(edad, 400) / 4.0 - min(ancho, 3.0) * 10.0))))
                    out.append({"lado": lado, "top": float(top), "bottom": float(bot),
                                "centro": centro, "dist_pct": round(d, 2),
                                "edad": int(edad), "score": sc,
                                "source_closed_ts": (K[-1][TS] + 300_000) / 1000.0 if closed_only else None})
            out.sort(key=lambda z: abs(z["dist_pct"]))
            if maximo is not None:
                out = out[:maximo]
    _CACHE[k] = (time.time(), out)
    return out


def seleccionar_ob_exportacion(sym):
    """Nearest barrier plus best descriptive score per side; not a probability.

    Consider all valid blocks within CERCA_PCT using closed candles. Preserve
    the nearest even if a farther block has a higher score. At most three.
    """
    candidates = zonas_ob(sym, maximo=None, closed_only=True)
    if not candidates:
        return []
    nearest = min(candidates, key=lambda z: (abs(z['dist_pct']), -z['score'], z['centro']))
    selected = [nearest]
    seen = {(nearest['lado'], nearest['top'], nearest['bottom'])}
    for side in ('LONG', 'SHORT'):
        available = [z for z in candidates if z['lado'] == side]
        if not available:
            continue
        best = min(available, key=lambda z: (-z['score'], abs(z['dist_pct']), z['edad'], z['centro']))
        identity = (best['lado'], best['top'], best['bottom'])
        if identity not in seen:
            selected.append(best)
            seen.add(identity)
    return selected


# ---------------------------------- FLUJO ----------------------------------
def _oi_hist(sym, periodo="5m", limite=30):
    try:
        return _get("https://fapi.binance.com/futures/data/openInterestHist"
                    "?symbol=%s&period=%s&limit=%d" % (sym, periodo, limite))
    except Exception:
        return []


def flujo(sym):
    """
    El equivalente del bloque FLOW del indicador del usuario, calculado aca.

    · oi_pct     cambio de open interest en OI_VENTANA velas de 5 m
    · estado     matriz precio x OI. OI subiendo NO es alcista por si solo:
                 depende de hacia donde fue el precio.
                     precio sube + OI sube -> LONG BUILD    (dinero nuevo largo)
                     precio baja + OI sube -> SHORT BUILD   (cortos nuevos)
                     precio sube + OI baja -> SHORT COVER   (NO es lo mismo que largo nuevo)
                     precio baja + OI baja -> LONG UNWIND
    · rvol       volumen de la vela contra su media de 50
    · presion    posicion del cierre dentro del rango, -100 a +100. Es un PROXY
                 de OHLC: no hay agresor por trade en velas. NO es delta real.
    """
    k = ("fl", sym)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]

    K = klines5m(sym, 200)
    out = {"oi_pct": None, "estado": "N/A", "rvol": None, "presion": None}
    if len(K) > RVOL_LEN + OI_VENTANA:
        c = K[-1]
        rng = c[HI] - c[LO]
        out["presion"] = round(((c[CL] - c[LO]) - (c[HI] - c[CL])) / rng * 100.0, 1) if rng > 0 else 0.0
        vm = sum(x[VOL] for x in K[-RVOL_LEN - 1:-1]) / RVOL_LEN
        out["rvol"] = round(c[VOL] / vm, 2) if vm > 0 else None
        H = _oi_hist(sym)
        if len(H) > OI_VENTANA:
            try:
                ahora = float(H[-1]["sumOpenInterest"])
                antes = float(H[-1 - OI_VENTANA]["sumOpenInterest"])
                if antes:
                    d = (ahora - antes) / abs(antes) * 100.0
                    out["oi_pct"] = round(d, 2)
                    p_sube = K[-1][CL] > K[-1 - OI_VENTANA][CL]
                    oi_sube = d >= OI_MIN_PCT
                    oi_baja = d <= -OI_MIN_PCT
                    if oi_sube and p_sube:
                        out["estado"] = "LONG BUILD"
                    elif oi_sube and not p_sube:
                        out["estado"] = "SHORT BUILD"
                    elif oi_baja and p_sube:
                        out["estado"] = "SHORT COVER"
                    elif oi_baja and not p_sube:
                        out["estado"] = "LONG UNWIND"
                    else:
                        out["estado"] = "NEUTRAL"
            except (KeyError, ValueError, TypeError):
                pass
    _CACHE[k] = (time.time(), out)
    return out


def lectura_flujo(f):
    """Frase corta del estado de posicionamiento. Interpretacion, no medicion."""
    e = (f or {}).get("estado")
    return {
        "LONG BUILD": "dinero nuevo abriendo largos",
        "SHORT BUILD": "cortos nuevos presionando",
        "SHORT COVER": "cierran cortos - empuje sin conviccion",
        "LONG UNWIND": "cierran largos - caida sin conviccion",
        "NEUTRAL": "sin cambio de posicionamiento",
    }.get(e, "")


if __name__ == "__main__":
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        z = zonas_ob(s)
        f = flujo(s)
        print("=" * 70)
        print("%s  OB vigentes cerca: %d" % (s, len(z)))
        for x in z:
            print("   %-5s [%.6g - %.6g] %+.2f%% edad %d score %d"
                  % (x["lado"], x["bottom"], x["top"], x["dist_pct"], x["edad"], x["score"]))
        print("   flujo: OI %s%% · %s · RVOL %s · presion %s%%  (%s)"
              % (f["oi_pct"], f["estado"], f["rvol"], f["presion"], lectura_flujo(f)))
