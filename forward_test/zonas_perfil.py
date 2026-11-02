# -*- coding: utf-8 -*-
"""
zonas_perfil.py — zonas de COMPRA y VENTA por perfil de volumen y REGIMEN.

DE DONDE SALE
    No se inventa nada: se usa exactamente la logica que el motor ya aplica en
    `vwap_profile.vp_extremes` y en `_auto_verdict` del panel.

        ADX >= 25   ->  regimen TENDENCIA  ->  modo TENDENCIA
                        se opera A FAVOR del impulso, comprando el RETROCESO
        ADX <  20   ->  regimen CHOP       ->  modo FADE
                        se opera CONTRA el estiramiento, volviendo al VWAP/POC
        20 a 25     ->  MIXTO: no hay zona. Esperar.

    La diferencia con dibujar POC/VAL/VAH sueltos es que aca la zona dice DONDE
    comprar y DONDE vender segun el regimen, que es lo que cambia el significado
    del mismo nivel: el VAL es soporte para comprar en chop, y es el borde que se
    pierde en una tendencia bajista.

★ EL VETO QUE SI ESTA MEDIDO
    Un FADE con ADX >= 30 es fadear una tendencia fuerte, y es el peor agujero
    medido del sistema: WR 19 %, E -2.02 (calibracion /loop 2026-09-03). Cuando
    se da, la zona sale marcada VETADA y no se debe operar. Es lo unico de este
    modulo con evidencia propia; el resto es la lectura del regimen.

LAS BANDAS
    La sigma del VWAP no viene en la fila, pero se despeja: la fila trae `vwap`,
    el precio `px` y `sig_pos` (a cuantas sigmas esta el precio del VWAP), asi que

        sigma = (px - vwap) / sig_pos

    Con eso se arman las bandas de +-2 sigma, que son el borde real del
    estiramiento, y no un porcentaje inventado.

USO
    from zonas_perfil import zonas_operativas
    z = zonas_operativas(fila_del_panel)
"""

from __future__ import annotations

ADX_TENDENCIA = 25.0
ADX_CHOP = 20.0
FADE_ADX_VETO = 30.0        # ★ medido: FADE con ADX>=30 -> WR 19 %, E -2.02
SIGMA_BANDA = 2.0


def _f(v):
    try:
        x = float(v)
        return x if x == x else None      # descarta NaN
    except (TypeError, ValueError):
        return None


def sigma_de(row):
    """Sigma del VWAP, despejada de la posicion en sigmas que trae la fila."""
    px, vw, sp = _f(row.get("px")), _f(row.get("vwap")), _f(row.get("sig_pos"))
    if px is None or vw is None or sp is None or abs(sp) < 0.05:
        return None
    s = (px - vw) / sp
    return abs(s) if s else None


def zonas_operativas(row):
    """
    Devuelve {"regimen":..., "modo":..., "lado":..., "adx":...,
              "compra": {lo, hi, motivo} | None,
              "venta":  {lo, hi, motivo} | None,
              "veto": texto | None}
    """
    adx = _f(row.get("adx")) or 0.0
    px = _f(row.get("px"))
    vw = _f(row.get("vwap"))
    poc = _f(row.get("poc"))
    val = _f(row.get("val"))
    vah = _f(row.get("vah"))
    modo = str(row.get("modo_reco") or "").upper()
    lado = str(row.get("dir_reco") or "").upper()
    reg = str(row.get("regimen") or "").lower()
    sg = sigma_de(row)

    out = {"regimen": reg or None, "modo": modo or None, "lado": lado or None,
           "adx": round(adx, 1), "compra": None, "venta": None, "veto": None}

    if px is None:
        return out

    # ── veto medido ──────────────────────────────────────────────────────────
    if modo == "FADE" and adx >= FADE_ADX_VETO:
        out["veto"] = ("FADE contra tendencia FUERTE (ADX %.0f) - el peor agujero "
                       "medido - WR 19 pct, E -2.02" % adx)
        return out                      # no se dibuja zona: no hay que operarla

    # ── chop: se fadea el estiramiento, de vuelta al VWAP/POC ───────────────
    if modo == "FADE" or (reg == "chop" and adx < ADX_CHOP):
        b_inf = (vw - SIGMA_BANDA * sg) if (vw is not None and sg) else val
        b_sup = (vw + SIGMA_BANDA * sg) if (vw is not None and sg) else vah
        if val is not None and b_inf is not None:
            out["compra"] = {"lo": min(val, b_inf), "hi": max(val, b_inf),
                             "motivo": "chop - piso del area y banda -2 sigma, "
                                       "vuelta al VWAP/POC"}
        if vah is not None and b_sup is not None:
            out["venta"] = {"lo": min(vah, b_sup), "hi": max(vah, b_sup),
                            "motivo": "chop - techo del area y banda +2 sigma, "
                                      "vuelta al VWAP/POC"}
        return out

    # ── tendencia: se compra el retroceso a favor, no el extremo ────────────
    if modo == "TENDENCIA" or (reg == "tendencia" and adx >= ADX_TENDENCIA):
        if vw is None or poc is None:
            return out
        lo, hi = min(vw, poc), max(vw, poc)
        if lado == "LONG":
            out["compra"] = {"lo": lo, "hi": hi,
                             "motivo": "tendencia alcista - retroceso al VWAP/POC, "
                                       "a favor del impulso"}
        elif lado == "SHORT":
            out["venta"] = {"lo": lo, "hi": hi,
                            "motivo": "tendencia bajista - retroceso al VWAP/POC, "
                                      "a favor del impulso"}
        return out

    # ── mixto ───────────────────────────────────────────────────────────────
    out["veto"] = "regimen MIXTO (ADX %.0f entre %.0f y %.0f) - sin zona, esperar" % (
        adx, ADX_CHOP, ADX_TENDENCIA)
    return out


def dentro(zona, precio, tol_pct=0.0, ref=None):
    """True si `precio` cae dentro de la zona (con tolerancia opcional en %)."""
    if not zona or precio is None:
        return False
    lo, hi = zona["lo"], zona["hi"]
    if tol_pct and ref:
        m = tol_pct / 100.0 * ref
        lo, hi = lo - m, hi + m
    return lo <= precio <= hi


def marca_para(zops, centro, ref=None, tol_pct=0.35):
    """Devuelve 'ZC' si el nivel cae en la zona de compra, 'ZV' en la de venta."""
    if not zops or centro is None:
        return ""
    if dentro(zops.get("compra"), centro, tol_pct, ref):
        return "ZC"
    if dentro(zops.get("venta"), centro, tol_pct, ref):
        return "ZV"
    return ""
