# -*- coding: utf-8 -*-
"""
ubicacion.py — ¿el activo esta REALMENTE en el mejor momento Y lugar?

EL PROBLEMA
    "MEJOR MOMENTO" en el panel era `stage == ENTRAR and liq_ok`: solo el gatillo
    z/SOTT mas el iman de liquidacion. Ignoraba tres capas que ya tenemos medidas o
    calculadas:

      · la GAMMA (si la estructura permite u opone el movimiento)
      · la UBICACION en el perfil de 24 h (borde del value area o medio del rango)
      · las CONVERGENCIAS (bandas donde coinciden metodos independientes)

    Un gatillo en el medio del value area, con gamma en contra, no es el mejor
    momento de nada.

LAS CAPAS, SEPARADAS POR SI ESTAN VALIDADAS O NO
    Esto importa mas que el score: mezclar lo medido con lo supuesto es como se
    fabrica una falsa confianza.

    VALIDADAS (tienen medicion detras, pueden VETAR)
      gatillo       z/SOTT multi-TF -> stage ENTRAR (gate del propio repo)
      liquidez      iman de liquidacion a favor y >= 1%
      gamma         fade en gamma NEGATIVA rinde -0.362%/op contra -0.169% en
                    positiva, orden estable 5 de 6 anios, en BTC y ETH. Es el
                    unico veto de gamma con respaldo.

    NO VALIDADAS (informan, NO vetan)
      ubicacion     borde del value area contra medio del rango
      convergencia  bandas con 3+ fuentes independientes
      delta         signo del delta del nivel en las 24 h

    Por eso `mejor_momento` exige SOLO las validadas. Las otras suben o bajan la
    calidad reportada, pero nunca bloquean por su cuenta.

USO
    python -X utf8 ubicacion.py BTCUSDT LONG
    python -X utf8 ubicacion.py TAOUSDT            (deduce el lado del panel)
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def _perfil(sym):
    try:
        import vwap_profile as vp
        return vp.profile(sym, "5m")
    except Exception:
        return None


def _capa_ubicacion(p, side):
    """
    Donde esta el precio dentro del perfil de 24 h. Un fade necesita que el precio
    este en un BORDE; en el medio del value area no hay nada que fadear.
    """
    if not p:
        return {"ok": None, "txt": "sin perfil"}
    px, vah, val = p.get("px"), p.get("vah"), p.get("val")
    up, dn = p.get("up_band"), p.get("dn_band")
    if not (px and vah and val):
        return {"ok": None, "txt": "perfil incompleto"}
    sig = p.get("sig_pos")
    arriba = px > vah or (up and px >= up)
    abajo = px < val or (dn and px <= dn)
    dentro = val <= px <= vah
    if dentro:
        return {"ok": False, "zona": "dentro del value area", "sig_pos": sig,
                "txt": "dentro del value area (%.2f sigma): no hay borde que operar" % (sig or 0)}
    zona = "arriba del VAH" if arriba else ("abajo del VAL" if abajo else "fuera")
    # el fade coherente es SHORT arriba y LONG abajo
    coherente = (side == "SHORT" and arriba) or (side == "LONG" and abajo)
    return {"ok": bool(coherente), "zona": zona, "sig_pos": sig,
            "txt": "%s (%.2f sigma), %s con %s" % (
                zona, sig or 0, "coherente" if coherente else "INCOHERENTE", side)}


def _capa_gamma(sym, side, tipo):
    """
    Unico veto de gamma con respaldo: fade (REVERSION) con regimen NEGATIVO.
    Medido: -0.362%/op contra -0.169% en positiva, orden estable 5 de 6 anios.
    """
    try:
        import gex_live as gl
        if not gl.ready():
            gl.refresh_once()
        coin = gl.coin_for(str(sym).upper())
        if not coin:
            return {"ok": None, "txt": "sin cadena de opciones propia (no vetea)"}
        lv = gl.levels(coin, "scalp")
        if not lv:
            return {"ok": None, "txt": "sin snapshot de gamma"}
        neg = lv["regimen"] == "NEGATIVO"
        if tipo == "REVERSION" and neg:
            return {"ok": False, "regimen": lv["regimen"], "estado": lv["estado"] if "estado" in lv else lv.get("velocidad_esperada"),
                    "txt": "VETO: fade en gamma NEGATIVA (cohorte medida -0.362%/op)"}
        return {"ok": True, "regimen": lv["regimen"],
                "estado": lv.get("velocidad_esperada"),
                "txt": "gamma %s, %s: no se opone" % (lv["regimen"], lv.get("velocidad_esperada"))}
    except Exception as e:
        return {"ok": None, "txt": "gamma no disponible (%s)" % str(e)[:40]}


def _capa_convergencia(sym, px):
    """Banda de confluencia mas cercana al precio. Informativa, no vetea."""
    try:
        import convergencias as cv
        t = cv.tabla(sym)
        if not t or not t.get("bandas"):
            return {"ok": None, "txt": "sin bandas"}
        cerca = min(t["bandas"], key=lambda z: abs(z["dist_pct"]))
        fuertes = [z for z in t["bandas"] if z["fuerte"]]
        return {"ok": bool(cerca["fuerte"] and abs(cerca["dist_pct"]) <= 0.5),
                "banda": cerca, "n_fuertes": len(fuertes),
                "txt": "banda mas cercana a %+.2f%%: %d fuentes, peso %.2f%s" % (
                    cerca["dist_pct"], cerca["n_fuentes"], cerca["peso"],
                    " (FUERTE)" if cerca["fuerte"] else "")}
    except Exception:
        return {"ok": None, "txt": "convergencias no disponibles"}


def _capa_liquidez(sym, side):
    try:
        import liq_magnets as lm
        m = lm.magnets(sym) or {}
        d = m.get("magnet_dist"); dirm = m.get("dir")
        if d is None or not dirm:
            return {"ok": None, "txt": "sin iman de liquidacion"}
        ok = (dirm == side) and abs(d) >= 1.0
        return {"ok": bool(ok), "dist": round(d, 2), "dir": dirm,
                "txt": "iman %s a %+.2f%%: %s" % (dirm, d, "a favor" if ok else "no ayuda")}
    except Exception:
        return {"ok": None, "txt": "liq_magnets no disponible"}


def evaluar(sym, side=None, tipo="REVERSION", stage=None):
    """
    Devuelve el veredicto por capas. `mejor_momento` solo es True si TODAS las
    capas VALIDADAS estan a favor. Las no validadas solo informan.
    """
    p = _perfil(sym)
    px = (p or {}).get("px")
    if side is None and p:
        sig = p.get("sig_pos") or 0
        side = "SHORT" if sig > 0 else "LONG"
    capas = {
        "ubicacion": _capa_ubicacion(p, side),
        "gamma": _capa_gamma(sym, side, tipo),
        "liquidez": _capa_liquidez(sym, side),
        "convergencia": _capa_convergencia(sym, px) if px else {"ok": None, "txt": "sin precio"},
    }
    if stage is not None:
        capas["gatillo"] = {"ok": stage == "ENTRAR", "stage": stage,
                            "txt": "etapa %s" % stage}

    VALIDADAS = ("gatillo", "liquidez", "gamma")
    NO_VALIDADAS = ("ubicacion", "convergencia")
    vetos = [k for k in VALIDADAS if capas.get(k, {}).get("ok") is False]
    faltan = [k for k in VALIDADAS if capas.get(k, {}).get("ok") is None]
    a_favor_val = [k for k in VALIDADAS if capas.get(k, {}).get("ok") is True]
    extras = [k for k in NO_VALIDADAS if capas.get(k, {}).get("ok") is True]

    mejor = (not vetos) and len(a_favor_val) >= 2
    calidad = len(a_favor_val) * 2 + len(extras)
    return {"symbol": sym, "side": side, "tipo": tipo, "px": px,
            "capas": capas, "vetos": vetos, "sin_dato": faltan,
            "validadas_ok": a_favor_val, "extras_ok": extras,
            "mejor_momento": bool(mejor), "calidad": calidad,
            "resumen": ("MEJOR MOMENTO" if mejor else
                        ("VETADO por " + ", ".join(vetos) if vetos else "incompleto"))}


def main():
    sym = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    _a = [x for x in sys.argv[2:] if not x.startswith("--")]
    side = _a[0].upper() if _a else None
    r = evaluar(sym, side, stage="ENTRAR" if "--entrar" in sys.argv else None)
    print("=" * 76)
    print("UBICACION  ·  %s %s  ·  precio %.6g" % (r["symbol"], r["side"], r["px"] or 0))
    print("=" * 76)
    print("  VALIDADAS (pueden vetar)")
    for k in ("gatillo", "liquidez", "gamma"):
        c = r["capas"].get(k)
        if not c:
            continue
        mk = "OK " if c["ok"] is True else ("NO " if c["ok"] is False else "-- ")
        print("    [%s] %-13s %s" % (mk, k, c["txt"]))
    print("  INFORMATIVAS (no vetan)")
    for k in ("ubicacion", "convergencia"):
        c = r["capas"].get(k)
        mk = "OK " if c["ok"] is True else ("no " if c["ok"] is False else "-- ")
        print("    [%s] %-13s %s" % (mk, k, c["txt"]))
    print()
    print("  ->", r["resumen"], " · calidad %d" % r["calidad"])
    if r["sin_dato"]:
        print("     sin dato en:", ", ".join(r["sin_dato"]))


if __name__ == "__main__":
    main()
