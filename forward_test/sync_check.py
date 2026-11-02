# -*- coding: utf-8 -*-
"""VEREDICTO DE SINCRONIA (pedido usuario 2026-08-26 "sincronizar mis entradas con lo que luego hace el mercado").
NO predice direccion (medido: no existe). Mide FOLLOW-THROUGH: si las condiciones favorecen que la entrada se SOSTENGA.
3 señales VALIDADAS:  IMAN de liquidez a favor (edge fuerte, via liq_gate) + REGIMEN de volatilidad vivo (ATR5m% > su
baseline; cont muere en chop) + MOMENTUM MTF alineado (>=2 de 5m/15m/1h con 1h obligatorio; el 1h sube prob +1% 55->66%).
Complementa al Pine 'entry_sync_gauge' (que hace regimen+momentum+cvd en el chart pero NO el iman). CLI: python sync_check.py TAOUSDT"""
import liq_gate as LG


def _regime(K, n=14, base=80):
    """(alive, atrp_actual, baseline). Tape VIVO si ATR% actual > media de ATR% (regimen no-chop). None sin data."""
    if not K or len(K) < n + base + 3:
        # fallback: ATR% actual vs umbral fijo 0.5 (medido en 5m para cont)
        if not K or len(K) < n + 3:
            return (None, None, None)
        Kc = K[:-1]
        trs = [max(Kc[j][2] - Kc[j][3], abs(Kc[j][2] - Kc[j - 1][4]), abs(Kc[j][3] - Kc[j - 1][4])) for j in range(len(Kc) - n, len(Kc))]
        c = Kc[-1][4]
        ap = (sum(trs) / n) / c * 100 if c else None
        return (ap is not None and ap >= 0.5, ap, 0.5)
    Kc = K[:-1]
    tr = [max(Kc[j][2] - Kc[j][3], abs(Kc[j][2] - Kc[j - 1][4]), abs(Kc[j][3] - Kc[j - 1][4])) for j in range(1, len(Kc))]
    atrp = []
    for k in range(n, len(tr)):
        atr = sum(tr[k - n:k]) / n
        c = Kc[k + 1][4] if k + 1 < len(Kc) else Kc[-1][4]
        atrp.append(atr / c * 100 if c else 0.0)
    if not atrp:
        return (None, None, None)
    cur = atrp[-1]
    b = sum(atrp[-base:]) / len(atrp[-base:])
    return (cur > b, cur, b)


def _momentum(sym):
    """(side, aligned_tfs). >=2 de {5m,15m,1h} con 1h obligatorio (misma logica que la alerta MOMENTUM del bot)."""
    import sott_combo_alert as A
    d5, _ = A._mom_dir(A.IMM._klines(sym, "5m", 200))
    d15, _ = A._mom_dir(A.IMM._klines(sym, "15m", 200))
    d1h, _ = A._mom_dir(A.IMM._klines(sym, "1h", 300))
    dirs = {"5m": d5, "15m": d15, "1h": d1h}
    longs = [t for t, d in dirs.items() if d == "LONG"]
    shorts = [t for t, d in dirs.items() if d == "SHORT"]
    if d1h == "LONG" and len(longs) >= 2:
        return ("LONG", longs)
    if d1h == "SHORT" and len(shorts) >= 2:
        return ("SHORT", shorts)
    return (None, [])


def magnet_regime(sym, side):
    """Solo IMAN + REGIMEN (barato, para enriquecer la alerta de momentum que YA sabe el side)."""
    import sott_combo_alert as A
    mag = LG.magnet_dir(sym)
    mag_align = None if mag not in ("LONG", "SHORT") else (mag == side)
    alive, atrp, base = _regime(A.IMM._klines(sym, "5m", 200))
    cvd = LG._cvd_dir(A.IMM._klines(sym, "5m", 60) or [])
    cvd_align = None if cvd not in ("LONG", "SHORT") else (cvd == side)
    return {"mag": mag, "mag_align": mag_align, "regime_alive": alive, "atrp": atrp, "cvd_align": cvd_align}


def level(mr):
    """FUERTE / OK / PARCIAL / DEBIL a partir de iman+regimen (+cvd)."""
    mag_against = (mr["mag_align"] is False)
    reg = bool(mr["regime_alive"])
    if mag_against:
        return "DEBIL"                       # iman EN CONTRA = no sincroniza (el edge fuerte manda)
    if mr["mag_align"] and reg and (mr["cvd_align"] is not False):
        return "FUERTE"                      # iman a favor + tape vivo + flujo no-en-contra
    if reg:
        return "OK"                          # tape vivo (iman neutro/sin data)
    return "PARCIAL"                         # sin regimen vivo


def _txt_mag(a):
    return "a favor" if a is True else ("EN CONTRA" if a is False else "neutro")


def verdict(sym):
    """Veredicto COMPLETO on-demand (deriva el side por momentum). Para el dashboard / CLI."""
    side, aligned = _momentum(sym)
    if side is None:
        return {"sym": sym, "side": None, "level": "SIN SETUP", "aligned": [], "detail": "momentum no alineado (>=2/3 con 1h)"}
    mr = magnet_regime(sym, side)
    lv = level(mr)
    detail = "iman %s / regimen %s (ATR%% %.2f) / cvd %s / momentum %d/3" % (
        _txt_mag(mr["mag_align"]),
        ("vivo" if mr["regime_alive"] else "chop") if mr["regime_alive"] is not None else "s/d",
        mr["atrp"] or 0.0,
        _txt_mag(mr["cvd_align"]),
        len(aligned))
    return {"sym": sym, "side": side, "level": lv, "aligned": aligned, "detail": detail, **mr}


def line(sym, side):
    """Linea corta para la alerta de momentum (TG/voz). Usa side ya conocido."""
    mr = magnet_regime(sym, side)
    lv = level(mr)
    return lv, "iman %s | regimen %s | flujo %s" % (
        _txt_mag(mr["mag_align"]),
        ("vivo" if mr["regime_alive"] else "chop") if mr["regime_alive"] is not None else "s/d",
        _txt_mag(mr["cvd_align"]))


if __name__ == "__main__":
    import sys
    syms = sys.argv[1:] or ["TAOUSDT"]
    for s in syms:
        v = verdict(s)
        print("%-10s SYNC=%-9s side=%-5s  %s" % (s, v["level"], v["side"] or "-", v.get("detail", "")))
