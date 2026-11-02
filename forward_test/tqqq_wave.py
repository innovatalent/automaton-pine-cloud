# -*- coding: utf-8 -*-
"""DETECTOR DE ONDA en ETFs 3x (TQQQ / SOXL) — usuario 2026-09-04: "TQQQ se movió 1 % abajo, 1 % arriba, 1 % abajo y otra vez
arriba y ninguna onda la pudiste operar; el panel debería detectarlo". Medido en bt_tqqq_pine_rules.py (port del screener
TQQQ_Swing_Perfil_Screener_v2): la ÚNICA regla con esperanza positiva y consistente por mes es la CAÍDA BRUSCA (fade LONG):
  caída ≥ 1.5 % en 6 velas de 5m + precio bajo la EMA200 + z75 girando arriba + vela alcista + SOTT k ≤ 45
  → LONG con stop 3 % + trailing +1.0/0.3 (salida mom): SOXL n=276 +0.20 %/op WR 75 % (+1 % en el 85 %), TQQQ n=16 +0.20 % WR 81 %.
  La SUBIDA BRUSCA (fade SHORT) es negativa → sólo informativa. CRT 15m/30m (turtle soup) del Pine: negativo → sólo niveles.
`signal(sym)` evalúa la ÚLTIMA vela 5m CERRADA (Binance = fuente del panel), cache 60 s. Devuelve dict con las banderas, el
estado (z, k, retN, dist EMA200) y los niveles CRT 15m/30m más recientes."""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

WAVE_SYMS = ("TQQQUSDT", "SOXLUSDT")
SHARP_THR = 1.5          # % en 6 velas (validado 1.5 > 2.0 > 2.5 en SOXL/TQQQ)
_C = {}


def _rules():
    import bt_tqqq_pine_rules as R
    return R


def signal(sym, ttl=60):
    now = time.time()
    h = _C.get(sym)
    if h and now - h[0] < ttl:
        return h[1]
    out = None
    try:
        import imminent_move_alert as _IMM
        R = _rules()
        K = _IMM._klines(sym, "5m", 1500)
        if K and len(K) > 300:
            K = [[float(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]) if len(x) > 5 else 0.0] for x in K][:-1]
            o = [k[1] for k in K]; hh = [k[2] for k in K]; ll = [k[3] for k in K]; c = [k[4] for k in K]
            n = len(K); zs = R.sma_std(c, 75)
            z = [((c[i] - zs[i][0]) / zs[i][1]) if (zs[i] and zs[i][1] > 0) else 0.0 for i in range(n)]
            k = R.sott_k(c, hh, ll); e200 = R.ema(c, 200)
            def flags(i):
                if k[i] is None:
                    return False, False, None, None, None
                retN = (c[i] - c[i - 6]) / c[i - 6] * 100.0; distE = (c[i] - e200[i]) / e200[i] * 100.0
                sd = retN <= -SHARP_THR and distE < 0 and z[i] > z[i - 1] and c[i] > o[i] and k[i] <= 45
                sr = retN >= SHARP_THR and distE > 0 and z[i] < z[i - 1] and c[i] < o[i] and k[i] >= 55
                return sd, sr, retN, distE, k[i]
            sd, sr, retN, distE, kk = flags(n - 1); sd_prev, sr_prev, _, _, _ = flags(n - 2)
            crt = {}
            for m in (3, 6):
                sig = R.crt_signals(K, m)
                if sig:
                    j, side, entry, target, sweep = sig[-1]
                    crt["%dm" % (m * 5)] = {"side": side, "entry": entry, "target": target, "sweep": sweep, "bars_ago": n - j}
            # retorno de la SESIÓN NY (desde el open de las 09:30 NY) → base de la fuente REBOTE (día ≤ −2%% → comprar al cierre)
            day_ret = None; ny_hm = None
            try:
                from zoneinfo import ZoneInfo
                from datetime import datetime as _dt
                _ny = ZoneInfo("America/New_York"); _last = _dt.fromtimestamp(K[-1][0] / 1000, _ny); ny_hm = _last.strftime("%H:%M")
                _open_px = None
                for kk_ in K:
                    _t = _dt.fromtimestamp(kk_[0] / 1000, _ny)
                    if _t.date() == _last.date() and _t.hour == 9 and _t.minute == 30:
                        _open_px = kk_[1]; break
                if _open_px:
                    day_ret = round((c[-1] / _open_px - 1) * 100.0, 2)
            except Exception:
                pass
            out = {"sym": sym, "ts_bar": int(K[-1][0] / 1000), "px": c[-1], "z": round(z[-1], 2), "k": (round(kk) if kk is not None else None),
                   "day_ret": day_ret, "ny_hm": ny_hm,
                   "retN": (round(retN, 2) if retN is not None else None), "distE200": (round(distE, 2) if distE is not None else None),
                   "sharp_drop": bool(sd), "sharp_drop_new": bool(sd and not sd_prev),
                   "sharp_rise": bool(sr), "sharp_rise_new": bool(sr and not sr_prev), "crt": crt, "ts": now}
    except Exception as e:
        out = {"sym": sym, "err": str(e)[:80], "ts": now}
    _C[sym] = (now, out)
    return out


if __name__ == "__main__":
    import json
    for s in WAVE_SYMS:
        print(json.dumps(signal(s), ensure_ascii=False))
