# -*- coding: utf-8 -*-
"""
bt_ob_short.py — el order block SHORT del motor, medido como es, con OOS reservado.

POR QUE ESTE Y NO OTRO
    De todo lo probado, el OB SHORT es el unico candidato con evidencia que NO vino de
    buscar: el motor lo viene operando en papel y lleva 114 operaciones, 59 activos,
    +0,142% por operacion y +16,17% acumulado. Nadie eligio esa cohorte despues de ver
    el resultado: el motor la fue tomando en vivo.

    Y hay una razon concreta para sospechar que las mediciones anteriores estaban mal:
    en `bt_bpr.py` el "order block" era una reconstruccion TOSCA en velas de 1 hora
    (vela contraria + desplazamiento), y dio -0,189%/op. El motor usa otra cosa por
    completo:

        · la libreria smartmoneyconcepts, con swing_length=5
        · en velas de 5 MINUTOS, no de 1 hora
        · toque del borde y cierre del otro lado, en la vela CERRADA
        · sin ningun cierre que haya invalidado la zona desde que se formo
        · PRIMERA confirmacion: si el OB ya fue tocado y confirmado antes, no vale
        · el stop es el BORDE de la zona, no un porcentaje fijo

    Nunca se midio eso. Se midio una aproximacion que no es lo mismo.

EL METODO, DECIDIDO ANTES DE MIRAR NADA
    La leccion de esta sesion fue que reservar activos DESPUES de ver los resultados no
    sirve. Asi que aca la particion se declara al principio, con semilla fija, y no se
    toca:

        DENTRO   los activos con indice par en la lista ordenada
        FUERA    los impares

    Se busca y se ajusta SOLO en los de adentro. Los de afuera se miran UNA vez, al
    final, y ese numero es el que decide. Si el resultado de afuera no acompana, no
    hay hallazgo, sin importar lo lindo que se vea adentro.

    Se cuentan TODOS los intentos para el Sharpe deflactado.

USO
    python -X utf8 bt_ob_short.py
"""

from __future__ import annotations

import io
import json
import math
import os
import random
import statistics as st
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from robustez import sharpe, deflactado, evaluar, informe   # noqa: E402
import funding_db as FDB                                    # noqa: E402

CACHE = os.path.join(HERE, "bt_cache", "m5")
PAGINAS = 14                 # 14 x 1000 = 14.000 velas de 5m ~ 48 dias
COSTO = 0.11
MIN_TR = 30
TS, OP, HI, LO, CL, VOL = 0, 1, 2, 3, 4, 5

# El universo, ordenado y fijo. La particion sale del indice: par = dentro, impar =
# fuera. Es arbitraria y esta decidida ANTES de ver un solo resultado.
UNIV = sorted([
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT",
    "AVAXUSDT", "DOTUSDT", "LTCUSDT", "UNIUSDT", "XLMUSDT", "NEARUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "SUIUSDT", "AAVEUSDT", "BNBUSDT", "TRXUSDT", "BCHUSDT",
    "HBARUSDT", "INJUSDT", "FILUSDT", "ATOMUSDT", "ETCUSDT", "ICPUSDT", "SEIUSDT",
    "1000PEPEUSDT", "WLDUSDT", "GALAUSDT", "ENAUSDT",
])
DENTRO = [s for i, s in enumerate(UNIV) if i % 2 == 0]
FUERA = [s for i, s in enumerate(UNIV) if i % 2 == 1]


def klines5m(sym, paginas=PAGINAS):
    """[ts, open, high, low, close, volumen] en velas de 5 minutos."""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "%s.json" % sym)
    if os.path.exists(p):
        try:
            return json.load(io.open(p, encoding="utf-8"))
        except Exception:
            pass
    out, end = {}, None
    for _ in range(paginas):
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=5m&limit=1000"
             % sym) + (("&endTime=%d" % end) if end else "")
        try:
            L = json.loads(urllib.request.urlopen(urllib.request.Request(
                u, headers={"User-Agent": "bt5"}), timeout=30).read())
        except Exception:
            break
        if not L:
            break
        antes = len(out)
        for k in L:
            out[int(k[0])] = [int(k[0]) // 1000, float(k[1]), float(k[2]),
                              float(k[3]), float(k[4]), float(k[5])]
        if len(out) == antes:
            break
        end = int(L[0][0]) - 1
        time.sleep(0.04)
    K = [out[t] for t in sorted(out)]
    if K:
        io.open(p, "w", encoding="utf-8").write(json.dumps(K))
    return K


VENTANA_OB = 600         # las velas que el motor tiene a la vista al decidir
PASO_OB = 120            # cada cuanto se recalcula la ventana


def ob_zonas(K, side, swing=5):
    """
    Zonas de order block SIN VER EL FUTURO.

    EL BUG QUE ESTO CORRIGE
        La primera version corria smc.ob() sobre el historico ENTERO de una vez. Eso
        parece inofensivo y no lo es: smc.swing_highs_lows(swing_length=5) confirma un
        swing mirando 5 velas a cada lado, asi que una zona marcada en la barra j usa
        informacion de j+5. Al recorrer el historico completo, TODAS las zonas quedan
        marcadas con conocimiento del futuro.

        El sintoma fue inconfundible: el lado LONG daba 97,6% de aciertos y factor de
        beneficio 30,8. Eso no existe. Era el algoritmo eligiendo, con el diario del
        lunes, cuales zonas iban a funcionar.

    COMO SE CORRIGE
        Se replica lo que hace el motor en vivo: mirar solo hacia atras. Se recorre en
        ventanas de VENTANA_OB velas y en cada una se aceptan unicamente las zonas
        cuyo swing ya estaba confirmado dentro de esa ventana, o sea las que terminan
        al menos `swing` velas antes del borde. Avanzando de a PASO_OB, cada zona se
        detecta con la misma informacion que habria tenido el motor en ese momento.
    """
    import pandas as pd
    from smartmoneyconcepts import smc
    sgn = 1 if side == "LONG" else -1
    vistas = {}
    n = len(K)
    for fin in range(VENTANA_OB, n + PASO_OB, PASO_OB):
        fin = min(fin, n)
        ini = max(0, fin - VENTANA_OB)
        sub = K[ini:fin]
        if len(sub) < 100:
            continue
        df = pd.DataFrame([[k[OP], k[HI], k[LO], k[CL], k[VOL]] for k in sub],
                          columns=["open", "high", "low", "close", "volume"])
        try:
            o = smc.ob(df, smc.swing_highs_lows(df, swing_length=swing))
        except Exception:
            continue
        OB, T, B = o["OB"].values, o["Top"].values, o["Bottom"].values
        # SEGUNDO BUG, mas sutil que el primero: no alcanza con excluir las ultimas
        # velas de la ventana. La zona se DETECTA cuando se corre smc.ob sobre la
        # ventana que termina en `fin`, o sea que en vivo recien es visible a partir
        # de `fin`. Si se la usa para una confirmacion anterior a `fin`, se esta
        # usando una zona que todavia no existia.
        #
        # El sintoma: entradas al azar con la MISMA salida daban 29% de aciertos (lo
        # que corresponde con objetivo al doble del stop), y el order block daba 70%
        # en los dos lados a la vez. Long y short ganando juntos no existe.
        #
        # Se guarda junto a cada zona la barra `visible_desde`, y en `senales` no se
        # acepta ninguna confirmacion anterior a ella.
        for j in range(len(OB) - swing - 1):
            if OB[j] == sgn:
                g = ini + j
                if g not in vistas:
                    vistas[g] = (g, float(T[j]), float(B[j]), fin)
    return [vistas[g] for g in sorted(vistas)]


def senales(K, side, max_age, swing=5):
    """
    Reproduce la regla del motor: la vela CERRADA toca el borde y cierra del otro
    lado, sin que la zona se haya invalidado antes, y es la PRIMERA confirmacion.
    Devuelve [(i, top, bottom, edad)].
    """
    zonas = ob_zonas(K, side, swing)
    if not zonas:
        return []
    c = [k[CL] for k in K]; hi = [k[HI] for k in K]; lo = [k[LO] for k in K]
    sgn = 1 if side == "LONG" else -1
    out = []
    for j, top, bot, visible in zonas:
        confirmado = False
        # la confirmacion no puede ser anterior al momento en que la zona era visible
        desde = max(j + 3, visible)
        for k in range(desde, min(len(K), j + 3 + max_age + 1)):
            if (sgn > 0 and c[k] < bot) or (sgn < 0 and c[k] > top):
                break                                  # la zona quedo invalidada
            toca = ((lo[k] <= top and c[k] > top) if sgn > 0
                    else (hi[k] >= bot and c[k] < bot))
            if toca:
                if not confirmado:                     # solo la PRIMERA
                    out.append((k, top, bot, k - j))
                confirmado = True
                break
    return sorted(out)


def atr5(K, i, n=14):
    tr = []
    for j in range(max(1, i - n), i):
        tr.append(max(K[j][HI] - K[j][LO], abs(K[j][HI] - K[j - 1][CL]),
                      abs(K[j][LO] - K[j - 1][CL])))
    return (sum(tr) / len(tr)) if tr else 0.0


def operar(D, FN, side, max_age, k_tp, vto_barras, atr_bound=True, swing=5,
           min_vol=0.0, delta_ok=False):
    """
    Simula como opera el motor: entrada al cierre de la vela de confirmacion, stop en
    el borde de la zona (acotado por ATR si atr_bound), objetivo a k_tp veces el
    riesgo, cierre a mercado al vencer. Una posicion por activo a la vez.
    """
    sgn = 1 if side == "LONG" else -1
    tr = []
    for s, K in D.items():
        F = FN.get(s) or []
        try:
            sig = senales(K, side, max_age, swing)
        except Exception:
            continue
        libre = -1
        for i, top, bot, edad in sig:
            if i <= libre or i + vto_barras + 2 >= len(K):
                continue
            e = K[i][CL]
            sl = bot if sgn > 0 else top
            slp = abs(e - sl) / e * 100.0
            if atr_bound:
                a = atr5(K, i) / e * 100.0 if e > 0 else 0
                if a > 0:
                    slp = min(2.5, max(0.3, min(2.5 * a, max(0.6 * a, slp))))
            if slp <= 0.05 or slp > 5:
                continue
            if min_vol > 0:
                v20 = sum(K[j][VOL] for j in range(max(0, i - 20), i)) / 20.0
                if v20 <= 0 or K[i][VOL] / v20 < min_vol:
                    continue
            if delta_ok:
                r = max(K[i][HI] - K[i][LO], 1e-12)
                d = (2 * (K[i][CL] - K[i][LO]) - r) / r
                if d * sgn <= 0:
                    continue
            sl_px = e * (1 - slp / 100.0 * sgn)
            tp_px = e * (1 + k_tp * slp / 100.0 * sgn)
            res = jsal = None
            for j in range(i + 1, min(i + 1 + vto_barras, len(K))):
                g_sl = (K[j][LO] <= sl_px) if sgn > 0 else (K[j][HI] >= sl_px)
                g_tp = (K[j][HI] >= tp_px) if sgn > 0 else (K[j][LO] <= tp_px)
                if g_sl:
                    res = -slp - COSTO; jsal = j; break
                if g_tp:
                    res = k_tp * slp - COSTO; jsal = j; break
            if res is None:
                jsal = min(i + vto_barras, len(K) - 1)
                res = (K[jsal][CL] / e - 1) * 100 * sgn - COSTO
            libre = jsal
            f = FDB.acumulado(F, K[i][TS], K[jsal][TS], sgn) if F else 0.0
            tr.append((res + f, K[i][TS], s))
    return tr


def resumen(tr, minimo=MIN_TR):
    if len(tr) < minimo:
        return None
    v = [x[0] for x in tr]
    g = sum(x for x in v if x > 0); p = -sum(x for x in v if x < 0)
    r = random.Random(41); o = []
    for _ in range(2000):
        sm = [v[r.randrange(len(v))] for _ in range(max(10, int(len(v) * .9)))]
        o.append(sum(sm) / len(sm))
    o.sort()
    return {"n": len(v), "e": sum(v) / len(v),
            "wr": 100.0 * sum(1 for x in v if x > 0) / len(v),
            "pf": (g / p) if p > 0 else 99.9, "p5": o[100],
            "sr": sharpe(v), "v": v, "tr": tr}


def cargar(simbolos):
    D, FN = {}, {}
    for s in simbolos:
        K = klines5m(s)
        if len(K) >= 5000:
            D[s] = K
            try:
                FN[s] = FDB.cargar(s)
            except Exception:
                FN[s] = []
    return D, FN


def main():
    print("=" * 96)
    print("ORDER BLOCK SHORT · el del motor (smartmoneyconcepts, 5m), con OOS reservado")
    print("=" * 96)
    print("  partición declarada ANTES de mirar nada: índice par = dentro, impar = fuera")
    print("  DENTRO (%d): %s" % (len(DENTRO), ", ".join(s.replace("USDT", "") for s in DENTRO)))
    print("  FUERA  (%d): %s\n" % (len(FUERA), ", ".join(s.replace("USDT", "") for s in FUERA)))

    Dd, FNd = cargar(DENTRO)
    print("  cargados %d de %d activos de DENTRO · %d velas de 5m (~%.0f días)\n"
          % (len(Dd), len(DENTRO), min(len(v) for v in Dd.values()) if Dd else 0,
             (min(len(v) for v in Dd.values()) / 288.0) if Dd else 0))
    if len(Dd) < 6:
        print("  sin datos suficientes"); return

    CAB = ("  %-42s %6s %9s %7s %5s %10s %8s"
           % ("configuración", "n", "neto/op", "WR", "PF", "Sharpe/op", "MC p5"))

    def fila(nom, r):
        if not r:
            print("  %-42s   sin muestra" % nom); return
        mk = "  <-- p5>0" if (r["p5"] or -1) > 0 else ""
        print("  %-42s %6d %+8.3f%% %6.1f%% %5.2f %+10.4f %+8.3f%s"
              % (nom, r["n"], r["e"], r["wr"], r["pf"], r["sr"] or 0, r["p5"] or 0, mk))
        return r

    # ── búsqueda, SOLO dentro de muestra ───────────────────────────────────
    print("  BÚSQUEDA (sólo en los activos de DENTRO)")
    print(CAB); print("  " + "-" * 92)
    intentos = {}
    for side in ("SHORT", "LONG"):
        for max_age in (20, 60):
            for k_tp in (1.5, 2.0, 3.0):
                for vto in (144, 288):        # 12 h y 24 h en velas de 5m
                    r = resumen(operar(Dd, FNd, side, max_age, k_tp, vto))
                    if r:
                        nom = "%s · edad<=%d · TP %.1fR · vto %dh" % (
                            side, max_age, k_tp, vto // 12)
                        intentos[nom] = r
                        if side == "SHORT" or k_tp == 2.0:
                            fila(nom, r)
    print()

    print("  FILTROS del motor sobre el mejor lado")
    for nom, kw in (("+ volumen >= 1,0x", dict(min_vol=1.0)),
                    ("+ delta a favor", dict(delta_ok=True)),
                    ("+ volumen + delta", dict(min_vol=1.0, delta_ok=True)),
                    ("sin acotar el stop por ATR", dict(atr_bound=False))):
        r = resumen(operar(Dd, FNd, "SHORT", 20, 2.0, 288, **kw))
        if r:
            intentos["SHORT " + nom] = r
        fila("SHORT · edad<=20 · TP 2R " + nom, r)
    print()

    # la mejor de adentro, por Sharpe
    cand = [(r["sr"], k) for k, r in intentos.items()
            if k.startswith("SHORT") and r["sr"] is not None]
    if not cand:
        print("  ninguna configuración SHORT con muestra"); return
    cand.sort(reverse=True)
    mejor_sr, mejor = cand[0]
    print("  MEJOR de adentro: %s (Sharpe %+.4f)\n" % (mejor, mejor_sr))

    # ── la prueba que decide: FUERA, una sola vez ──────────────────────────
    print("=" * 96)
    print("  AHORA SÍ: los activos de FUERA, mirados por primera y única vez")
    print("=" * 96)
    Df, FNf = cargar(FUERA)
    print("  cargados %d de %d activos de FUERA\n" % (len(Df), len(FUERA)))
    if len(Df) < 6:
        print("  sin datos suficientes afuera"); return

    print(CAB); print("  " + "-" * 92)
    # se corre EXACTAMENTE la mejor configuracion, sin tocar nada
    import re
    m = re.match(r"SHORT · edad<=(\d+) · TP ([\d.]+)R · vto (\d+)h", mejor)
    if m:
        ma, ktp, vh = int(m.group(1)), float(m.group(2)), int(m.group(3)) * 12
        kw = {}
    else:
        ma, ktp, vh = 20, 2.0, 288
        kw = ({"min_vol": 1.0} if "volumen" in mejor and "delta" not in mejor else
              {"delta_ok": True} if "delta" in mejor and "volumen" not in mejor else
              {"min_vol": 1.0, "delta_ok": True} if "volumen" in mejor and "delta" in mejor else
              {"atr_bound": False} if "sin acotar" in mejor else {})
    r_dentro = intentos[mejor]
    r_fuera = resumen(operar(Df, FNf, "SHORT", ma, ktp, vh, **kw))
    fila("la misma config · DENTRO", r_dentro)
    fila("la misma config · FUERA", r_fuera)
    print()

    if r_fuera:
        print("  DESGLOSE de FUERA")
        for s in sorted(set(x[2] for x in r_fuera["tr"])):
            v = [x[0] for x in r_fuera["tr"] if x[2] == s]
            if len(v) >= 5:
                print("     %-14s n=%-4d %+7.3f%%  acum %+7.2f%%"
                      % (s.replace("USDT", ""), len(v), sum(v) / len(v), sum(v)))
        print()
        # walk-forward sobre el conjunto
        todos = r_dentro["tr"] + r_fuera["tr"]
        print("  WALK-FORWARD por quincena (dentro + fuera)")
        tt = sorted(x[1] for x in todos)
        if tt:
            paso = (tt[-1] - tt[0]) / 4.0
            ok = tot = 0
            for q in range(4):
                a, b = tt[0] + q * paso, tt[0] + (q + 1) * paso
                v = [x[0] for x in todos if a <= x[1] < b]
                if len(v) < 20:
                    continue
                tot += 1; e = sum(v) / len(v); ok += 1 if e > 0 else 0
                print("     %s  n=%-4d %+8.4f%%  %s"
                      % (time.strftime("%Y-%m-%d", time.gmtime(a)), len(v), e,
                         "OK" if e > 0 else "pierde"))
            print("     -> %d de %d tramos\n" % (ok, tot))

    # ── POR CLASE DE ACTIVO ────────────────────────────────────────────────
    # Pedido del usuario: validar por separado cripto, acciones, materias primas y
    # TQQQ. Son mercados distintos -horario, liquidez, quien esta del otro lado- y no
    # hay razon para suponer que un gatillo funcione igual en los cuatro.
    #
    # OJO CON LA MUESTRA: partir en cuatro deja cohortes chicas, y con pocas
    # operaciones cualquier diferencia puede ser ruido. Se informa el n al lado de
    # cada numero para que se lea con esa advertencia.
    print("=" * 96)
    print("  POR CLASE DE ACTIVO · cripto / acciones / materias primas / TQQQ")
    print("=" * 96)
    CLASES = {
        "acciones":  ["AAPLUSDT", "MSFTUSDT", "NVDAUSDT", "TSLAUSDT", "METAUSDT",
                      "AMZNUSDT", "GOOGLUSDT", "AMDUSDT", "NFLXUSDT", "COINUSDT",
                      "MUUSDT", "AVGOUSDT", "PLTRUSDT", "HOODUSDT"],
        "materias":  ["XAUUSDT", "XAGUSDT", "CLUSDT"],
        "apalancados": ["TQQQUSDT", "SOXLUSDT"],
    }
    print("  %-38s %6s %9s %7s %5s %10s %8s"
          % ("clase · lado", "n", "neto/op", "WR", "PF", "Sharpe/op", "MC p5"))
    print("  " + "-" * 92)
    # cripto: lo ya cargado (dentro + fuera juntos, que es toda la muestra)
    for lado in ("SHORT", "LONG"):
        tr = (operar(Dd, FNd, lado, ma, ktp, vh, **kw)
              + operar(Df, FNf, lado, ma, ktp, vh, **kw))
        fila("cripto (%d activos) · %s" % (len(Dd) + len(Df), lado), resumen(tr))
    print()
    for cl, syms in CLASES.items():
        Dc, FNc = {}, {}
        for sy in syms:
            try:
                K = klines5m(sy)
            except Exception:
                continue
            if len(K) >= 5000:
                Dc[sy] = K
                try:
                    FNc[sy] = FDB.cargar(sy)
                except Exception:
                    FNc[sy] = []
        if not Dc:
            print("  %-38s   sin datos en Binance futuros" % cl); continue
        for lado in ("SHORT", "LONG"):
            r = resumen(operar(Dc, FNc, lado, ma, ktp, vh, **kw), minimo=20)
            fila("%s (%d activos) · %s" % (cl, len(Dc), lado), r)
        print()
    print("  NOTA: las acciones y los ETF apalancados solo se mueven en horario de")
    print("  Wall Street, asi que su muestra es mucho menor y las velas nocturnas son")
    print("  planas. Si una clase queda con pocas operaciones, no se puede concluir.")
    print()

    # ── la vara completa ───────────────────────────────────────────────────
    sr_int = [r["sr"] for r in intentos.values() if r["sr"] is not None]
    print("  VEREDICTO con la vara de robustez.py (%d intentos contados)" % len(sr_int))
    ev = evaluar(r_dentro["v"], sr_int,
                 r_fuera["v"] if r_fuera else None, p5=r_dentro["p5"], unidad="op")
    informe(mejor, ev)


if __name__ == "__main__":
    main()
