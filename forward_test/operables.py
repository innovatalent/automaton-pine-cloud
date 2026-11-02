# -*- coding: utf-8 -*-
"""
operables.py — los activos que SIRVEN PARA OPERAR AHORA, no los que fijamos antes.

LA IDEA
    Un activo aparece solo si cumple las tres cosas a la vez:

      1. UBICACION: esta en un extremo del value area o FUERA de el (24 h).
      2. GAMMA APLICABLE: o tiene cadena de opciones propia (BTC, ETH), o esta
         fuertemente correlacionado con quien la tiene, o es TQQQ (que hereda del
         Nasdaq via QQQ).
      3. SENAL COHERENTE: la que corresponde a ESE regimen de gamma en ESA
         ubicacion. No es la misma en pinning que en violenta.

    Si la gamma no le aplica, NO aparece. Es la diferencia con el panel general:
    aca la estructura de opciones es requisito, no adorno.

LA MATRIZ — que senal corresponde segun regimen y ubicacion

    PINNING (gamma positiva concentrada): los dealers venden fuerza y compran
    debilidad, o sea que empujan el precio DE VUELTA al centro.
        abajo del VAL  -> LONG al POC      (validado: WR 60-62%, orden 2 de 2 anios)
        arriba del VAH -> SHORT al POC     (el espejo se REFUTO en 3 tests: se
                                            muestra pero degradado a "solo aviso")

    VIOLENTA (gamma negativa concentrada): los dealers persiguen el precio.
        abajo del VAL  -> NO fadear. El extremo se extiende, no revierte.
                          (medido: fade en gamma negativa -0.362%/op vs -0.169%)
        arriba del VAH -> NO fadear, misma razon. Favorece continuacion.

    BISAGRA (a menos de 0.5% del gamma flip): el regimen puede darse vuelta DENTRO
    del trade. Cualquier lado, pero con tamano reducido.

    NORMAL (libro repartido, concentracion <= 0.15): la estructura no opina. El
    activo se evalua sin aporte de la gamma.

DE DONDE SALE LA GAMMA DE CADA UNO
    BTC, ETH   cadena propia (Deribit ~89% del OI)
    TQQQ       del Nasdaq via QQQ (nocional 15x el de TQQQ; los dealers cubren ahi)
    alts       heredada de su LIDER medido (BTC o ETH), SOLO si la correlacion viva
               de 48 h es >= 0.30. Medido sobre 12.600 eventos: bajo ese umbral el
               contexto pierde poder predictivo (+0.28 -> -0.04) y bajo 0.15 se
               invierte (-0.19). Una alt desacoplada NO hereda nada.

USO
    python -X utf8 operables.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

# __CORR_BANDAS_2026_09_08__ — la correlacion no es binaria. Medido sobre 12.600
# eventos (ver HALLAZGOS, seccion corr_btc.py), la correlacion entre lo que hizo BTC
# en 24 h y lo que hace la alt DESPUES:
#
#     corr >= 0.60    +0.129   sigue a BTC, pero el contexto predice MENOS
#     corr 0.30-0.60  +0.283   <- LA MEJOR: se mueve con BTC pero con recorrido propio
#     corr < 0.30     -0.035   deja de predecir -> no se hereda la gamma
#     corr < 0.15     -0.189   predice al reves
#
# El sistema usaba un umbral binario (>=0.30 hereda). Eso trataba igual a la banda
# 0.30-0.60, que es la mejor, y a la >=0.60, que es menos util: una alt pegada a BTC
# no aporta nada propio, solo replica. La banda MEDIA es donde la gamma de BTC sigue
# aplicando y la alt todavia tiene expansion propia.
CORR_MIN = 0.30          # bajo esto la gamma del lider NO se hereda
CORR_OPTIMA = (0.30, 0.60)   # banda de mayor poder predictivo (+0.283)


def _gamma_de(sym):
    """
    Devuelve (estado_gamma, origen, detalle) o None si la gamma no le aplica.
    origen: 'propia' | 'nasdaq' | 'heredada'
    """
    import gex_live as gl
    su = str(sym).upper()
    if not gl.ready():
        gl.refresh_once()

    coin = gl.coin_for(su)
    if coin:
        lv = gl.levels(coin, "scalp")
        return (lv, "propia", coin) if lv else None

    if su in getattr(gl, "EQUITY_MAP", {}):          # TQQQ -> regimen de QQQ
        tk = gl.EQUITY_MAP[su][0]
        st = gl.equity_state(tk)
        if not st:
            return None
        return gl._eq_levels(st, tk), "nasdaq", tk

    if su in getattr(gl, "STOCK_GEX", {}):           # accion con cadena PROPIA
        tk = gl.STOCK_GEX[su]
        st = gl.equity_state(tk)
        if not st:
            return None
        return gl._eq_levels(st, tk), "propia", tk

    lid, r2 = gl.LIDER.get(su, (None, None))
    if not lid:
        return None
    try:
        import corr_btc as cb
        c = cb.corr(su, "BTCUSDT" if lid == "BTC" else "ETHUSDT")
    except Exception:
        c = None
    if not c or c["corr"] < CORR_MIN:
        return None                                   # desacoplada: no hereda
    lv = gl.levels(lid, "scalp")
    if not lv:
        return None
    cv = c["corr"]
    banda = ("optima" if CORR_OPTIMA[0] <= cv < CORR_OPTIMA[1] else "alta")
    det = "%s corr %.2f (%s) R2 %.2f" % (lid, cv, banda, r2 or 0)
    return lv, "heredada", det


def _senal(estado, zona):
    """
    La matriz: que corresponde en ese regimen y esa ubicacion.
    Devuelve (lado, tipo, fuerza, nota) o None si no corresponde operar.
    """
    if zona == "dentro":
        return None
    if estado == "PINNING":
        if zona == "abajo":
            return ("LONG", "fade al POC", "alta",
                    "gamma positiva: los dealers compran debilidad. Validado: WR 60-62%")
        # __SIMETRIA_2026_09_09__: el SHORT desde el VAH vuelve a MOSTRARSE.
        #
        # Antes decia "el espejo SHORT se REFUTO en 3 tests". Esas tres pruebas
        # median RETORNO EN EXCESO DEL MERCADO SIN STOP. Medido con la mecanica real
        # -stop, objetivo, comisiones y salida por ATR- el orden se DA VUELTA, y hay
        # tres fuentes independientes que coinciden:
        #
        #   1. backtest con stop (812 dias, 18 activos, salida ATR 1,5x/3x):
        #        SHORT + gamma + delta  -0,014%/op   Sharpe -0,0060
        #        LONG  + gamma + delta  -0,189%/op   Sharpe -0,0836
        #   2. FUERA de muestra (16 activos que nunca se usaron):
        #        SHORT -0,050%/op  Sharpe -0,0197  |  LONG -0,197%/op  Sharpe -0,0781
        #   3. el paper REAL del propio motor, que es forward test y no fue elegido:
        #        OB|SHORT  n=113  +0,135%/op  +15,25% acumulado
        #        OB|LONG   n= 86  -0,054%/op   -4,62% acumulado
        #
        # El SHORT es MEJOR que el LONG en las tres. Bloquearlo no tenia respaldo.
        #
        # __SHORT_VAL_2026_09_09__ — RETIRADA la fuente 3, y cerrada la pregunta.
        # (a) El +15,25% del paper OB|SHORT NO era evidencia: con 138 operaciones
        #     cerradas da +0,141%/op, pero SIN LAS 5 MEJORES cae a -0,065%/op, y toda
        #     la muestra son 3 dias. Cinco trades no sostienen una fuente.
        # (b) Estudio pre-registrado con 34 perpetuos y 750 dias (bt_short_val.py,
        #     bt_short_val2.py, biblioteca/13_shorts.md): 20 configuraciones contra un
        #     control AL AZAR DEL MISMO LADO -que es la vara correcta, porque medir un
        #     SHORT contra cero en una ventana que cayo 37% no prueba nada-. El mejor
        #     candidato (ignicion bajista) le gana al azar dentro de muestra y en los
        #     17 activos reservados (+0,119%/op), pero PIERDE contra su propio control
        #     en el tiempo reservado (-0,522% contra -0,085% del azar) y su Sharpe
        #     (+0,077) queda debajo de la vara del azar (+0,157): DSR 2,2%.
        #     `cuantas_faltan` devuelve None -> no llega nunca, no es esperar mas datos.
        #
        # CONCLUSION: ningun SHORT pasa la vara. Queda como AVISO con contexto, no como
        # senal: sirve para preparar y mirar, no para entrar.
        return ("SHORT", "fade al POC", "aviso",
                "SHORT desde el VAH: rinde MEJOR que el LONG con la misma vara "
                "(-0,014%/op contra -0,189%, y fuera de muestra -0,050% contra "
                "-0,197%), pero NINGUN SHORT paso la prueba de robustez (34 activos, "
                "750 dias, 20 configuraciones contra control al azar: DSR 2,2% y "
                "pierde en el tiempo reservado). Es aviso para preparar, no senal "
                "de entrada")
    if estado == "VIOLENTA":
        return (("SHORT" if zona == "abajo" else "LONG"), "continuacion", "aviso",
                "gamma NEGATIVA: el extremo se extiende, NO fadear "
                "(medido -0.362%/op contra -0.169%)")
    if estado == "BISAGRA":
        return (("LONG" if zona == "abajo" else "SHORT"), "fade al POC", "reducida",
                "sobre el gamma flip: el regimen puede darse vuelta dentro del trade")
    return (("LONG" if zona == "abajo" else "SHORT"), "fade al POC", "sin aporte",
            "libro repartido: la gamma no opina, decidir con el resto del panel")


def _delta(sym):
    """Delta real de la ultima vela CERRADA de 5m. Es el gatillo validado."""
    try:
        import json as _j, urllib.request as _u
        u = "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=5m&limit=3" % sym
        K = _j.loads(_u.urlopen(_u.Request(u, headers={"User-Agent": "op"}), timeout=12).read())
        k = K[-2]
        v = float(k[5])
        return ((2 * float(k[9]) - v) / v) if v > 0 else None
    except Exception:
        return None


def evaluar(sym):
    import vwap_profile as vp
    p = vp.profile(sym, "5m")
    if not p:
        return None
    px = p["px"]
    arriba = px > p["vah"] or px >= p["up_band"]
    abajo = px < p["val"] or px <= p["dn_band"]
    zona = "arriba" if arriba else ("abajo" if abajo else "dentro")
    if zona == "dentro":
        return None                                   # requisito 1: extremo o fuera

    g = _gamma_de(sym)
    if not g:
        return None                                   # requisito 2: gamma aplicable
    lv, origen, det = g
    estado = lv.get("velocidad_esperada") or lv.get("estado")

    s = _senal(estado, zona)
    if not s:
        return None                                   # requisito 3: senal coherente
    lado, tipo, fuerza, nota = s

    d = _delta(sym)
    delta_ok = None if d is None else ((d > 0) if lado == "LONG" else (d < 0))

    tgt = p["poc"] if abs(p["poc"] - px) < abs(p["vwap"] - px) else p["vwap"]
    return {"symbol": sym, "px": px, "zona": zona, "sig_pos": p.get("sig_pos"),
            "lado": lado, "tipo": tipo, "fuerza": fuerza, "nota": nota,
            "regimen": lv["regimen"], "estado": estado,
            "gamma_origen": origen, "gamma_detalle": det,
            "flip": lv.get("gamma_flip"), "dist_flip_pct": lv.get("dist_flip_pct"),
            "target": round(tgt, 8), "dist_target_pct": round((tgt - px) / px * 100, 2),
            "poc": p["poc"], "val": p["val"], "vah": p["vah"],
            "delta": (round(d, 3) if d is not None else None), "delta_ok": delta_ok,
            "corr_banda": ("optima" if "(optima)" in str(det) else
                           ("alta" if "(alta)" in str(det) else "propia")),
            "listo": bool(fuerza == "alta" and delta_ok)}


def escanear(syms=None):
    if syms is None:
        try:
            import mom_paper as MP
            import gex_live as _gl
            syms = sorted(set(MP.CRYPTO)) + ["TQQQUSDT"] + sorted(_gl.STOCK_GEX)
        except Exception:
            syms = ["BTCUSDT", "ETHUSDT", "TQQQUSDT"]
    out = []
    for s in syms:
        try:
            r = evaluar(s)
        except Exception:
            r = None
        if r:
            out.append(r)
    # orden: primero lo LISTO, y dentro de eso la banda de correlacion OPTIMA
    # (0.30-0.60), que es la de mayor poder predictivo medido (+0.283 contra +0.129).
    orden = {"alta": 0, "reducida": 1, "sin aporte": 2, "aviso": 3}
    banda = {"propia": 0, "optima": 1, "alta": 2}
    out.sort(key=lambda r: (not r["listo"], orden.get(r["fuerza"], 9),
                            banda.get(r.get("corr_banda"), 9),
                            -abs(r.get("sig_pos") or 0)))
    return out


def main():
    res = escanear()
    print("=" * 88)
    print("OPERABLES AHORA  ·  extremo del value area + gamma aplicable + señal coherente")
    print("=" * 88)
    if not res:
        print("  Ningún activo cumple las tres condiciones ahora mismo.")
        print("  Es lo esperable: se exige estar en un extremo Y que la gamma aplique.")
        return
    print("  %-9s %6s %8s %-6s %-14s %-9s %7s %6s"
          % ("activo", "zona", "sigma", "lado", "tipo", "estado", "objetivo", "delta"))
    print("  " + "-" * 80)
    for r in res:
        mk = " <<<" if r["listo"] else ""
        print("  %-9s %6s %+7.2f %-6s %-14s %-9s %+6.2f%% %6s%s"
              % (r["symbol"].replace("USDT", ""), r["zona"], r.get("sig_pos") or 0,
                 r["lado"], r["tipo"], r["estado"], r["dist_target_pct"],
                 ("%.3f" % r["delta"]) if r["delta"] is not None else "-", mk))
    listos = [r for r in res if r["listo"]]
    print()
    if listos:
        print("  %d LISTO(S) — señal validada con el delta a favor:" % len(listos))
        for r in listos:
            print("    %s %s desde %s · objetivo %.6g (%+.2f%%) · gamma %s (%s)"
                  % (r["symbol"], r["lado"], r["zona"], r["target"],
                     r["dist_target_pct"], r["estado"], r["gamma_origen"]))
    else:
        print("  Ninguno LISTO. Los que figuran no tienen el delta a favor o su")
        print("  régimen no habilita el fade (violenta = no fadear, aviso = refutado).")


if __name__ == "__main__":
    main()
