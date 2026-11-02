# -*- coding: utf-8 -*-
"""VIGILANTE de SETUPS por CONFLUENCIA (pedido usuario 2026-09-03, caso BNB): rastrea shorts/longs desde que se FORMAN por
confluencia z/SOTT en varias temporalidades (aunque todavía NO estén en extremo VWAP, ej BNB con z sobrecompra 3TF), los
ETAPA (①esperar → ②gatillo → ③ENTRAR) aplicando el filtro de tendencia 4h (no pelear la tendencia = lección UNI), y
ALERTA por voz+Telegram al CONFIRMARSE (flip de flujo 5m + que deje de pelear la tendencia). scan()=lista para el panel ·
scan_and_alert()=+dispara alertas con latch/cooldown. Reusa el canal de alertas de sott_combo_alert (VOICE json + Telegram)."""
import os, sys, json, time
import urllib.request as _ur
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from direction_engine import sott_zone as SZ

__ALERT_HUB_PATCHED__ = True   # parche alert_hub 2026-09-04 (no borrar: idempotencia)
def _HUB_OK(prefix, side, sym):
    """Puerta única de alertas: sólo hablan/mandan las celdas (prefijo, lado) validadas por alert_hub. Fail-open."""
    try:
        import alert_hub as _AH
        import time as _t
        return _AH.gate("%s-%s-%s-%d" % (prefix, sym, side, int(_t.time())), sym, side)
    except Exception:
        return True

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "confluence_watch_state.json")
_KC = {}
ZLB = 75
COOLDOWN = 45 * 60

# ANCLADOS (usuario 2026-09-08 "incluí UNI en el panel de vigilancia"): simbolos que se
# muestran SIEMPRE en la franja aunque no lleguen al umbral de confluencia, con stage
# "vigilar" y pinned=True. No alertan ni entran a la tabla de extremos: solo quedan a la
# vista. Configurable con la variable de entorno WATCH_PIN (separada por comas).
# 2026-09-08: el anclaje se VACIA por defecto. Fijar un simbolo contradice el objetivo
# del panel, que es mostrar los MEJORES para operar AHORA, no los que se fijaron antes.
# Se puede reactivar con la variable de entorno WATCH_PIN si hace falta seguir uno.
PIN = set(x.strip().upper() for x in os.getenv("WATCH_PIN", "").split(",") if x.strip())
TFS = [("15m", "15"), ("1h", "60"), ("4h", "240"), ("1d", "D")]


def _kl(sym, interval, limit=300):
    key = (sym, interval); h = _KC.get(key)
    if h and time.time() - h[0] < 180:
        return h[1]
    c = None
    try:
        d = json.load(_ur.urlopen(_ur.Request(
            "https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=%s&limit=%d" % (sym, interval, limit),
            headers={"User-Agent": "x"}), timeout=12))
        L = list(reversed(d.get("result", {}).get("list", []) or []))
        c = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in L]
    except Exception:
        c = None
    _KC[key] = (time.time(), c)
    return c


def _z(c):
    if not c or len(c) < ZLB + 1:
        return None
    w = [x[4] for x in c[-ZLB - 1:-1]]; m = sum(w) / ZLB
    sd = (sum((x - m) ** 2 for x in w) / ZLB) ** 0.5
    return (c[-1][4] - m) / sd if sd > 0 else 0.0


def _src_last(K):
    """SOTT src_new (200/20) del último bar, computado de las MISMAS velas ya descargadas (evita el fetch doble de src_new)."""
    if not K or len(K) < SZ.NEW_PERIOD_K + 40:
        return None
    h = [b[2] for b in K]; l = [b[3] for b in K]; c = [b[4] for b in K]
    st = SZ._stoch(c, h, l, SZ.NEW_PERIOD_K)
    k = SZ._var_func(st, SZ.NEW_SMOOTH_K)
    return k[-1] + 1000.0


def _ema_trend(c, n=50):
    cl = [x[4] for x in c] if c else []
    if len(cl) < n + 8:
        return "flat"
    k = 2.0 / (n + 1); e = cl[0]; es = []
    for x in cl:
        e = x * k + e * (1 - k); es.append(e)
    slope = (es[-1] - es[-7]) / es[-7] * 100; pxv = (cl[-1] - es[-1]) / es[-1] * 100
    return "up" if (slope > 0.05 and pxv > -1) else ("down" if (slope < -0.05 and pxv < 1) else "flat")


def _flip5(sym, side):
    """¿el flujo de 5m ya flipeó del lado del setup? = delta reciente (3v) EN CONTRA del movimiento + surge."""
    c = _kl(sym, "5", 100)
    if not c or len(c) < 25:
        return (False, 0.0)
    def dl(b):
        rng = b[2] - b[3]
        return b[5] * ((b[4] - b[3]) - (b[2] - b[4])) / rng if rng > 0 else 0.0
    d = [dl(b) for b in c]
    recent = sum(d[-3:]); avg = (sum(abs(x) for x in d[-20:]) / 20.0) or 1e-9
    surge = abs(recent) / avg
    on = (side == "SHORT" and recent < 0) or (side == "LONG" and recent > 0)
    return (on, round(surge, 2))


def scan(syms):
    """Lista de setups por confluencia z/SOTT multi-TF, con etapa (esperar/gatillo/ENTRAR) y filtro de tendencia 4h."""
    out = []
    for s in syms:
        zt = {}; stt = {}
        for lbl, code in TFS:
            c = _kl(s, code, 300)          # UNA sola descarga por TF → z y SOTT de las mismas velas
            zt[lbl] = _z(c)
            stt[lbl] = _src_last(c)
        zov = sum(1 for v in zt.values() if v is not None and v >= 2.5)
        zun = sum(1 for v in zt.values() if v is not None and v <= -2.5)
        sov = sum(1 for v in stt.values() if v is not None and v > SZ.UPPER_BAND)
        sun = sum(1 for v in stt.values() if v is not None and v < SZ.LOWER_BAND)
        side = None
        if zov >= 3 or (zov >= 2 and sov >= 2):
            side = "SHORT"
        elif zun >= 3 or (zun >= 2 and sun >= 2):
            side = "LONG"
        if not side:
            if s not in PIN:
                continue
            # ANCLADO: sin confluencia suficiente, pero se muestra igual para vigilarlo.
            # side = hacia donde se esta inclinando (informativo), stage = "vigilar".
            _up = zov + sov
            _dn = zun + sun
            lean = "SHORT" if _up > _dn else ("LONG" if _dn > _up else None)
            c4p = _kl(s, "240", 300)
            out.append({"sym": s, "side": lean, "stage": "vigilar", "pinned": True,
                        "z_agree": max(zov, zun), "sott_agree": max(sov, sun),
                        "trend4h": _ema_trend(c4p), "contra": False, "flip5m": False,
                        "surge": 0.0, "liq_ok": False, "liq_dist": None,
                        "primed24h": 0, "primed_max": 0, "best": False,
                        "px": (c4p[-1][4] if c4p else None)})
            continue
        c4 = _kl(s, "240", 300); trend = _ema_trend(c4)
        contra = (side == "SHORT" and trend == "up") or (side == "LONG" and trend == "down")
        flip, surge = _flip5(s, side)
        if contra:
            stage = "esperar"                      # ① peleando la tendencia (ej BNB) → ESPERAR a que el 4h se dé vuelta (lección UNI)
        elif flip and surge >= 1.3:
            stage = "ENTRAR"                       # ③ confirmado: flip 5m fuerte + NO pelea la tendencia = el momento óptimo
        elif flip or zov >= 3 or zun >= 3:
            stage = "gatillo"                      # ② dejó de pelear la tendencia y el flujo empieza a darse vuelta
        else:
            stage = "esperar"                      # ① estirado pero sin gatillo
        # PERSISTENCIA 24h (usuario: vigilancia ESPECIAL a los que tuvieron 3+ confluencias en 24h) — de confluence_history (barato).
        pr = {"count": 0, "max": 0}
        try:
            import confluence_history as _ch
            pr = _ch.primed(s, time.time())
        except Exception:
            pass
        # LIQUIDEZ a favor (gatillo + liquidez = mejor momento). SOLO donde importa (gatillo/ENTRAR o persistente) → magnets() pesa.
        liq_ok = False; liq_dist = None
        if stage in ("gatillo", "ENTRAR") or pr.get("count", 0) >= 3:
            try:
                import liq_magnets as _lm
                mg = _lm.magnets(s) or {}
                _md = mg.get("dir"); _ld = mg.get("magnet_dist")
                liq_dist = (round(_ld, 1) if _ld is not None else None)
                liq_ok = (_md == side) and (_ld is not None and abs(_ld) >= 1.0)
            except Exception:
                pass
        # __UBICACION_2026_09_08__ — "MEJOR MOMENTO" ya no es solo gatillo+liquidez.
        # Antes bastaba stage==ENTRAR con el iman a favor, lo que marcaba como optimo a
        # activos parados en el MEDIO del value area o con la gamma en contra. Ahora se
        # exige que ninguna capa VALIDADA vete. Se evalua solo cuando el gatillo ya esta
        # dado (es raro), asi que no pesa en el barrido.
        best = bool(stage == "ENTRAR" and liq_ok)
        ubic = None
        if stage == "ENTRAR":
            try:
                import ubicacion as _ub
                ubic = _ub.evaluar(s, side, tipo="REVERSION", stage=stage)
                best = bool(ubic.get("mejor_momento"))
            except Exception:
                pass
        out.append({"sym": s, "side": side, "stage": stage, "z_agree": max(zov, zun), "sott_agree": max(sov, sun),
                    "trend4h": trend, "contra": bool(contra), "flip5m": bool(flip), "surge": surge,
                    "liq_ok": bool(liq_ok), "liq_dist": liq_dist, "primed24h": pr.get("count", 0), "primed_max": pr.get("max", 0),
                    "best": best, "px": (c4[-1][4] if c4 else None),
                    "ubic": ({"vetos": ubic["vetos"], "validadas_ok": ubic["validadas_ok"],
                              "extras_ok": ubic["extras_ok"], "calidad": ubic["calidad"],
                              "resumen": ubic["resumen"]} if ubic else None)})
    # orden: MEJOR MOMENTO (ENTRAR+liq) → ENTRAR → gatillo → esperar; dentro por persistencia 24h y confluencia
    _rank = {"ENTRAR": 0, "gatillo": 1, "esperar": 2, "vigilar": 3}
    out.sort(key=lambda u: (0 if u["best"] else 1, _rank.get(u["stage"], 9), -(u["primed24h"]), -(u["z_agree"] + u["sott_agree"])))
    return out


def _emit(u):
    if not _HUB_OK("cw", u.get("side"), u.get("sym")): return
    try:
        import sott_combo_alert as _sca
    except Exception:
        return
    sym = u["sym"]; side = u["side"]; word = "long" if side == "LONG" else "short"
    try:
        sp = _sca._spoken(sym)
    except Exception:
        sp = sym.replace("USDT", "")
    try:
        import imminent_move_alert as IMM
        alerts = IMM._load(_sca.VOICE, [])
        _liqv = "con liquidación a favor" if u.get("liq_ok") else "sin imán claro"
        alerts.append({"id": "cw-%s-%s-%d" % (sym, side, int(time.time())), "asset": sym, "side": side,
                       "text": "%s en %s, confluencia z y sott en %d temporalidades, confirmada con flip de flujo, a favor de la tendencia, %s" % (word, sp, u["z_agree"], _liqv),
                       "ts": int(time.time())})
        json.dump(alerts[-30:], open(_sca.VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    if getattr(_sca, "TG", None) is not None:
        try:
            emo = "\U0001f534 SHORT" if side == "SHORT" else "\U0001f7e2 LONG"
            liqtxt = ("🧲 <b>liquidez a favor</b> (%s%%)" % u.get("liq_dist")) if u.get("liq_ok") else "sin imán de liquidación claro"
            prtxt = (" · ⭐<b>cargado %dx en 24h</b>" % u.get("primed24h", 0)) if u.get("primed24h", 0) >= 3 else ""
            tag = " · 🎯 MEJOR MOMENTO (gatillo+liquidez)" if u.get("best") else ""
            _sca.TG._send("⚡ <b>%s %s — CONFLUENCIA CONFIRMADA%s%s</b>\n"
                          "z/SOTT en <b>%d TFs</b> + <b>flip de flujo 5m</b> (surge %.1f) + a favor de tendencia 4h (%s) + %s.\n"
                          "<i>El momento óptimo del %s (no peleaba la tendencia). SL más allá del extremo.</i>"
                          % (sp, emo, tag, prtxt, u["z_agree"], u["surge"], u["trend4h"], liqtxt, word), force=True, zalert=True)
        except Exception:
            pass


_STOCKS = {"NVDAUSDT", "TSLAUSDT", "AAPLUSDT", "METAUSDT", "MSTRUSDT", "COINUSDT", "TQQQUSDT", "SOXLUSDT", "SNDKUSDT",
           "PLTRUSDT", "AMDUSDT", "GOOGLUSDT", "AMZNUSDT", "MSFTUSDT", "HOODUSDT", "MUUSDT", "NFLXUSDT", "AVGOUSDT", "SKHYNIXUSDT"}


def _ws_open():
    """Sesión regular Wall Street (9:30-16:00 NY, L-V) — las acciones sólo se mueven ahí."""
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        ny = _dt.datetime.now(ZoneInfo("America/New_York"))
        if ny.weekday() >= 5:
            return False
        hm = ny.hour * 60 + ny.minute
        return 9 * 60 + 30 <= hm < 16 * 60
    except Exception:
        u = _dt.datetime.utcnow()
        if u.weekday() >= 5:
            return False
        return 13 * 60 + 30 <= u.hour * 60 + u.minute < 20 * 60


def scan_and_alert(syms):
    setups = scan(syms)
    _wso = _ws_open()
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    now = time.time()
    for u in setups:
        if u["stage"] != "ENTRAR":
            continue
        # __NO_WS_GATE_2026_09_04__: sin gate "mercado cerrado" (usuario 2026-09-04: ningún activo se rechaza por WS cerrado; decide el flujo)
        key = "%s-%s" % (u["sym"], u["side"]); last = st.get(key, 0)
        if now - last < COOLDOWN:
            continue
        st[key] = now
        _emit(u)
    try:
        json.dump(st, open(STATE, "w"))
    except Exception:
        pass
    return setups


if __name__ == "__main__":
    U = SZ.roster()
    setups = scan(U)
    print("=== SETUPS por CONFLUENCIA z/SOTT multi-TF (con etapa + filtro tendencia 4h) ===")
    for u in setups:
        flag = "⚠PELEA-TEND" if u["contra"] else "✓ok-tend"
        print("  %-8s %-5s [%-8s] z%d sott%d · tend4h=%-5s %s · flip5m=%s surge=%.1f" % (
            u["sym"].replace("USDT", ""), u["side"], u["stage"], u["z_agree"], u["sott_agree"],
            u["trend4h"], flag, u["flip5m"], u["surge"]))
