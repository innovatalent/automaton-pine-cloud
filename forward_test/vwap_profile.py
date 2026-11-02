# -*- coding: utf-8 -*-
"""PERFIL DE VOLUMEN POR DELTA + VWAP 24h EN VIVO (port del Pine del usuario 'volume_profile_vwap_24h', 2026-08-27).

Calcula, en Python, lo mismo que el indicador del chart para poder ENRIQUECER las alertas (voz/telegram) y
MEDIR si mejora las entradas antes de cablearlo como gate:
  - POC (Point of Control) 24h = el precio con MAS volumen -> IMAN (el mercado tiende a volver a buscarlo).
  - Value Area (70%) VAH/VAL: si el precio esta FUERA de la VA tiende a revertir hacia el POC.
  - Delta-profile: quien domina POR NIVEL (buy vs sell) -> soporte de absorcion / resistencia de distribucion.
  - VWAP rolling 24h + bandas +/- k*sigma (SIN reset): posicion en sigmas. |sigma|>=2.5 = estirado (fade).
Datos: reusa imminent_move_alert._klines (Binance futuros cripto / Bitunix ETFs). K=[t,o,h,l,c,v].
Uso:  from vwap_profile import verdict, line ; verdict("TAOUSDT")   /   python vwap_profile.py TAOUSDT TQQQUSDT
"""

BARS_24H = {"5m": 288, "15m": 96, "1h": 24, "3m": 480}
NBINS = 100        # bins del perfil (2026-08-28: 48->100 para POC más fino, más cerca de la resolución de TradingView)
VA_PCT = 0.70          # value area = 70% del volumen alrededor del POC
SIGMA_K = 2.5          # banda de VWAP que consideramos "extremo" (fade)


def _delta(o, h, l, c, v):
    """Delta por vela = MISMA formula del indicador CVD de TradingView (TradingFinder 'CVD Divergence Oscillator'):
    Buying - Selling = v*(c-l)/(h-l) - v*(h-c)/(h-l) = v*((c-l)-(h-c))/(h-l) = v*(2c-h-l)/(h-l).
    FIX 2026-09-02: antes usaba v*(c-o)/rng (close vs OPEN = cuerpo) -> NO coincidia con TradingView. Ahora usa la
    posicion del CLOSE en el rango H-L (= TradingView + los backtests de hoy bt_extremes_trigger). 'o' queda sin uso."""
    rng = max(h - l, 1e-9)
    return v * ((c - l) - (h - c)) / rng


def _adx_last(h, l, c, n=14):
    """ADX de Wilder (último valor) = fuerza de TENDENCIA. >=25 tendencia / <20 chop. Define el RÉGIMEN (pedido usuario
    2026-09-03: en tendencia se ACOMPAÑA el extremo, en chop se FADEA — validado bt_vwap_extreme_cont_vs_fade.py)."""
    m = len(c)
    if m < 2 * n + 2:
        return 0.0
    lo = max(1, m - 120)   # últimas ~120 velas alcanzan para estabilizar ADX(14)
    tr = []; pdm = []; ndm = []
    for i in range(lo, m):
        up = h[i] - h[i - 1]; dn = l[i - 1] - l[i]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    if len(tr) < 2 * n:
        return 0.0
    def _rma(x):
        out = [sum(x[:n]) / n]
        for i in range(n, len(x)):
            out.append((out[-1] * (n - 1) + x[i]) / n)
        return out
    atr = _rma(tr); rp = _rma(pdm); rn = _rma(ndm)
    dx = []
    for i in range(len(atr)):
        if atr[i] > 0:
            pdi = 100 * rp[i] / atr[i]; ndi = 100 * rn[i] / atr[i]; s = pdi + ndi
            dx.append(100 * abs(pdi - ndi) / s if s > 0 else 0.0)
    if len(dx) < n:
        return 0.0
    adx = sum(dx[:n]) / n
    for i in range(n, len(dx)):
        adx = (adx * (n - 1) + dx[i]) / n
    return adx


def profile(sym, tf="5m", bars=None):
    """Perfil de volumen+delta 24h + VWAP+/-sigma. Devuelve dict con POC/VA/VWAP o None si no hay data."""
    import imminent_move_alert as IMM
    n = bars or BARS_24H.get(tf, 288)
    K = IMM._klines(sym, tf, n + 5)
    # ANTI-FLICKER (pedido usuario 2026-09-02 "el SHORT a los segundos se vuelve tendencia = falsa entrada"): dropear la
    # vela EN FORMACIÓN (la última que devuelve el exchange) = barstate.isconfirmed del Pine. Las señales (SHORT/OK/cont)
    # SOLO cambian al CERRAR la vela, no intra-bar. Elimina el parpadeo (ej SUI que ponía SHORT y a los segundos era tendencia).
    if K and len(K) > 3:
        K = K[:-1]
    return compute(K, n, sym=sym)


def compute(K, n=288, sym=""):
    """Nucleo: computa el perfil sobre klines YA provistos K=[t,o,h,l,c,v] (para medir historicamente sin fetch)."""
    if not K or len(K) < 30:
        return None
    K = K[-n:] if len(K) > n else K
    o = [k[1] for k in K]; h = [k[2] for k in K]; l = [k[3] for k in K]; c = [k[4] for k in K]; v = [k[5] for k in K]
    px = c[-1]
    lo_all = min(l); hi_all = max(h)
    if hi_all <= lo_all:
        return None
    # ---- VWAP rolling 24h + sigma (ponderado por volumen, HLC3) ----
    sv = sum(v) or 1e-9
    tp = [(h[i] + l[i] + c[i]) / 3.0 for i in range(len(K))]
    vwap = sum(tp[i] * v[i] for i in range(len(K))) / sv
    var = sum(v[i] * (tp[i] - vwap) ** 2 for i in range(len(K))) / sv
    sigma = var ** 0.5
    sig_pos = (px - vwap) / sigma if sigma > 0 else 0.0     # posicion del precio en sigmas
    # ---- perfil por bins: reparte volumen y delta de cada vela por el rango que ocupa ----
    binsz = (hi_all - lo_all) / NBINS
    vol_bin = [0.0] * NBINS
    dlt_bin = [0.0] * NBINS
    for i in range(len(K)):
        d = _delta(o[i], h[i], l[i], c[i], v[i])
        b0 = int((l[i] - lo_all) / binsz); b1 = int((h[i] - lo_all) / binsz)
        b0 = min(max(b0, 0), NBINS - 1); b1 = min(max(b1, 0), NBINS - 1)
        span = b1 - b0 + 1
        for b in range(b0, b1 + 1):
            vol_bin[b] += v[i] / span
            dlt_bin[b] += d / span
    tot = sum(vol_bin) or 1e-9
    poc_i = max(range(NBINS), key=lambda b: vol_bin[b])
    poc = lo_all + (poc_i + 0.5) * binsz
    # ---- value area 70% expandiendo desde el POC hacia el lado mas pesado ----
    lo_i = hi_i = poc_i
    acc = vol_bin[poc_i]
    while acc < VA_PCT * tot and (lo_i > 0 or hi_i < NBINS - 1):
        up = vol_bin[hi_i + 1] if hi_i < NBINS - 1 else -1
        dn = vol_bin[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1; acc += max(up, 0)
        else:
            lo_i -= 1; acc += max(dn, 0)
    vah = lo_all + (hi_i + 1) * binsz
    val = lo_all + lo_i * binsz
    # ---- delta del nivel actual (bin donde esta el precio) + delta neto total ----
    cur_i = min(max(int((px - lo_all) / binsz), 0), NBINS - 1)
    lvl_delta = dlt_bin[cur_i]
    net_delta = sum(dlt_bin)
    # LIQUIDEZ ABAJO vs ARRIBA (pedido usuario 2026-09-03: comparar en vivo qué lado está MÁS cargado de volumen → el precio
    # tiende a ir a buscar la liquidez mayor). vol_below = % del volumen 24h por DEBAJO del precio actual.
    vol_below = round(100.0 * sum(vol_bin[:cur_i]) / (tot or 1))
    # delta-POC = bin con delta mas comprador y mas vendedor (absorcion / distribucion)
    dbuy_i = max(range(NBINS), key=lambda b: dlt_bin[b])
    dsell_i = min(range(NBINS), key=lambda b: dlt_bin[b])
    # ---- FLUJO (mismos indicadores del Pine cvd_divergence_flujo_ok_SYNC): flow2 + CVD (5m) + flow2 sup + CVD sup (1h) + SYNC ----
    def _sgn(x): return 1 if x > 0 else (-1 if x < 0 else 0)
    _d5 = [_delta(o[j], h[j], l[j], c[j], v[j]) for j in range(max(0, len(K) - 4), len(K))]
    flow2 = 1 if (len(_d5) >= 2 and _d5[-1] > 0 and _d5[-2] > 0) else (-1 if (len(_d5) >= 2 and _d5[-1] < 0 and _d5[-2] < 0) else 0)
    recent_cvd = _sgn(sum(_d5[-3:]))                               # CVD reciente 5m (últimas 3): +comprador/-vendedor
    # 1h por RESAMPLE de las 5m (12 velas = 1h), sin fetch extra
    _H = [[K[i][0], K[i][1], max(x[2] for x in K[i:i + 12]), min(x[3] for x in K[i:i + 12]), K[i + 11][4], sum(x[5] for x in K[i:i + 12])]
          for i in range(0, (len(K) // 12) * 12 - 11, 12)]
    _dH = [_delta(k[1], k[2], k[3], k[4], k[5]) for k in _H[-4:]] if len(_H) >= 2 else [0.0, 0.0]
    flow2_sup = 1 if (len(_dH) >= 2 and _dH[-1] > 0 and _dH[-2] > 0) else (-1 if (len(_dH) >= 2 and _dH[-1] < 0 and _dH[-2] < 0) else 0)
    cvd_sup = _sgn(sum(_dH[-3:]))                                  # CVD sup (1h): +comprador/-vendedor
    # SYNC (régimen): ATR% actual vs su media (tape VIVO si está por encima) — igual que sync_check
    _tr = [max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1])) for j in range(1, len(K))]
    _ap = [(sum(_tr[k - 14:k]) / 14) / c[k] * 100 for k in range(14, len(_tr))] if len(_tr) > 20 else []
    sync_alive = (_ap[-1] > sum(_ap[-50:]) / len(_ap[-50:])) if len(_ap) > 50 else None
    # DELTA SURGE (gatillo validado bt_extremes_trigger 2026-09-02): |delta reciente 3v| / promedio |delta| 20v.
    # >=1.5 + delta CONTRA el extremo = el gatillo de la reversion (P mitad 58% vs 46% base).
    _alldl = [abs(_delta(o[j], h[j], l[j], c[j], v[j])) for j in range(len(K))]
    _davg = (sum(_alldl[-20:]) / min(20, len(_alldl))) if _alldl else 0.0
    _recent3 = abs(sum(_d5[-3:]))
    delta_surge = round(_recent3 / _davg, 2) if _davg > 0 else 0.0
    # CVD ACUMULADO (Hist del Pine v8 = Σ delta sobre Period=21) = el "flujo de fondo". Su SIGNO/cruce de cero = el
    # flujo acumulado cambió de mano (gatillo de reversión del v8). Se computa sobre la serie COMPLETA (no las 4 de _d5).
    _CVD_P = 21
    _dfull = [_delta(o[j], h[j], l[j], c[j], v[j]) for j in range(len(K))]
    hist = sum(_dfull[-_CVD_P:]) if _dfull else 0.0
    hist_prev = sum(_dfull[-_CVD_P - 1:-1]) if len(_dfull) > _CVD_P else hist
    # recentCVD del v8 (valor y su valor 2 barras atras) para buyBuilding/sellBuilding = delta contrario subiendo/bajando.
    recent_raw = sum(_dfull[-3:]) if len(_dfull) >= 3 else 0.0
    recent_prev = sum(_dfull[-5:-2]) if len(_dfull) >= 5 else 0.0
    # VOLUMEN importante (pedido usuario "que entró volumen importante"): ratio vela actual vs media 20.
    vol_ratio = (v[-1] / (sum(v[-21:-1]) / 20.0)) if len(v) >= 22 and sum(v[-21:-1]) > 0 else 1.0
    adx = _adx_last(h, l, c, 14)   # RÉGIMEN: >=25 tendencia (acompañar) / <20 chop (fadear)
    # CLUSTER-REACH (2026-09-02): niveles de alta prob de toque apilados en una banda = iman fuerte para rankear activos.
    # Validado bt_cluster_reach.py: ALTO(>=1.6) llega 28%/E+0.28 vs medio(1.0-1.6) 15%/+0.10. reach(lvl)=2*(1-Phi(|lvl-px|/sigma)).
    import math as _m
    # sigma de reach = ATR * sqrt(N) (movimiento esperado ~24h) = el modelo CALIBRADO (bt_reach_calib: +1% ~65%).
    # OJO: NO usar el sigma del VWAP (dispersion) -> daba % mal calibrado (ej 13% en vez de 65%).
    _trs = [max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1])) for j in range(1, len(c))]
    _atr14 = (sum(_trs[-14:]) / min(14, len(_trs))) if _trs else 0.0
    reach_sig = _atr14 * (len(c) ** 0.5)
    # __ATR1H_2026_10_18__ ATR(14) de 1 HORA desde estas mismas barras de 5 m (sin red
    # extra). Hace falta en la escala de 1 h, no la de 5 m: el rango medio de una barra
    # de 5 m es 4-5 veces menor, y es la escala de 1 h la que usa el Pine para clasificar
    # volatilidad y con la que esta calibrada la tabla de alcance.
    _h1 = []
    for _k in range(max(0, len(c) - 15 * 12), len(c), 12):
        if _k + 12 > len(c):
            break
        _h1.append((max(h[_k:_k + 12]), min(l[_k:_k + 12]), c[_k + 11]))
    atr1h_pct = 0.0
    if len(_h1) >= 2 and px > 0:
        _t1 = [max(_h1[j][0] - _h1[j][1], abs(_h1[j][0] - _h1[j - 1][2]),
                   abs(_h1[j][1] - _h1[j - 1][2])) for j in range(1, len(_h1))]
        atr1h_pct = sum(_t1) / len(_t1) / px * 100.0
    def _reachp(_lv):
        if reach_sig <= 0 or px <= 0:
            return 0.0
        return 2.0 * (1.0 - 1.0 / (1.0 + _m.exp(-1.702 * abs(_lv - px) / reach_sig)))
    # niveles agrupables = perfil (POC/VWAP/VAH/VAL) + liquidez 24h (maximo/minimo NO testeados)
    _levs = [poc, vwap, vah, val, hi_all, lo_all]
    # banda 0.5% = OPTIMO por backtest bt_group_band (reach zonas apiladas 62%; apilar 2+ es el edge vs suelto 22-45%)
    cluster_reach = 0.0
    cluster_n = 1
    for _a in _levs:
        _grp = [_b for _b in _levs if abs(_b - _a) / px * 100 <= 0.5]
        _s = sum(_reachp(_b) for _b in _grp)
        if _s > cluster_reach:
            cluster_reach = _s
            cluster_n = len(_grp)
    cluster_reach = round(cluster_reach, 2)
    return {
        "cluster_reach": cluster_reach, "cluster_n": cluster_n, "reach_poc": round(_reachp(poc) * 100), "reach_vwap": round(_reachp(vwap) * 100), "reach_sig": round(reach_sig, 6),
        "atr1h_pct": round(atr1h_pct, 4),
        "sym": sym, "px": px, "poc": poc, "poc_dist": round((poc - px) / px * 100, 2),
        "vah": vah, "val": val, "in_va": val <= px <= vah,
        "vwap": vwap, "sigma": sigma, "sig_pos": round(sig_pos, 2),
        "up_band": vwap + SIGMA_K * sigma, "dn_band": vwap - SIGMA_K * sigma,
        "lvl_delta": lvl_delta, "net_delta": net_delta, "delta_surge": delta_surge, "vol_below": vol_below,
        "flow2": flow2, "recent_cvd": recent_cvd, "flow2_sup": flow2_sup, "cvd_sup": cvd_sup, "sync_alive": sync_alive,
        "hist": hist, "hist_prev": hist_prev, "hist_rising": hist > hist_prev,
        "recent_raw": recent_raw, "recent_prev": recent_prev, "adx": round(adx, 1),
        "dbuy_px": lo_all + (dbuy_i + 0.5) * binsz, "dsell_px": lo_all + (dsell_i + 0.5) * binsz,
    }


def poc_dir(sym, tf="5m", min_dist=0.15):
    """Direccion hacia el POC (iman de volumen). LONG si el POC esta ARRIBA, SHORT si esta ABAJO. NEUTRO si muy cerca."""
    p = profile(sym, tf)
    if not p:
        return "NEUTRO", None
    if abs(p["poc_dist"]) < min_dist:
        return "NEUTRO", p
    return ("LONG" if p["poc_dist"] > 0 else "SHORT"), p


def line(sym, side=None, tf="5m"):
    """Linea corta para la alerta (voz/telegram). Devuelve (tag, texto) o (None, None) si no hay data.
    tag: 'IMAN POC' (precio fuera de VA, pull al POC) / 'VWAP EXTREMO' (|sigma|>=k, fade) / 'NIVEL' (info)."""
    p = profile(sym, tf)
    if not p:
        return None, None
    _f = (lambda x: ("%.2f" % x) if x >= 10 else ("%.4f" % x))
    poc_toward = ("a favor" if side is None else
                  ("a favor" if (side == "LONG") == (p["poc_dist"] > 0) else "EN CONTRA"))
    dlt = "comprador" if p["lvl_delta"] > 0 else ("vendedor" if p["lvl_delta"] < 0 else "neutro")
    # tag = MISMO tier que el panel del dashboard (interconexión, evitar contradicciones): EXTREMO>=2.5 / FUERTE(PRIME)>=2.0
    _a = abs(p["sig_pos"])
    if _a >= SIGMA_K:
        tag = "\U0001f3af EXTREMO"          # 2.5σ+ (= EXTREMO en el panel)
    elif _a >= 2.0:
        tag = "\U0001f3af PRIME"            # 2.0σ+ (= FUERTE/zona prime del panel = mejores operaciones)
    elif not p["in_va"]:
        tag = "IMAN POC"                    # fuera de VA, <2σ (= leve en el panel)
    else:
        tag = "NIVEL"
    # CONV = flujo que confirma la reversión (flow2+CVD 5m + flow2+CVD 1h + delta), en la dirección del trade
    _want = -1 if side == "SHORT" else (1 if side == "LONG" else None)
    _conv = ""
    if _want is not None:
        _ld = 1 if p["lvl_delta"] > 0 else (-1 if p["lvl_delta"] < 0 else 0)
        _c = sum(1 for v in [p.get("flow2", 0), p.get("recent_cvd", 0), p.get("flow2_sup", 0), p.get("cvd_sup", 0), _ld] if v == _want)
        _sy = ("SYNC" if p.get("sync_alive") else "chop") if p.get("sync_alive") is not None else "s/d"
        _conv = " | conv %d/5 flujo · %s" % (_c, _sy)
    txt = "POC %s (%+.2f%% %s) | VWAP %+.1fsigma | delta %s%s" % (
        _f(p["poc"]), p["poc_dist"], poc_toward, p["sig_pos"], dlt, _conv)
    return tag, txt


def mtf_reversion(sym, tfs=("5m", "15m")):
    """SETUP REVERSION AL POC (pedido usuario 2026-08-27):
    SHORT si en TODOS los tfs el precio esta ARRIBA y AFUERA del value area (px>VAH) Y el delta del nivel es VENDEDOR.
    LONG espejo: px<VAL Y delta comprador. Objetivo SIEMPRE = POC (el iman de volumen). None si no dispara."""
    ps = {}
    for tf in tfs:
        p = profile(sym, tf)
        if not p:
            return None
        ps[tf] = p
    short = all(ps[tf]["px"] > ps[tf]["vah"] and ps[tf]["lvl_delta"] < 0 for tf in tfs)
    long_ = all(ps[tf]["px"] < ps[tf]["val"] and ps[tf]["lvl_delta"] > 0 for tf in tfs)
    if not (short or long_):
        return None
    side = "SHORT" if short else "LONG"
    p5 = ps[tfs[0]]
    target = p5["poc"]                                  # objetivo = POC del tf rapido (el mas accionable)
    dist = (target - p5["px"]) / p5["px"] * 100.0
    return {"sym": sym, "side": side, "px": p5["px"], "target_poc": target, "dist_poc": round(dist, 2),
            "tfs": list(tfs), "sig_pos": {tf: ps[tf]["sig_pos"] for tf in tfs},
            "lvl_delta": {tf: ps[tf]["lvl_delta"] for tf in tfs}}


def fade_signal(sym, K=None, sig_thr=2.5, fast_pct=1.5):
    """SETUP FADE VWAP 2.5sigma VALIDADO (bt_vwap_fade.py, salida TRAILING): fade del extremo |sigma|>=sig_thr en 5m
    con confirmacion RÁPIDO (movio >=fast_pct% en 6 velas hacia el extremo) O GIRO (última vela con delta en contra).
    Objetivo referencia = VWAP/POC pero se OPERA CON TRAILING (runner). Devuelve dict o None. K opcional (klines 5m)."""
    if K is None:
        import imminent_move_alert as IMM
        K = IMM._klines(sym, "5m", 300)
    if not K or len(K) < 290:
        return None
    p = compute(K, 288, sym=sym)
    if not p:
        return None
    sp = p["sig_pos"]
    side = "SHORT" if sp >= sig_thr else ("LONG" if sp <= -sig_thr else None)
    if side is None:
        return None
    entry = K[-1][4]
    vel6 = (entry - K[-7][4]) / K[-7][4] * 100.0
    fast = (vel6 >= fast_pct) if side == "SHORT" else (vel6 <= -fast_pct)
    d_last = _delta(K[-1][1], K[-1][2], K[-1][3], K[-1][4], K[-1][5])
    giro = (d_last < 0) if side == "SHORT" else (d_last > 0)
    if not (fast or giro):
        return None
    tgt = p["vwap"] if abs(p["vwap"] - entry) < abs(p["poc"] - entry) else p["poc"]
    return {"sym": sym, "side": side, "px": entry, "sig_pos": round(sp, 2), "vel6": round(vel6, 2),
            "fast": fast, "giro": giro, "vwap": p["vwap"], "poc": p["poc"],
            "target": tgt, "dist_target": round((tgt - entry) / entry * 100, 2)}


def alert_toward_center(sym, side, K=None, tf="5m"):
    """NOISE-GATE de alertas (usuario 2026-08-27 "solo alertas del lado externo, operar hacia el POC/VWAP").
    Pasa SOLO si: (1) el precio está en el LADO EXTERNO del perfil = fuera del value area o más allá de ±2.5σ del VWAP,
    en el lado correcto; Y (2) la alerta OPERA HACIA el centro = POC o VWAP en la dirección del trade.
    Filtra continuación-hacia-la-extensión y alertas en el medio del rango. Devuelve (ok, motivo). Fail-open sin data."""
    p = compute(K, 288, sym=sym) if K is not None else profile(sym, tf)
    if not p:
        return True, "sin perfil (fail-open)"
    px = p["px"]
    extern_hi = (px > p["vah"]) or (px >= p["up_band"])     # arriba y afuera del value area / >= +2.5σ
    extern_lo = (px < p["val"]) or (px <= p["dn_band"])     # abajo y afuera / <= -2.5σ
    if side == "LONG":
        toward = (p["poc"] > px) or (p["vwap"] > px)        # objetivo (POC/VWAP) ARRIBA
        ok = extern_lo and toward
    else:
        toward = (p["poc"] < px) or (p["vwap"] < px)        # objetivo ABAJO
        ok = extern_hi and toward
    tgt = p["vwap"] if abs(p["vwap"] - px) < abs(p["poc"] - px) else p["poc"]
    motivo = ("externo->centro (%+.1fσ, hacia %s %+.2f%%)" % (
        p["sig_pos"], "VWAP" if tgt == p["vwap"] else "POC", (tgt - px) / px * 100)
        if ok else "en rango/continuación (%+.1fσ, in_va=%s)" % (p["sig_pos"], p["in_va"]))
    return ok, motivo


def extreme_delta_signal(sym, K=None, tf="5m"):
    """EXTREMO + DELTA (usuario 2026-08-27): precio ARRIBA y afuera del value area (o >=+2.5σ) CON delta del nivel
    VENDEDOR -> alerta SHORT ; ABAJO y afuera (o <=-2.5σ) CON delta COMPRADOR -> LONG. Objetivo = POC/VWAP. None si no.
    Formato compatible con fade_signal para reusar el emisor de la alerta."""
    p = compute(K, 288, sym=sym) if K is not None else profile(sym, tf)
    if not p:
        return None
    px = p["px"]
    up = (px > p["vah"]) or (px >= p["up_band"])
    dn = (px < p["val"]) or (px <= p["dn_band"])
    if up and p["lvl_delta"] < 0:
        side = "SHORT"
    elif dn and p["lvl_delta"] > 0:
        side = "LONG"
    else:
        return None
    tgt = p["vwap"] if abs(p["vwap"] - px) < abs(p["poc"] - px) else p["poc"]
    return {"sym": sym, "side": side, "px": px, "sig_pos": round(p["sig_pos"], 2), "vel6": 0.0,
            "fast": False, "giro": False, "delta": True, "lvl_delta": p["lvl_delta"],
            "vwap": p["vwap"], "poc": p["poc"], "target": tgt, "dist_target": round((tgt - px) / px * 100, 2)}


# SÍMBOLOS SIEMPRE PRESENTES en el panel (pedido usuario 2026-09-03: TQQQ prioritario, que SIEMPRE aparezca aunque no
# sea buen momento — lo opera en horario Wall Street y quiere su serie/evolución completa). Env EXTREMES_ALWAYS agrega más.
import os as _os_ai
ALWAYS_INCLUDE = set(["TQQQUSDT"] + [x for x in _os_ai.getenv("EXTREMES_ALWAYS", "").split(",") if x])


def vp_extremes(syms, tf="5m"):
    """Estado del perfil para una lista de símbolos, SOLO los que están afuera del value area o en extremo |σ|>=2.5
    (+ los de ALWAYS_INCLUDE, que salen siempre). Para el panel. Devuelve lista ordenada por |σ| desc con señal si aplica."""
    out = []
    for s in syms:
        try:
            p = profile(s, tf)
        except Exception:
            p = None
        if not p:
            continue
        px = p["px"]
        up = (px > p["vah"]) or (px >= p["up_band"])
        dn = (px < p["val"]) or (px <= p["dn_band"])
        up_t = px > p["vwap"]; dn_t = px < p["vwap"]   # tendencia (trend proxy para conv, roster-wide)
        # SECUENCIA (pedido usuario 2026-09-02): NO etiquetar reversión mientras el extremo sigue CON la tendencia.
        # Acompañamos la tendencia (trend) y la REVERSIÓN (SHORT arriba / LONG abajo) SOLO se etiqueta cuando REALMENTE
        # tomó liquidez (más allá de ±2.5σ de la banda o surge climático de delta) Y el flujo se dio vuelta (delta
        # CONTRA + surge/CVD en contra). Elimina el short prematuro (ej HOOD que sube horas). delta_surge>=1.5 = gatillo
        # validado (bt_extremes_trigger 58% vs 46%). EXT_TRIG_SURGE regula (0 = vuelve al comportamiento viejo).
        import os as _os2
        _TRIG = float(_os2.getenv("EXT_TRIG_SURGE", "1.5"))
        surge = p.get("delta_surge", 0.0) or 0.0
        _rcvd = p.get("recent_cvd", 0) or 0
        _flow2 = p.get("flow2", 0) or 0
        _cs = p.get("cvd_sup", 0) or 0
        _f2s = p.get("flow2_sup", 0) or 0
        _hist = p.get("hist", 0.0) or 0.0
        _hprev = p.get("hist_prev", 0.0) or 0.0
        _rising = bool(p.get("hist_rising"))
        _alive = bool(p.get("sync_alive"))
        _rraw = p.get("recent_raw", 0.0) or 0.0
        _rprev = p.get("recent_prev", 0.0) or 0.0
        took_liq = bool((px >= p["up_band"]) or (px <= p["dn_band"]) or (surge >= _TRIG))
        # OK DIRECCIONAL v8 FIEL (pedido usuario 2026-09-02 "en TradingView NUNCA salió la flechita OK venta en SUI y
        # siguió subiendo; la flechita OK compra/venta determina la dirección con bastante precisión"): réplica EXACTA del
        # stack del indicador v8, sobre velas CERRADAS. 3 fixes de fidelidad: (a) SYNC NO gatea (syncGateOK=false en el v8,
        # solo colorea), (b) buyBuilding/sellBuilding (delta contrario subiendo bloquea el OK), (c) reversión = Hist CRUZA
        # cero contra la tendencia (revDn/revUp), no `hist<0` laxo. Omitido: Last_Divergence (1 pto de conv; queda +conservador).
        buy_building  = _rraw > _rprev
        sell_building = _rraw < _rprev
        cvd_buy  = (_rcvd > 0) and _rising                 # v8 cvdBuy  = recentCVD sube Y Hist sube
        cvd_sell = (_rcvd < 0) and (not _rising)           # v8 cvdSell = recentCVD baja Y Hist baja
        conv_buy  = int(_flow2 > 0) + int(cvd_buy)  + int(_cs > 0) + int(_f2s > 0) + int(up_t)   # up_t = tendencia (px>vwap)
        conv_sell = int(_flow2 < 0) + int(cvd_sell) + int(_cs < 0) + int(_f2s < 0) + int(dn_t)
        low_opp_sell = (_flow2 <= 0) and (_rcvd <= 0) and (_cs <= 0)    # v8 lowOppSell: poco flujo COMPRADOR en contra
        low_opp_buy  = (_flow2 >= 0) and (_rcvd >= 0) and (_cs >= 0)
        ok_sell = cvd_sell and conv_sell >= 2 and low_opp_sell and (conv_sell > conv_buy) and (not buy_building)   # = flechita OK VENTA del v8
        ok_buy  = cvd_buy  and conv_buy  >= 2 and low_opp_buy  and (conv_buy  > conv_sell) and (not sell_building)  # = flechita OK COMPRA del v8
        # GATE UNIFICADO (pedido usuario "meté todo en 1 panel, todo el roster"): incluir la fila si es EXTREMO VWAP
        # O tiene flechita OK direccional (así el panel muestra también los OK compra/venta de activos NO-extremos).
        if not (up or dn) and not (ok_buy or ok_sell) and s not in ALWAYS_INCLUDE:
            continue
        lado_ext = "SUP" if up else ("INF" if dn else ("SUP" if px >= p["vwap"] else "INF"))
        _okside = "LONG" if ok_buy else ("SHORT" if ok_sell else None)     # dirección de la flechita OK del v8
        sig = None; sig_mode = None
        trend = _okside or ("LONG" if up else "SHORT")
        # REVERSIÓN (v8 diamante) = OK CONTRARIA a la tendencia + tomó liquidez + el Hist YA cruzó (o está del lado contra).
        # up-extremo -> OK VENTA + Hist<0 = fade SHORT. Sin el cruce/lado del Hist NO es reversión (es debilitándose).
        if up and took_liq and ok_sell and _hist < 0:
            sig = "SHORT"; sig_mode = "reversion"
        elif dn and took_liq and ok_buy and _hist > 0:
            sig = "LONG"; sig_mode = "reversion"
        # CONTINUACIÓN = la flechita OK A FAVOR de la tendencia (v8 OK compra/venta). Sólo si el v8 la mostraría.
        cont_score = (conv_buy if ok_buy else (conv_sell if ok_sell else 0)) + (1 if _alive else 0)   # confluencia del lado OK + SYNC
        cont_ok = (sig is None) and bool(_okside)                             # flechita OK compra/venta a favor de la tendencia
        ok_dir = "compra" if ok_buy else ("venta" if ok_sell else None)       # la flechita del v8 (compra/venta)
        hp_mode = "LIQUIDEZ" if sig else ("TENDENCIA" if cont_ok else None)   # 2 modos: reversión al nivel / acompañar tendencia
        # DEBILITÁNDOSE (pedido usuario "cuándo cerrar el LONG"): ya NO hay flechita OK a favor + tomó liquidez + Hist dejó
        # de acompañar = la tendencia se agota -> ajustar/cerrar ANTES del flip. Etapa previa a la reversión.
        cont_weak = (sig is None) and (not bool(_okside)) and took_liq and (not _rising)
        # RÉGIMEN (ADX) -> MODO recomendado (pedido usuario 2026-09-03, validado bt_vwap_extreme_cont_vs_fade.py:
        # en TENDENCIA (ADX>=25) el extremo se ACOMPAÑA/continúa hasta ~6h; en CHOP (ADX<20) se FADEA al VWAP).
        _adx = p.get("adx", 0.0) or 0.0
        regimen = "tendencia" if _adx >= 25 else ("chop" if _adx < 20 else "mixto")
        if regimen == "tendencia":
            modo_reco = "TENDENCIA"; dir_reco = "LONG" if up else "SHORT"   # continuación del impulso estirado
        elif regimen == "chop":
            modo_reco = "FADE"; dir_reco = "SHORT" if up else "LONG"        # reversión al VWAP/POC
        else:
            modo_reco = "mixto"; dir_reco = None
        # TARGET >=1% con ALTA PROB (validado bt_target_1pct_cluster.py 2026-09-02): el +1% fijo se toca 65% (el mejor target
        # >=1%); apuntar a un nivel/cluster lejano baja la prob a 18-22%. => target = mitad al centro pero con PISO de 1%
        # (si la mitad ya es >=1% se usa la mitad; si no, se estira a 1%). El cluster-reach se usa para RANKEAR activos, no para alejar el target.
        import math as _mm
        _center = p["vwap"] if abs(p["vwap"] - px) < abs(p["poc"] - px) else p["poc"]
        _mid = px + (_center - px) * 0.5
        _minmv = px * 0.01
        _dir = 1 if _center > px else -1
        tgt = _mid if abs(_mid - px) >= _minmv else (px + _minmv * _dir)
        _sg = p.get("reach_sig", 0.0)   # ATR*sqrt(N) calibrado (NO el sigma del VWAP)
        target_reach = round(2.0 * (1.0 - 1.0 / (1.0 + _mm.exp(-1.702 * abs(tgt - px) / _sg))) * 100) if _sg > 0 else 0
        # __OBJETIVO_H3_2026_10_18__ El `tgt` de arriba esta MAL, y esta medido fuera de
        # muestra (41 simbolos, n=21.857, verificador sellado; ver RESULTADOS_OBJETIVO.md):
        #   · el piso de 1 % lo manda MAS ALLA del iman que lo justifica en el 68,9 % de
        #     las lecturas, o sea a un precio donde no hay ningun nivel;
        #   · se toca solo el 26,1 % a 3 h — SPY 0,9 %, QQQ 4,6 %, BTC 13,0 % — que es
        #     EXACTAMENTE la tasa base de su celda (distancia x volatilidad): no informa
        #     nada que la distancia y la volatilidad no digan ya.
        # El "+1 % se toca 65 %" del comentario de arriba NO se reprodujo por ningun
        # camino: ni a 4 h (33,6 %), ni en su propio universo de alts, ni condicionando
        # al extremo que lo validaba — a >=2,5 sigma BAJA a 27,0 % (la conviccion
        # anti-predice, igual que en el control corregido de niveles). El piso de 1 %
        # se valido a >=2,5 sigma con 28 alts volatiles y hoy se aplica a TODAS las
        # filas (mediana 1,13 sigma, cero a >=2,5): se generalizo fuera de su dominio.
        #
        # `obj2` es la regla H3: a 1 ATR(1h) hacia el iman y RECORTADO para no pasarse
        # nunca de el. Medida: toque 66,0 %, calibracion 1,2 pp agregada y +-3,5 pp por
        # simbolo, mejora en 41/41 simbolos.
        #
        # `tgt`/`target` NO se tocan a proposito: el brazo POCFADE los usa como
        # take-profit y lleva 336 operaciones paper con esa regla. Y no hay motivo de
        # rentabilidad para cambiarlo: re-simulando ESA misma muestra con TP de 0,15 % a
        # 2,0 %, TODAS las distancias dan negativo (la mejor, 0,30 %, da -0,257 %/op)
        # porque el stop salta el 26,2 % de las veces sin importar donde este el
        # objetivo. Mover el objetivo no vuelve rentable al intradia.
        _a1 = p.get("atr1h_pct") or 0.0
        obj2 = obj2_dist = obj2_kind = None
        if _a1 > 0:
            _dc = abs(_center - px) / px * 100.0
            _d2 = min(_a1, _dc)
            if _d2 > 1e-9:
                obj2 = round(px * (1 + _dir * _d2 / 100.0), 6)
                obj2_dist = round(_dir * _d2, 3)
                obj2_kind = ("VWAP" if _center == p["vwap"] else "POC") if _d2 >= _dc - 1e-9 else "ATR"
        # TIER por extensión (usuario 2026-08-28 "busco >±2σ = mejores operaciones"): EXTREMO≥2.5, FUERTE≥2.0, leve=fuera de VA
        _asig = abs(p["sig_pos"])
        tier = "EXTREMO" if _asig >= SIGMA_K else ("FUERTE" if _asig >= 2.0 else "leve")
        # FLUJO (indicadores del Pine) + CONV: cuántos confirman la reversión al centro.
        # SUP (reversión SHORT) confirma con flujo VENDEDOR (-1) ; INF (LONG) con flujo COMPRADOR (+1).
        _want = -1 if lado_ext == "SUP" else 1
        _ld = 1 if p["lvl_delta"] > 0 else (-1 if p["lvl_delta"] < 0 else 0)
        _fl = {"flow2": p.get("flow2", 0), "cvd": p.get("recent_cvd", 0), "flow2_sup": p.get("flow2_sup", 0),
               "cvd_sup": p.get("cvd_sup", 0), "delta": _ld}
        conv = sum(1 for v in _fl.values() if v == _want)
        # CONFLUENCIA SWEEP-FADE (VALIDADO grado A doble-grab 82%/17% OOS): si además NY barrió el/los extremo(s) de
        # Londres y reclamó del MISMO lado que la reversión (INF→LONG, SUP→SHORT), es la señal más fuerte. Sólo lo
        # consultamos para los símbolos que YA están en extremo (no agrega fetch al resto). fail-soft.
        sf_grade = None; sf_mid_dist = None; sf_opp_dist = None
        if up or dn:                                      # PERF: sweep_fade (red) SOLO para extremos, no las filas OK-solo
            try:
                import session_sweep as _SS
                _sf = _SS.sweep_fade_now(s)
                if _sf and ((_sf["side"] == "LONG") == (lado_ext == "INF")):
                    sf_grade = _sf["grade"]                   # "A" (doble grab) / "B" (un extremo)
                    sf_mid_dist = _sf.get("dist_mid")         # % al MID del rango de Londres (target ~80%)
                    sf_opp_dist = _sf.get("dist_opp")         # % al extremo OPUESTO de Londres
            except Exception:
                pass
        out.append({
            "symbol": s, "px": px, "sig_pos": p["sig_pos"], "sig_abs": round(_asig, 2), "tier": tier,
            "prime": _asig >= 2.0,                       # zona de mejores operaciones (>=2σ)
            "lado_ext": lado_ext,
            "beyond_band": (px >= p["up_band"] or px <= p["dn_band"]),
            "poc": p["poc"], "poc_dist": p["poc_dist"], "vwap": p["vwap"],
            "vah": p["vah"], "val": p["val"], "lvl_delta": p["lvl_delta"], "net_delta": p["net_delta"],
            "delta_surge": p.get("delta_surge", 0),      # gatillo: |delta reciente|/prom ; >=1.5 + delta contra = reversion (58%)
            "vol_below": p.get("vol_below"),             # % de volumen 24h POR DEBAJO del precio (liquidez abajo vs arriba)
            "cluster_reach": p.get("cluster_reach", 0),  # niveles de alta prob apilados (>=1.6 = zona iman fuerte, E+0.28)
            "cluster_n": p.get("cluster_n", 1),          # cuantos niveles apilados en la banda 0.5% (2+ = edge, 62% vs suelto 22-45%)
            "reach_poc": p.get("reach_poc", 0), "reach_vwap": p.get("reach_vwap", 0),
            "delta_side": "vendedor" if p["lvl_delta"] < 0 else ("comprador" if p["lvl_delta"] > 0 else "neutro"),
            "signal": sig, "sig_mode": sig_mode, "trend": trend, "took_liq": took_liq,
            "cont_score": cont_score, "cont_ok": cont_ok, "cont_weak": cont_weak, "best_cont": False,
            "ok_dir": ok_dir, "hp_mode": hp_mode, "is_ext": bool(up or dn),
            "adx": round(_adx, 1), "regimen": regimen, "modo_reco": modo_reco, "dir_reco": dir_reco,
            "hist": round(_hist, 1), "hist_rising": p.get("hist_rising"),
            "target": tgt, "target_kind": ("mitad->" if abs(_mid - px) >= _minmv else "1%->") + ("VWAP" if _center == p["vwap"] else "POC"),
            "target_full": round(_center, 6), "dist_target": round((tgt - px) / px * 100, 2), "target_reach": target_reach,
            "obj2": obj2, "obj2_dist": obj2_dist, "obj2_kind": obj2_kind, "atr1h_pct": p.get("atr1h_pct"),
            # FLUJO (Pine): -1 vendedor / +1 comprador / 0 ; conv = cuántos confirman la reversión ; sync = régimen vivo
            "flow2": _fl["flow2"], "cvd": _fl["cvd"], "flow2_sup": _fl["flow2_sup"], "cvd_sup": _fl["cvd_sup"],
            "conv": conv, "flow_want": _want, "sync": p.get("sync_alive"),
            "sweep_fade": sf_grade,                       # confluencia barrido de sesión: "A"/"B"/None
            "sf_mid_dist": sf_mid_dist, "sf_opp_dist": sf_opp_dist,   # targets del RANGO de Londres (mid ~80% / opuesto)
            "premium_conf": sf_grade == "A",              # extremo VWAP + sweep-fade grado A = lo más fuerte
        })
    # MEJOR CONTINUACIÓN (pedido usuario): entre los que siguen la tendencia (cont_ok), la #1 por score de flujo alineado
    # = la mejor para operar A FAVOR hasta que gire. Espejo directo del "OK direccional #1" del v8.
    _conts = [r for r in out if r.get("cont_ok")]
    if _conts:
        _bc = max(_conts, key=lambda r: (r["cont_score"], abs(r["sig_pos"])))
        _bc["best_cont"] = True
    out.sort(key=lambda r: -abs(r["sig_pos"]))
    return out


def hi_prob_scan(symbols):
    """ENTRADAS DE ALTA PROBABILIDAD para TODO el roster (pedido usuario 2026-09-02: "medí todo para entrar con alta
    probabilidad a BUSCAR LA LIQUIDEZ o ACOMPAÑAR el volumen de la tendencia"; el panel EXTREMOS perdía OK compra/venta
    en activos NO-extremos). Combina sobre velas CERRADAS: (1) flechita OK compra/venta del v8, (2) SOTT multi-TF en
    extremo (ubicación, incl 1h/1d — "el SOTT 1h en compra habilitó la subida"), (3) fondo = CVD del TF superior 1h
    (verde/rojo), (4) VOLUMEN importante, (5) delta (dentro del OK), (6) destino = liquidez/nivel. Clasifica 2 modos:
    LIQUIDEZ (reversión al nivel) / TENDENCIA (acompañar). Rankea por nº de confluencias. NO gateado a extremo VWAP."""
    try:
        from direction_engine import sott_zone as _SZ
    except Exception:
        _SZ = None
    out = []
    for s in symbols:
        try:
            p = profile(s, "5m")
        except Exception:
            p = None
        if not p:
            continue
        px = p["px"]
        _rcvd = p.get("recent_cvd", 0) or 0; _flow2 = p.get("flow2", 0) or 0
        _cs = p.get("cvd_sup", 0) or 0; _f2s = p.get("flow2_sup", 0) or 0
        _rising = bool(p.get("hist_rising")); _rraw = p.get("recent_raw", 0) or 0; _rprev = p.get("recent_prev", 0) or 0
        up_t = px > p["vwap"]; dn_t = px < p["vwap"]
        cvd_buy = (_rcvd > 0) and _rising; cvd_sell = (_rcvd < 0) and (not _rising)
        conv_buy = int(_flow2 > 0) + int(cvd_buy) + int(_cs > 0) + int(_f2s > 0) + int(up_t)
        conv_sell = int(_flow2 < 0) + int(cvd_sell) + int(_cs < 0) + int(_f2s < 0) + int(dn_t)
        low_b = (_flow2 >= 0) and (_rcvd >= 0) and (_cs >= 0); low_s = (_flow2 <= 0) and (_rcvd <= 0) and (_cs <= 0)
        ok_buy = cvd_buy and conv_buy >= 2 and low_b and conv_buy > conv_sell and not (_rraw < _rprev)
        ok_sell = cvd_sell and conv_sell >= 2 and low_s and conv_sell > conv_buy and not (_rraw > _rprev)
        if not (ok_buy or ok_sell):
            continue
        side = "LONG" if ok_buy else "SHORT"
        mtf = _SZ.sott_mtf_confluence(s) if _SZ else None          # SOTT multi-TF (ubicación, incl 1h/1d)
        sott_ok = bool(mtf and mtf["side"] == side)
        htf_ok = (_cs > 0) if ok_buy else (_cs < 0)                # fondo = CVD 1h a favor
        vr = p.get("vol_ratio", 1.0) or 1.0
        vol_ok = vr >= 1.5                                          # volumen importante
        up_ext = (px > p["vah"]) or (px >= p["up_band"]); dn_ext = (px < p["val"]) or (px <= p["dn_band"])
        is_rev = (ok_sell and up_ext) or (ok_buy and dn_ext)       # reversión = OK contra el extremo
        mode = "LIQUIDEZ" if is_rev else "TENDENCIA"
        center = p["vwap"] if abs(p["vwap"] - px) < abs(p["poc"] - px) else p["poc"]
        clr = p.get("cluster_reach", 0) or 0
        dest_ok = (clr >= 1.0) or is_rev                           # destino: nivel apilado o el imán POC/VWAP (reversión)
        confl = 1 + int(sott_ok) + int(htf_ok) + int(vol_ok) + int(dest_ok)     # 1..5 (el OK ya cuenta)
        tier = "ALTA" if confl >= 4 else ("MEDIA" if confl == 3 else "baja")
        out.append({"symbol": s, "side": side, "mode": mode, "ok": ("compra" if ok_buy else "venta"),
                    "confl": confl, "tier": tier, "sott": (mtf["grade"] if sott_ok else None), "htf_ok": htf_ok,
                    "vol_ratio": round(vr, 2), "vol_ok": vol_ok, "dest_ok": dest_ok, "cluster_reach": clr,
                    "px": px, "target": round(center, 6), "dist_target": round((center - px) / px * 100, 2),
                    "conv": (conv_buy if ok_buy else conv_sell), "hist": round(p.get("hist", 0.0), 1)})
    out.sort(key=lambda r: (-r["confl"], -(r["cluster_reach"] or 0)))
    return out


def verdict(sym, tf="5m"):
    """Veredicto completo on-demand (dashboard/CLI). Deriva un sesgo por POC+VWAP+delta."""
    p = profile(sym, tf)
    if not p:
        return {"sym": sym, "tag": "SIN DATA", "detail": "sin klines"}
    # sesgo: fuera de VA -> hacia POC ; extremo VWAP -> fade ; delta del nivel confirma
    bias = None
    if abs(p["sig_pos"]) >= SIGMA_K:
        bias = "SHORT" if p["sig_pos"] > 0 else "LONG"     # estirado -> fade
        why = "VWAP %+.1fsigma (estirado, fade)" % p["sig_pos"]
    elif not p["in_va"]:
        bias = "LONG" if p["poc_dist"] > 0 else "SHORT"    # fuera de VA -> pull al POC
        why = "fuera de VA -> imán POC %+.2f%%" % p["poc_dist"]
    else:
        why = "dentro de value area (balance)"
    dlt = "comprador" if p["lvl_delta"] > 0 else ("vendedor" if p["lvl_delta"] < 0 else "neutro")
    tag, _ = line(sym, bias, tf)
    return {"sym": sym, "tag": tag, "bias": bias, "why": why, "delta_nivel": dlt, **p}


if __name__ == "__main__":
    import sys
    for s in (sys.argv[1:] or ["TAOUSDT", "TQQQUSDT", "BTCUSDT"]):
        v = verdict(s)
        if v.get("tag") == "SIN DATA":
            print("%-10s sin data" % s); continue
        print("%-10s px=%.6g  POC=%.6g(%+.2f%%)  VA[%.6g..%.6g]%s  VWAP%+.1fs  bias=%s  delta_nivel=%s  | %s" % (
            s, v["px"], v["poc"], v["poc_dist"], v["val"], v["vah"],
            "" if v["in_va"] else " FUERA", v["sig_pos"], v["bias"] or "-", v["delta_nivel"], v["why"]))
