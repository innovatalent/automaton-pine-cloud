# -*- coding: utf-8 -*-
"""ALERTA UNIFICADA: MOVIMIENTO INMINENTE desde EXTREMO con ALTA CONFIANZA (voz + Telegram).
Pedido usuario 2026-08-21: "unifica todo lo de hoy para que la voz/telegram me avise cuando hay un
movimiento inminente en algun activo, desde un extremo y con alta confianza".

Combina DOS cosas:
  A) EL EDGE MEDIDO (exhaustion_ignition, bt WR86% E24h+0.70 con SOTT bajo): EXTREMO + IGNICION.
       - EXTREMO  = SOTT en zona (COMPRA src<=1020 / VENTA src>=1080)  Y/O  z-score largo en extremo.
       - IGNICION = vela con volumen >= VOLX x avg20 + vela a favor + z-corto girando = 'movimiento inminente'.
  B) EL SCORE DE CONFLUENCIA de HOY (espejo del Pine 'pro confluence' bloque ⑬), 0-100%. PESOS RE-CALIBRADOS
     2026-08-21 con bt_imminent_clean.py (klines reales, metrica clean-1%): solo estos SUMAN clean-1% ->
       SOTT HTF coincide(3.5) + SOTT extremo(3.0) + bubble a favor(3.0) + z sale del extremo(2.0) +
       tendencia MTF(1.5) + ignicion(2.0).  Los que RESTAN (medido) van demotidos a 0.5 c/u:
       divergencia CVD, multidivergencia RSI, SMT vs BTC, toma de liquidez CRT.   max = 16.5
     CAVEAT: ningun subset llego a 50% clean robusto OOS -> el % es PRIORIDAD de setup, no probabilidad.

Solo avisa si:  EXTREMO (hard) Y IGNICION (hard) Y score >= SCORE_MIN (alta confianza).
Dedup por latch + cooldown por activo+lado. Gate asset_quality.alert_ok (solo validados WR>=60).
Salidas: Telegram (telegram_notify._send zalert=True) + cola de voz (zscore_alerts.json que el dashboard habla).
NO opera; solo avisa. Se invoca desde el ciclo de 3 min (zscore_vol_cvd_paper) -> sin tarea/consola nueva.
"""
import os, sys, json, time
import urllib.request as _ur

os.environ.setdefault("ENGINE_TG", "1")   # habilita telegram SOLO en este proceso (igual que zscore_alert)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "direction_engine"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import sott_zone as SZ                    # SOTT 500/200/+1000 (idem Pine) -> compute_series(h,l,c)
try:
    import telegram_notify as TG
except Exception:
    TG = None
try:
    import asset_quality as AQ
except Exception:
    AQ = None
try:
    import bitunix as BX
except Exception:
    BX = None

# --------------------------------------------------------------------------------------------------
# PARAMETROS
CHART_TF   = "1h"      # TF principal (el edge medido es 1h)
HTF_TF     = "4h"      # TF superior para confirmar el SOTT
Z_LONG_LB  = 100       # gauge de agotamiento (oversold/overbought)
Z_SHORT_LB = 24        # el giro
Z_OS       = -1.2      # z_long <= esto = agotamiento bajista (piso) -> LONG
Z_OB       = 1.2       # z_long >= esto = agotamiento alcista (techo) -> SHORT
VOLX_MIN   = 2.5       # volumen de la vela senal >= 2.5x avg20 = 'entrada violenta' (ignicion)
SCORE_MIN  = int(os.getenv("IMM_SCORE_MIN", "62"))   # % de confluencia minimo para avisar (alta confianza)
COOLDOWN   = 90 * 60   # 90 min entre alertas del mismo activo+lado
SWEEP_LB   = 20        # ventana para la toma de liquidez (CRT/Turtle Soup)
BUBBLE_LB  = 200       # ventana del percentil de la bubble de delta
MAXSC      = 16.5      # suma de pesos (RE-PESADO 2026-08-21 con bt_imminent_clean: HTF/bubble/SOTT SUMAN clean-1%
#                        (+14/+11/+9 lift); SMT/multidiv/divCVD/CRT RESTAN (-12/-8/-7/-6) -> demotidos a 0.5)

STATE = os.path.join(HERE, "imminent_alert_state.json")
VOICE = os.path.join(ROOT, "direction_engine", "zscore_alerts.json")

# universo = los validados para alerta (asset_quality.alert_ok). Fallback: cripto liquida + ETFs.
UNIVERSE = ["ENAUSDT", "LDOUSDT", "ORDIUSDT", "NEARUSDT", "INJUSDT", "WLDUSDT", "STXUSDT",
            "APTUSDT", "GALAUSDT", "OPUSDT", "ARBUSDT", "TQQQUSDT", "SOXLUSDT"]


# --------------------------------------------------------------------------------------------------
# DATA
def _binance(sym, tf, limit):
    u = "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=%s&limit=%d" % (sym, tf, limit)
    r = json.load(_ur.urlopen(_ur.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=12))
    return [[float(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in r]


def _klines(sym, tf, limit=1000):
    """Binance futures (= grafico del usuario) para cripto; Bitunix para ETFs/stocks. None si no hay data."""
    try:
        K = _binance(sym, tf, limit)
        if K and len(K) >= 560:
            return K
    except Exception:
        pass
    if BX is not None:
        try:
            K = BX.klines_smart(sym, tf, limit)
            if K:
                return [[float(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in K]
        except Exception:
            pass
    return None


# --------------------------------------------------------------------------------------------------
# HELPERS de features
def _z(vals, lb, i):
    if i < lb:
        return 0.0
    w = vals[i - lb:i]
    m = sum(w) / lb
    var = sum((x - m) ** 2 for x in w) / lb
    sd = var ** 0.5
    return (vals[i] - m) / sd if sd > 0 else 0.0


def _delta(K, j):
    # delta = MISMA formula de TradingView / del panel EXTREMOS: v*((c-l)-(h-c))/rng = v*(2c-h-l)/rng
    # (posicion del CLOSE en el rango H-L, no el cuerpo). FIX 2026-09-02: antes v*(c-o)/rng NO coincidia
    # con TradingView ni con vwap_profile._delta -> las alertas (scan_unified cvd_ok) usaban delta erroneo. 'o' sin uso.
    o, h, l, c, v = K[j][1], K[j][2], K[j][3], K[j][4], K[j][5]
    return v * ((c - l) - (h - c)) / max(h - l, 1e-9)


def _rsi(closes, n=14):
    out = [50.0] * len(closes)
    if len(closes) < n + 1:
        return out
    g = 0.0; ls = 0.0
    for i in range(1, n + 1):
        ch = closes[i] - closes[i - 1]
        g += max(ch, 0); ls += max(-ch, 0)
    ag = g / n; al = ls / n
    out[n] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    for i in range(n + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(ch, 0)) / n
        al = (al * (n - 1) + max(-ch, 0)) / n
        out[i] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    return out


# --------------------------------------------------------------------------------------------------
# SCORE de confluencia (espejo del Pine ⑬), por lado
def _score(K, ser, side, htf_zone_ok, btc_c):
    """Devuelve (score0_100, dict de componentes activos). side = 'LONG' o 'SHORT'."""
    n = len(K)
    i = n - 1
    c = [b[4] for b in K]; o = [b[1] for b in K]; h = [b[2] for b in K]; l = [b[3] for b in K]; v = [b[5] for b in K]
    comp = {}
    sc = 0.0
    up = side == "LONG"

    # (1) SOTT extremo en el chart TF (zona baja para LONG, alta para SHORT)  -> 3.0
    zone = ser[i]["zone"]
    sott_ok = (up and zone == "COMPRA") or (not up and zone == "VENTA")
    # tambien cuenta si estuvo en zona en las ultimas 3 velas (no todo cae en 1 vela)
    if not sott_ok:
        sott_ok = any(((up and ser[j]["zone"] == "COMPRA") or (not up and ser[j]["zone"] == "VENTA"))
                      for j in range(max(0, i - 2), i + 1))
    if sott_ok:
        sc += 3.0; comp["SOTT_extremo"] = 3.0

    # (2) SOTT HTF coincide (la TS superior tambien en zona a favor)  -> 2.5
    if htf_zone_ok:
        sc += 3.5; comp["SOTT_HTF"] = 3.5

    # (3) z-score SALE del extremo a favor (user: reforzar cuando el z sale de zona extrema)  -> 2.5
    zl_min = min(_z(c, Z_LONG_LB, j) for j in range(max(Z_LONG_LB, i - 5), i + 1))
    zl_max = max(_z(c, Z_LONG_LB, j) for j in range(max(Z_LONG_LB, i - 5), i + 1))
    zs = _z(c, Z_SHORT_LB, i); zs_prev = _z(c, Z_SHORT_LB, i - 1)
    if up:
        z_exit = zl_min <= Z_OS and zs > zs_prev            # venia sobrevendido y el corto gira arriba
    else:
        z_exit = zl_max >= Z_OB and zs < zs_prev            # venia sobrecomprado y el corto gira abajo
    if z_exit:
        sc += 2.0; comp["z_sale_extremo"] = 2.0

    # (4) bubble de delta A FAVOR (vela con delta grande, signo del lado)  -> 2.0
    if i >= BUBBLE_LB:
        deltas = [abs(_delta(K, j)) for j in range(i - BUBBLE_LB, i)]
        srt = sorted(deltas); p85 = srt[int(len(srt) * 0.85)]
        d_now = _delta(K, i)
        bubble = abs(d_now) >= p85 and ((up and d_now > 0) or (not up and d_now < 0))
        if bubble:
            sc += 3.0; comp["bubble"] = 3.0

    # (5) tendencia MTF (EMA50 del chart alineada al giro que buscamos)  -> 2.0
    #     en reversion desde piso, basta que la vela recupere sobre la EMA rapida (no seguir en caida libre)
    ema = c[0]
    a = 2.0 / (20 + 1)
    for x in c:
        ema = a * x + (1 - a) * ema
    mtf = (up and c[i] >= ema) or (not up and c[i] <= ema)
    if mtf:
        sc += 1.5; comp["tendencia"] = 1.5

    # (6) divergencia CVD (precio LL / cvd HL para bull; al reves para bear)  -> 1.5
    if i >= 12:
        cvd = [0.0] * n
        for t in range(1, n):
            cvd[t] = cvd[t - 1] * 0.9 + _delta(K, t)
        if up:
            div = l[i] <= min(l[i - 10:i]) and cvd[i] > min(cvd[i - 10:i])
        else:
            div = h[i] >= max(h[i - 10:i]) and cvd[i] < max(cvd[i - 10:i])
        if div:
            sc += 0.5; comp["div_CVD"] = 0.5

    # (7) multidivergencia RSI (regular + hidden)  -> 1.5
    rsi = _rsi(c)
    if i >= 12:
        pl = min(range(i - 10, i), key=lambda j: l[j])     # pivote low reciente
        ph = max(range(i - 10, i), key=lambda j: h[j])     # pivote high reciente
        if up:
            reg = l[i] < l[pl] and rsi[i] > rsi[pl]         # precio LL, RSI HL
            hid = l[i] > l[pl] and rsi[i] < rsi[pl]         # precio HL, RSI LL (continuacion)
        else:
            reg = h[i] > h[ph] and rsi[i] < rsi[ph]
            hid = h[i] < h[ph] and rsi[i] > rsi[ph]
        if reg or hid:
            sc += 0.5; comp["multidiv_RSI"] = 0.5

    # (8) SMT vs BTC (el activo hace nuevo extremo pero BTC NO = divergencia inter-mercado)  -> 1.5
    if btc_c is not None and len(btc_c) >= n and i >= 10:
        bi = len(btc_c) - 1
        if up:
            smt = l[i] <= min(l[i - 10:i]) and btc_c[bi] > min(btc_c[bi - 10:bi])
        else:
            smt = h[i] >= max(h[i - 10:i]) and btc_c[bi] < max(btc_c[bi - 10:bi])
        if smt:
            sc += 0.5; comp["SMT_vs_BTC"] = 0.5

    # (9) toma de LIQUIDEZ CRT / Turtle Soup (barrio el extremo previo y cerro de vuelta adentro)  -> 2.0
    if i >= SWEEP_LB + 1:
        prior_low = min(l[i - SWEEP_LB:i])
        prior_high = max(h[i - SWEEP_LB:i])
        if up:
            sweep = l[i] < prior_low and c[i] > prior_low      # mecho abajo el piso y recupero
        else:
            sweep = h[i] > prior_high and c[i] < prior_high     # mecho arriba el techo y rechazo
        if sweep:
            sc += 0.5; comp["toma_liquidez_CRT"] = 0.5

    # (10) IGNICION de volumen (>= VOLX x avg20 + vela a favor)  -> 2.0
    avgv = sum(v[i - 20:i]) / 20 if i >= 20 else 0
    volx = v[i] / avgv if avgv > 0 else 0
    candle_ok = (up and c[i] > o[i]) or (not up and c[i] < o[i])
    ignition = volx >= VOLX_MIN and candle_ok
    if ignition:
        sc += 2.0; comp["ignicion_vol"] = 2.0

    score = int(round(sc / MAXSC * 100))
    return score, comp, {"volx": volx, "zl": _z(c, Z_LONG_LB, i), "zs": zs,
                         "ignition": ignition, "sott_extremo": sott_ok, "z_exit": z_exit,
                         "sweep": comp.get("toma_liquidez_CRT", 0) > 0, "px": c[i]}


# --------------------------------------------------------------------------------------------------
def _htf_zone_ok(sym, side):
    """El SOTT de la TS superior (4h) tambien en zona a favor. Fail-open (no bloquea) si no hay data 4h."""
    K = _klines(sym, HTF_TF, 1000)
    if not K or len(K) < 560:
        return False
    h = [b[2] for b in K]; l = [b[3] for b in K]; c = [b[4] for b in K]
    ser = SZ.compute_series(h, l, c)
    z = ser[-1]["zone"]
    return (side == "LONG" and z == "COMPRA") or (side == "SHORT" and z == "VENTA")


def _load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def _spoken(sym):
    return sym.replace("USDT", "")


def _fmt_px(p):
    return ("%.2f" % p) if p >= 10 else ("%.4f" % p)


def _num_es(n):
    m = {0: "cero", 10: "diez", 20: "veinte", 30: "treinta", 40: "cuarenta", 50: "cincuenta",
         60: "sesenta", 62: "sesenta y dos", 65: "sesenta y cinco", 70: "setenta", 75: "setenta y cinco",
         80: "ochenta", 85: "ochenta y cinco", 90: "noventa", 95: "noventa y cinco", 100: "cien"}
    k = min(m.keys(), key=lambda x: abs(x - n))
    return m[k]


def _emit(sym, side, score, comp, extra):
    now = time.time()
    sp = _spoken(sym)
    word = "long" if side == "LONG" else "short"
    zona = "piso" if side == "LONG" else "techo"
    activos = " · ".join(k.replace("_", " ") for k in comp.keys())
    # TELEGRAM
    if TG is not None:
        try:
            emo = "\U0001f7e2 LONG" if side == "LONG" else "\U0001f534 SHORT"
            TG._send(
                "⚡ <b>%s %s — MOVIMIENTO INMINENTE</b>\n"
                "Confianza <b>%d%%</b> · desde el <b>%s</b> (extremo)\n"
                "vol <b>%.1fx</b> · z_long %+.2f · z_corto %+.2f\n"
                "Confluencia: %s\n"
                "precio $%s"
                % (sp, emo, score, zona, extra["volx"], extra["zl"], extra["zs"], activos, _fmt_px(extra["px"])),
                force=True, zalert=True)
        except Exception:
            pass
    # VOZ (arranca con 'long en'/'short en' para pasar el whitelist del dashboard)
    try:
        text = "%s en %s, movimiento inminente desde %s, confianza %s por ciento" % (word, sp, zona, _num_es(score))
        alerts = _load(VOICE, [])
        alerts.append({"id": "imm-%s-%s-%d" % (sym, side, int(now)), "asset": sym, "side": side,
                       "text": text, "score": score, "ts": int(now)})
        alerts = alerts[-30:]
        json.dump(alerts, open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass


def scan(dry=False, verbose=False):
    """Escanea el universo. dry=True -> no envia, solo devuelve la tabla (para test)."""
    st = _load(STATE, {})
    now = time.time()
    rows = []
    fired = 0
    # BTC 1h una sola vez (para el SMT)
    btc = _klines("BTCUSDT", CHART_TF, 1000)
    btc_c = [b[4] for b in btc] if btc else None
    for sym in UNIVERSE:
        try:
            if AQ is not None and not AQ.alert_ok(sym):
                continue
            K = _klines(sym, CHART_TF, 1000)
            if not K or len(K) < 560:
                continue
            ser = SZ.compute_series([b[2] for b in K], [b[3] for b in K], [b[4] for b in K])
            # lado candidato segun el extremo (SOTT o z_long)
            i = len(K) - 1
            c = [b[4] for b in K]
            zone = ser[i]["zone"]
            zl = _z(c, Z_LONG_LB, i)
            side = None
            if zone == "COMPRA" or zl <= Z_OS:
                side = "LONG"
            elif zone == "VENTA" or zl >= Z_OB:
                side = "SHORT"
            if side is None:
                continue
            # HTF confirm solo para candidatos (ahorra requests)
            htf_ok = _htf_zone_ok(sym, side)
            score, comp, extra = _score(K, ser, side, htf_ok, btc_c)
            extreme = extra["sott_extremo"] or (side == "LONG" and zl <= Z_OS) or (side == "SHORT" and zl >= Z_OB)
            gate = extreme and extra["ignition"] and score >= SCORE_MIN
            rows.append((sym, side, score, extra["volx"], zl, extreme, extra["ignition"], gate, comp))
            if dry:
                continue
            if not gate:
                continue
            # dedup latch + cooldown
            s = st.get(sym, {})
            if s.get("side") == side and (now - s.get("ts", 0)) < COOLDOWN:
                continue
            _emit(sym, side, score, comp, extra)
            st[sym] = {"side": side, "ts": now, "score": score}
            fired += 1
        except Exception:
            continue
    if not dry:
        try:
            json.dump(st, open(STATE, "w"))
        except Exception:
            pass
    if verbose or dry:
        print("=== IMMINENT MOVE (extremo + ignicion + confluencia>=%d%%) ===" % SCORE_MIN)
        print("%-10s %-5s %5s %6s %7s %4s %4s %4s" % ("sym", "side", "score", "volx", "z_long", "EXT", "IGN", "GO"))
        for r in sorted(rows, key=lambda x: -x[2]):
            print("%-10s %-5s %4d%% %5.1fx %+6.2f %4s %4s %4s  %s" %
                  (r[0], r[1], r[2], r[3], r[4], "Y" if r[5] else "-", "Y" if r[6] else "-",
                   "GO" if r[7] else "-", ",".join(r[8].keys())))
    return fired


if __name__ == "__main__":
    import sys as _s
    scan(dry=("--dry" in _s.argv), verbose=True)
