# -*- coding: utf-8 -*-
"""SWEEP-FADE de sesión (VALIDADO 2026-08-28, bt_sweep_fade.py): NY barre el extremo del rango de Londres, el precio
RECLAIMA el nivel, y se FADEA hacia el midpoint/extremo opuesto. Medido n=694, 10 activos, 90d: llega a la MITAD 80%,
al OPUESTO 59%, stopea sólo 14% — robusto cross-símbolo Y OOS (IN 60/78 ≈ OOS 58/82). Detector compartido alerta+brazo."""
from datetime import datetime, timezone

LONDON = (8, 13)   # sesión Londres UTC [08,13)
SWEEP_FROM = 13    # el barrido puede ocurrir ya en la transición Londres→NY (13 UTC). Barrido-en-transición + reclaim
                   # en NY rinde MEJOR (mitad 78% vs 70%, bt_sweep_transition.py) que el barrido dentro de NY
POST = 14          # pero sólo señalamos con la vela actual en NY propio (14h+ = 11h AR): la 1ª hora (13h) rinde peor
BUF = 0.001        # buffer del stop (0.1% más allá del extremo del barrido)


def _hr(ts):
    t = ts / 1000 if ts > 2e10 else ts
    return datetime.fromtimestamp(t, timezone.utc).hour


def _day(ts):
    t = ts / 1000 if ts > 2e10 else ts
    return int(t // 86400)


def sweep_signal(sym, K):
    """Devuelve el fade del barrido de sesión o None. K = klines [t,o,h,l,c,v] (15m ideal), t en ms/seg.
    LONG = barrió el MÍN de Londres y reclama → fade arriba (mid/opuesto). SHORT = espejo del máx."""
    if not K or len(K) < 20:
        return None
    today = _day(K[-1][0])
    ld = [b for b in K if _day(b[0]) == today and LONDON[0] <= _hr(b[0]) < LONDON[1]]
    if len(ld) < 3:
        return None                                  # Londres incompleto / sin data hoy
    L_hi = max(b[2] for b in ld); L_lo = min(b[3] for b in ld)
    if L_hi <= L_lo:
        return None
    mid = (L_hi + L_lo) / 2.0
    # barras CERRADAS desde la transición (13h) para detectar el barrido...
    swin = [b for b in K[:-1] if _day(b[0]) == today and _hr(b[0]) >= SWEEP_FROM]
    if not swin or _hr(swin[-1][0]) < POST:           # ...pero sólo señalamos ya en NY propio (14h+)
        return None
    last_c = swin[-1][4]                              # cierre de la última vela cerrada
    lows_sw = [b[3] for b in swin if b[3] < L_lo]
    highs_sw = [b[2] for b in swin if b[2] > L_hi]
    both_swept = bool(lows_sw and highs_sw)           # doble grab = setup A (mitad 82%/opuesto 73%/stop 17% OOS)
    sig = None
    if lows_sw and last_c > L_lo:                     # barrió el mín y reclamó → LONG
        ext = min(lows_sw)
        sig = {"side": "LONG", "level": L_lo, "sweep_ext": ext, "stop": ext * (1 - BUF),
               "mid": mid, "opp": L_hi}
    elif highs_sw and last_c < L_hi:                  # barrió el máx y reclamó → SHORT
        ext = max(highs_sw)
        sig = {"side": "SHORT", "level": L_hi, "sweep_ext": ext, "stop": ext * (1 + BUF),
               "mid": mid, "opp": L_lo}
    if not sig:
        return None
    entry = sig["level"]
    sig.update({"sym": sym, "entry": entry, "day": today, "both_swept": both_swept,
                "grade": "A" if both_swept else "B",
                "dist_mid": round((sig["mid"] - entry) / entry * 100, 2),
                "dist_opp": round((sig["opp"] - entry) / entry * 100, 2),
                "risk": round(abs(entry - sig["stop"]) / entry * 100, 2)})
    return sig


def sweep_fade_now(sym, tf="15m", nbars=300):
    """Conveniencia: baja klines y devuelve sweep_signal (grado A/B) o None. Lo usan panel/alertas/brazos con 1 línea."""
    try:
        import imminent_move_alert as IMM
        K = IMM._klines(sym, tf, nbars)
    except Exception:
        return None
    return sweep_signal(sym, K) if K else None


if __name__ == "__main__":
    import sys, imminent_move_alert as IMM
    for s in (sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TAOUSDT", "INJUSDT"]):
        K = IMM._klines(s, "15m", 300)
        sg = sweep_signal(s, K) if K else None
        if sg:
            print("%-9s [%s] %-5s reclaim@%.5g stop%.5g -> mid %+.2f%% / opuesto %+.2f%% (riesgo %.2f%%)%s" % (
                s, sg["grade"], sg["side"], sg["entry"], sg["stop"], sg["dist_mid"], sg["dist_opp"], sg["risk"],
                "  ★DOBLE-GRAB" if sg["both_swept"] else ""))
        else:
            print("%-9s sin sweep-fade ahora" % s)
