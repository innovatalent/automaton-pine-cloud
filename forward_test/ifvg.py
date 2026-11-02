# -*- coding: utf-8 -*-
"""GATILLO iFVG (usuario 2026-09-03: "cuando suena la alerta el activo se mueve EN CONTRA, luego rebota en un iFVG y AHÍ
sigue a la alerta"). Lógica ALINEADA con el Pine del usuario (PRO_Confluence_definitive_MERGED, sección ④ FVG/iFVG/BPR):
  FVG alcista = high[2] < low (gap up) · FVG bajista = low[2] > high (gap down), tamaño >= fvgMinPct.
  iFVG: un FVG se INVIERTE cuando una vela lo rompe por DESPLAZAMIENTO (cuerpo > 2× el cuerpo promedio) y CIERRA del otro
  lado → pasa a ser soporte/resistencia opuesta. Bull FVG roto abajo → iFVG BAJISTA (resistencia). Bear FVG roto arriba →
  iFVG ALCISTA (soporte). ENTRADA = el precio RE-TESTEA la zona iFVG (con tolerancia) y rechaza a favor de la alerta.

Validado con label_outcomes: ganadores mae 0.59% vs perdedores 2.92% (5×); 56% de ganadores van primero en contra.
Causal (sólo velas CERRADAS). Devuelve {status: WAIT|ENTER|EXPIRE, entry, sl, reason}."""
import json, time
import urllib.request as _ur

MAXW_BARS = 12          # ventana 12×5m = 1h; sin gatillo → EXPIRE (no perseguir)
MAX_ADVERSE = 2.0       # >2% en contra sin rechazar = perfil perdedor (mae 2.9%) → EXPIRE
SWEEP_MIN = 0.15        # barrido mínimo en contra para considerar "se movió en contra"
FVG_MIN_PCT = 0.05      # tamaño mínimo de FVG (= fvgMinPct del Pine), filtra ruido
FVG_TOL = 0.20          # tolerancia de entrada al re-testear la zona iFVG (% del precio)
SL_BUFFER = 0.15        # SL más allá del extremo de la zona iFVG / swing


def _k5(sym, limit=60):
    try:
        d = json.load(_ur.urlopen(_ur.Request(
            "https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=5&limit=%d" % (sym, limit),
            headers={"User-Agent": "x"}), timeout=10))
        K = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])] for x in reversed(d["result"]["list"])]  # ts,o,h,l,c
        return K[:-1] if len(K) > 1 else K   # dropear la vela en formación (causal)
    except Exception:
        return None


def _avg_body_pct(K):
    b = [abs((k[4] - k[1]) / k[1] * 100.0) for k in K if k[1]]
    return (sum(b) / len(b)) if b else 0.3


def pending_trigger(sym, side, alert_px, alert_ts):
    """SHORT: sweep arriba → bull FVG → INVERSIÓN por desplazamiento (cierra debajo) = iFVG resistencia → RE-TEST + rechazo
    = ENTER. Mirror LONG. Causal, fail-soft (WAIT ante error)."""
    try:
        K = _k5(sym)
        if not K or len(K) < 5:
            return {"status": "WAIT", "reason": "sin datos"}
        after = [b for b in K if b[0] / 1000.0 > (alert_ts - 60)]
        if len(after) < 3:
            return {"status": "WAIT", "reason": "esperando velas"}
        bars = len(after)
        px = after[-1][4]
        thr = 2.0 * _avg_body_pct(K)                    # umbral de DESPLAZAMIENTO (= fvgThr del Pine)
        # excursión en contra
        if side == "SHORT":
            adverse = (max(b[2] for b in after) - alert_px) / alert_px * 100.0
        else:
            adverse = (alert_px - min(b[3] for b in after)) / alert_px * 100.0
        if adverse >= MAX_ADVERSE:
            return {"status": "EXPIRE", "reason": "se fue %.1f%% en contra sin rechazar (perfil perdedor)" % adverse}
        if bars >= MAXW_BARS:
            return {"status": "EXPIRE", "reason": "venció la ventana (1h) sin gatillo iFVG"}
        if adverse < SWEEP_MIN:
            return {"status": "WAIT", "reason": "aún sin barrido en contra"}

        def body_pct(k):
            return (k[4] - k[1]) / k[1] * 100.0 if k[1] else 0.0

        tol = px * FVG_TOL / 100.0
        if side == "SHORT":
            # 1) bull FVG (gap up): low[i] > high[i-2], tamaño >= min
            # 2) inversión: una vela j>i con CIERRE < bottom del FVG y DESPLAZAMIENTO (body < -thr) → iFVG bajista [b, t]
            # 3) entrada: una vela k>j RE-TESTEA la zona (high dentro de [b-tol, t+tol]) y cierra debajo (rechazo)
            for i in range(2, len(after)):
                lo_i = after[i][3]; hi_i2 = after[i - 2][2]
                if not (lo_i > hi_i2 and (lo_i - hi_i2) / px * 100.0 >= FVG_MIN_PCT):
                    continue
                b_bot, b_top = hi_i2, lo_i                 # zona del bull FVG
                for j in range(i + 1, len(after)):
                    if after[j][4] < b_bot and body_pct(after[j]) <= -thr:   # inversión por desplazamiento
                        zt, zb = b_top, b_bot              # iFVG bajista = resistencia en [zb, zt]
                        swing = max(after[k][2] for k in range(i - 2, j + 1))
                        for k in range(j + 1, len(after)):  # re-test + rechazo
                            if after[k][2] >= zb - tol and after[k][2] <= zt + tol and after[k][4] < zt:
                                return {"status": "ENTER", "entry": after[k][4], "sl": swing * (1 + SL_BUFFER / 100.0),
                                        "reason": "iFVG bajista: barrió +%.1f%%, desplazó y re-testeó la zona" % adverse}
                        break
            return {"status": "WAIT", "reason": "barrió pero sin inversión+retest aún"}
        else:
            for i in range(2, len(after)):
                hi_i = after[i][2]; lo_i2 = after[i - 2][3]
                if not (lo_i2 > hi_i and (lo_i2 - hi_i) / px * 100.0 >= FVG_MIN_PCT):
                    continue
                b_top, b_bot = lo_i2, hi_i                 # zona del bear FVG
                for j in range(i + 1, len(after)):
                    if after[j][4] > b_top and body_pct(after[j]) >= thr:    # inversión por desplazamiento alcista
                        zt, zb = b_top, b_bot              # iFVG alcista = soporte en [zb, zt]
                        swing = min(after[k][3] for k in range(i - 2, j + 1))
                        for k in range(j + 1, len(after)):
                            if after[k][3] <= zt + tol and after[k][3] >= zb - tol and after[k][4] > zb:
                                return {"status": "ENTER", "entry": after[k][4], "sl": swing * (1 - SL_BUFFER / 100.0),
                                        "reason": "iFVG alcista: barrió +%.1f%%, desplazó y re-testeó la zona" % adverse}
                        break
            return {"status": "WAIT", "reason": "barrió pero sin inversión+retest aún"}
    except Exception as e:
        return {"status": "WAIT", "reason": "err %s" % str(e)[:40]}


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "TAOUSDT"
    side = sys.argv[2] if len(sys.argv) > 2 else "SHORT"
    K = _k5(sym)
    if K:
        px = K[-1][4]
        print("test %s %s alert_px=%.4f:" % (sym, side, px), pending_trigger(sym, side, px, time.time() - 3600))
