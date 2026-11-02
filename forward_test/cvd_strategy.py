r"""
cvd_strategy.py -- Generador de senal 'cvd_div_tlbreak' (fiel a los Pine del usuario, en Python).

Mecanismo (H9, order flow -- NO testeado por el research OHLCV):
  1) CVD (delta de volumen comprador/vendedor) acumulado -> divergencia contra el precio
     (fractales n=2 + filtro de tendencia EMA50): bull div (precio LL, CVD HL) / bear div (precio HH, CVD LH).
  2) La divergencia arma un SESGO. La "red pill" = ruptura de linea de tendencia (LuxAlgo, ATR slope)
     en la direccion del sesgo -> ENTRADA.
  3) Objetivo 1-2%; stop ESTRUCTURAL bajo/sobre el pivote de la divergencia (capado).

Todo sobre velas CERRADAS (la ultima vela formandose se descarta antes de llamar scan) => no repinta.
Devuelve una senal SOLO si la entrada dispara en la ultima vela confirmada.
"""


def _ema(x, n):
    k = 2.0 / (n + 1.0)
    out = [x[0]]
    for i in range(1, len(x)):
        out.append(x[i] * k + out[-1] * (1 - k))
    return out


def _rma(x, n):
    out = [x[0]] * len(x)
    if len(x) >= n:
        seed = sum(x[:n]) / n
        out[n - 1] = seed
        for i in range(n, len(x)):
            out[i] = (out[i - 1] * (n - 1) + x[i]) / n
        for i in range(n - 1):
            out[i] = seed
    return out


def _atr(h, l, c, n):
    tr = [h[0] - l[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return _rma(tr, n)


def _rolling_sum(x, n):
    out = [0.0] * len(x)
    s = 0.0
    from collections import deque
    q = deque()
    for i in range(len(x)):
        q.append(x[i])
        s += x[i]
        if len(q) > n:
            s -= q.popleft()
        out[i] = s
    return out


def _is_ph(h, idx, left, right):
    if idx - left < 0 or idx + right >= len(h):
        return False
    piv = h[idx]
    for j in range(idx - left, idx + right + 1):
        if j != idx and h[j] >= piv:
            return False
    return True


def _is_pl(l, idx, left, right):
    if idx - left < 0 or idx + right >= len(l):
        return False
    piv = l[idx]
    for j in range(idx - left, idx + right + 1):
        if j != idx and l[j] <= piv:
            return False
    return True


def _sma(x, n):
    out = [0.0] * len(x)
    s = 0.0
    from collections import deque
    q = deque()
    for i in range(len(x)):
        q.append(x[i]); s += x[i]
        if len(q) > n:
            s -= q.popleft()
        out[i] = s / min(len(q), n)
    return out


def _rsi(c, n):
    N = len(c)
    up = [0.0] * N
    dn = [0.0] * N
    for i in range(1, N):
        ch = c[i] - c[i - 1]
        up[i] = ch if ch > 0 else 0.0
        dn[i] = -ch if ch < 0 else 0.0
    ru = _rma(up, n)
    rd = _rma(dn, n)
    out = [50.0] * N
    for i in range(N):
        out[i] = (100.0 - 100.0 / (1.0 + ru[i] / rd[i])) if rd[i] > 0 else (100.0 if ru[i] > 0 else 50.0)
    return out


# Osciladores de divergencia CENTRADOS EN 0 (>0 en techos, <0 en pisos): mismo detector para todos.
DIV_OSCS = ("CVD", "MACD", "RSI", "AO")


def _osc(kl, which, period=21):
    N = len(kl)
    h = [r[2] for r in kl]; l = [r[3] for r in kl]; c = [r[4] for r in kl]; v = [r[5] for r in kl]
    if which == "MACD":
        ef = _ema(c, 12); es = _ema(c, 26)
        macd = [ef[i] - es[i] for i in range(N)]
        sig = _ema(macd, 9)
        return [macd[i] - sig[i] for i in range(N)]
    if which == "RSI":
        r = _rsi(c, 14)
        return [r[i] - 50.0 for i in range(N)]
    if which == "AO":
        hl2 = [(h[i] + l[i]) / 2.0 for i in range(N)]
        a5 = _sma(hl2, 5); a34 = _sma(hl2, 34)
        return [a5[i] - a34[i] for i in range(N)]
    # CVD (default)
    delta = [v[i] * (((c[i] - l[i]) - (h[i] - c[i])) / (h[i] - l[i])) if h[i] > l[i] else 0.0 for i in range(N)]
    return _rolling_sum(delta, period)


def analyze(kl, n=2, period=21, ema_len=50, tl_len=14, tl_mult=1.0,
            div_win=30, bias_expiry=30, sl_cap=1.0, sl_floor=0.3, sl_buf=0.1,
            use_trend_filter=True, brk_atr=0.25, brk_confirm=True, div_osc="CVD"):
    # brk_atr: margen (fraccion de ATR de ESTE activo) que el cierre debe SUPERAR la trendline
    #          para contar como quiebre real (auto-adaptado por volatilidad del activo).
    # brk_confirm: exige que la vela del quiebre cierre en la direccion del quiebre (body confirm).
    """kl: velas CERRADAS [ms,o,h,l,c,v] (oldest->newest).
    Devuelve {'divergence': <div RECIEN formada en la ultima vela, o None>,
              'entry':      <entrada divergencia+ruptura en la ultima vela, o None>}."""
    N = len(kl)
    need = max(period, ema_len, tl_len * 2 + 2, 2 * n + 2) + 5
    if N < need:
        return {"divergence": None, "entry": None}
    ms = [r[0] for r in kl]
    o = [r[1] for r in kl]
    h = [r[2] for r in kl]
    l = [r[3] for r in kl]
    c = [r[4] for r in kl]
    v = [r[5] for r in kl]

    ema50 = _ema(c, ema_len)
    atr_tl = _atr(h, l, c, tl_len)
    hist = _osc(kl, div_osc, period)

    # --- pase unico: divergencias (fractales n) + trendline (len) + combinacion ---
    up_last = up_prev = None   # (price, hist, bar)
    dn_last = dn_prev = None
    bull_bias_bar = bear_bias_bar = -10 ** 9
    bull_pivot = bear_pivot = None

    upper = lower = 0.0
    slope_ph = slope_pl = 0.0
    upos = dnos = 0
    upos_p = dnos_p = 0

    entry = None
    fresh_div = None
    prev_bull = prev_bear = False
    for t in range(N):
        # ---- Divergencia (fractal en t-n). SIN filtro de tendencia por defecto:
        #      "solo 2 condiciones" (divergencia + ruptura) -> mas operaciones (pedido usuario). ----
        if t - n >= 0:
            up_ok = (c[t - n] > ema50[t]) if use_trend_filter else True
            dn_ok = (c[t - n] < ema50[t]) if use_trend_filter else True
            if _is_ph(h, t - n, n, n) and up_ok:
                up_prev = up_last
                up_last = (h[t - n], hist[t - n], t - n)
            if _is_pl(l, t - n, n, n) and dn_ok:
                dn_prev = dn_last
                dn_last = (l[t - n], hist[t - n], t - n)

        bear = False
        if up_last and up_prev:
            HLp, HLh, HLb = up_last
            HPp, HPh, HPb = up_prev
            if HLh > 0 and HPh > 0 and (HLb + div_win) > t and (HLb - HPb) < div_win:
                bear = (HLp > HPp) and (HLh < HPh)
        bull = False
        if dn_last and dn_prev:
            LLp, LLh, LLb = dn_last
            LPp, LPh, LPb = dn_prev
            if LLh < 0 and LPh < 0 and (LLb + div_win) > t and (LLb - LPb) < div_win:
                bull = (LLp < LPp) and (LLh > LPh)
        if bull:
            bull_bias_bar = t
            bull_pivot = dn_last[0]
        if bear:
            bear_bias_bar = t
            bear_pivot = up_last[0]
        # ---- Divergencia RECIEN formada (flanco de subida): PRE-ALERTA antes del gatillo ----
        if bull and not prev_bull:
            fresh_div = {"bar_ms": ms[t], "bar_idx": t, "side": "LONG", "kind": "alcista", "price": c[t]}
        if bear and not prev_bear:
            fresh_div = {"bar_ms": ms[t], "bar_idx": t, "side": "SHORT", "kind": "bajista", "price": c[t]}
        prev_bull, prev_bear = bull, bear

        # ---- Trendline LuxAlgo (pivote en t-tl_len) ----
        ph = _is_ph(h, t - tl_len, tl_len, tl_len) if t - tl_len >= 0 else False
        pl = _is_pl(l, t - tl_len, tl_len, tl_len) if t - tl_len >= 0 else False
        ph_val = h[t - tl_len] if ph else None
        pl_val = l[t - tl_len] if pl else None
        slope = atr_tl[t] / tl_len * tl_mult
        slope_ph = slope if ph else slope_ph
        slope_pl = slope if pl else slope_pl
        upper = ph_val if ph else (upper - slope_ph)
        lower = pl_val if pl else (lower + slope_pl)
        upos_p, dnos_p = upos, dnos
        # Quiebre REAL: el cierre debe superar la trendline por >= brk_atr*ATR del activo,
        # y (si brk_confirm) la vela cierra en la direccion del quiebre.
        up_thr = (upper - slope_ph * tl_len) + brk_atr * atr_tl[t]
        dn_thr = (lower + slope_pl * tl_len) - brk_atr * atr_tl[t]
        up_body = (c[t] >= o[t]) if brk_confirm else True
        dn_body = (c[t] <= o[t]) if brk_confirm else True
        upos = 0 if ph else (1 if (c[t] > up_thr and up_body) else upos)
        dnos = 0 if pl else (1 if (c[t] < dn_thr and dn_body) else dnos)
        brk_up = upos > upos_p
        brk_dn = dnos > dnos_p

        # ---- Combinacion: ruptura en direccion del sesgo de divergencia ----
        # ENTRADA EN EL NIVEL DEL QUIEBRE (2026-07-29 pedido usuario GOOGL: entrar donde se ACTIVA la red
        # pill = la trendline rota (up_thr/dn_thr), NO en el cierre de la vela que hace overshoot y peor
        # precio). Calza con orden LIMITE (fill en el nivel) y con el hallazgo MC (ganador toma poco calor).
        if brk_up and (t - bull_bias_bar) <= bias_expiry and bull_pivot is not None:
            entry = {"bar_ms": ms[t], "bar_idx": t, "side": "LONG", "entry": up_thr, "pivot": bull_pivot}
        elif brk_dn and (t - bear_bias_bar) <= bias_expiry and bear_pivot is not None:
            entry = {"bar_ms": ms[t], "bar_idx": t, "side": "SHORT", "entry": dn_thr, "pivot": bear_pivot}

    # Entrada: solo si dispara en la ULTIMA vela confirmada
    entry_out = None
    if entry and entry["bar_idx"] == N - 1:
        px = entry["entry"]
        piv = entry["pivot"]
        if entry["side"] == "LONG":
            sl_pct = (px - piv) / px * 100.0 + sl_buf
        else:
            sl_pct = (piv - px) / px * 100.0 + sl_buf
        if sl_pct <= 0:
            sl_pct = sl_floor
        sl_pct = max(sl_floor, min(sl_cap, sl_pct))
        entry_out = {"bar_ms": entry["bar_ms"], "side": entry["side"], "entry": px, "sl_pct": round(sl_pct, 4)}
        # MAGNITUD/Z-SCORE de la divergencia (2026-07-25): fuerza del oscilador = gap del hist entre los 2
        # pivotes de la divergencia, normalizado por el desvio del hist. Feature NUEVA para el loop MC:
        # mide QUE TAN FUERTE es la divergencia (no solo que exista). Se guarda en zones.div_z.
        _hv = [x for x in hist if x == x]
        _m = (sum(_hv) / len(_hv)) if _hv else 0.0
        _sd = ((sum((x - _m) ** 2 for x in _hv) / len(_hv)) ** 0.5) if _hv else 0.0
        div_z = None
        if _sd > 0:
            if entry["side"] == "LONG" and dn_last and dn_prev:
                div_z = round((dn_last[1] - dn_prev[1]) / _sd, 3)
            elif entry["side"] == "SHORT" and up_last and up_prev:
                div_z = round((up_prev[1] - up_last[1]) / _sd, 3)
        entry_out["div_z"] = div_z
    # Divergencia recien formada en la ULTIMA vela (pre-alerta antes del gatillo)
    div_out = fresh_div if (fresh_div and fresh_div["bar_idx"] == N - 1) else None
    # ESTADO ACTUAL (para el semaforo / tarjetas por activo)
    L = N - 1
    bias = None
    pivot = None
    bias_ago = None
    if (L - bull_bias_bar) <= bias_expiry and bull_pivot is not None:
        bias, pivot, bias_ago = "LONG", bull_pivot, L - bull_bias_bar
    if (L - bear_bias_bar) <= bias_expiry and bear_pivot is not None and (bias_ago is None or (L - bear_bias_bar) < bias_ago):
        bias, pivot, bias_ago = "SHORT", bear_pivot, L - bear_bias_bar
    stt = {"price": c[L], "cvd": hist[L], "bias": bias, "pivot": pivot, "bias_ago": bias_ago,
           "brk_up": upper - slope_ph * tl_len + brk_atr * atr_tl[L],
           "brk_dn": lower + slope_pl * tl_len - brk_atr * atr_tl[L]}
    return {"divergence": div_out, "entry": entry_out, "state": stt}


def eval_brk_atr(kl, cands, fwd=6, brk_confirm=True, tl_len=14, tl_mult=1.0):
    """Walk-forward institucional: sobre la ventana RECIENTE, para cada candidato brk_atr mide
    cuanto CONTINUA el precio 'fwd' velas despues de cada quiebre de trendline (en su direccion).
    La trendline (upper/lower/slope) NO depende de brk_atr -> se calcula 1 vez; cada candidato solo
    cambia el umbral. Devuelve {cand: {'n': nquiebres, 'avg_fwd': %continuacion promedio}}."""
    N = len(kl)
    if N < tl_len * 2 + fwd + 5:
        return {}
    o = [r[1] for r in kl]; h = [r[2] for r in kl]; l = [r[3] for r in kl]; c = [r[4] for r in kl]
    atr_tl = _atr(h, l, c, tl_len)
    upper = lower = 0.0
    slope_ph = slope_pl = 0.0
    UP = [0.0] * N; LO = [0.0] * N; PH = [False] * N; PL = [False] * N
    for t in range(N):
        ph = _is_ph(h, t - tl_len, tl_len, tl_len) if t - tl_len >= 0 else False
        pl = _is_pl(l, t - tl_len, tl_len, tl_len) if t - tl_len >= 0 else False
        slope = atr_tl[t] / tl_len * tl_mult
        slope_ph = slope if ph else slope_ph
        slope_pl = slope if pl else slope_pl
        upper = (h[t - tl_len] if ph else (upper - slope_ph))
        lower = (l[t - tl_len] if pl else (lower + slope_pl))
        UP[t] = upper - slope_ph * tl_len
        LO[t] = lower + slope_pl * tl_len
        PH[t] = ph; PL[t] = pl
    out = {}
    for cand in cands:
        upos = dnos = 0
        rets = []
        for t in range(N):
            up_body = (c[t] >= o[t]) if brk_confirm else True
            dn_body = (c[t] <= o[t]) if brk_confirm else True
            up_p, dn_p = upos, dnos
            upos = 0 if PH[t] else (1 if (c[t] > UP[t] + cand * atr_tl[t] and up_body) else upos)
            dnos = 0 if PL[t] else (1 if (c[t] < LO[t] - cand * atr_tl[t] and dn_body) else dnos)
            if t + fwd < N and c[t] > 0:
                if upos > up_p:
                    rets.append((c[t + fwd] - c[t]) / c[t] * 100.0)
                elif dnos > dn_p:
                    rets.append((c[t] - c[t + fwd]) / c[t] * 100.0)
        n = len(rets)
        out[cand] = {"n": n, "avg_fwd": (sum(rets) / n) if n else 0.0}
    return out


def _div_fresh(kl, osc, n=2, div_win=30, use_trend_filter=True):
    """Divergencias RECIEN formadas sobre el oscilador dado -> lista (bar, dir) dir=+1 bull / -1 bear.
    use_trend_filter=True replica el Pine del usuario: el fractal solo cuenta si close[n] esta del lado
    correcto de la EMA50 (pivot high sobre la EMA para bajista / pivot low bajo la EMA para alcista)."""
    N = len(kl); h = [r[2] for r in kl]; l = [r[3] for r in kl]; c = [r[4] for r in kl]
    ema = _ema(c, 50) if use_trend_filter else None
    up_last = up_prev = dn_last = dn_prev = None
    prev_bull = prev_bear = False
    ev = []
    for t in range(N):
        if t - n >= 0:
            if _is_ph(h, t - n, n, n) and (not use_trend_filter or c[t - n] > ema[t]):
                up_prev = up_last; up_last = (h[t - n], osc[t - n], t - n)
            if _is_pl(l, t - n, n, n) and (not use_trend_filter or c[t - n] < ema[t]):
                dn_prev = dn_last; dn_last = (l[t - n], osc[t - n], t - n)
        bear = False
        if up_last and up_prev:
            HLp, HLh, HLb = up_last; HPp, HPh, HPb = up_prev
            if HLh > 0 and HPh > 0 and (HLb + div_win) > t and (HLb - HPb) < div_win:
                bear = (HLp > HPp) and (HLh < HPh)
        bull = False
        if dn_last and dn_prev:
            LLp, LLh, LLb = dn_last; LPp, LPh, LPb = dn_prev
            if LLh < 0 and LPh < 0 and (LLb + div_win) > t and (LLb - LPb) < div_win:
                bull = (LLp < LPp) and (LLh > LPh)
        if bull and not prev_bull:
            ev.append((t, 1))
        if bear and not prev_bear:
            ev.append((t, -1))
        prev_bull, prev_bear = bull, bear
    return ev


def eval_div_osc(kl, oscs=DIV_OSCS, fwd=36, target_pct=1.0, n=2, div_win=30, period=21):
    """TESTIGO institucional: para cada oscilador mide, sobre la ventana RECIENTE, que fraccion de sus
    divergencias fue seguida por un movimiento de >= target_pct% EN LA DIRECCION de la divergencia
    (max excursion favorable dentro de fwd velas). Devuelve {osc:{'n':,'hit':fraccion>=1%,'avg_mfe':}}.
    'hit' = validacion: cuan seguido esa divergencia 'entrega' el 1% ultimamente."""
    N = len(kl)
    if N < 60:
        return {}
    h = [r[2] for r in kl]; l = [r[3] for r in kl]; c = [r[4] for r in kl]
    out = {}
    for w in oscs:
        osc = _osc(kl, w, period)
        ev = _div_fresh(kl, osc, n, div_win)
        nn = 0; hits = 0; mfes = 0.0
        for (t, d) in ev:
            if t + 1 >= N or c[t] <= 0:
                continue
            end = min(t + fwd, N - 1)
            if d == 1:
                mfe = max((h[k] - c[t]) / c[t] * 100.0 for k in range(t + 1, end + 1))
            else:
                mfe = max((c[t] - l[k]) / c[t] * 100.0 for k in range(t + 1, end + 1))
            nn += 1; mfes += mfe
            if mfe >= target_pct:
                hits += 1
        out[w] = {"n": nn, "hit": (hits / nn) if nn else 0.0, "avg_mfe": (mfes / nn) if nn else 0.0}
    return out


def all_signals(kl, div_osc="CVD", brk_atr=0.25, brk_confirm=True, n=2, period=21,
                ema_len=50, tl_len=14, tl_mult=1.0, div_win=30, bias_expiry=30,
                sl_cap=1.0, sl_floor=0.3, sl_buf=0.1, use_trend_filter=True):
    """Devuelve TODAS las entradas red pill (divergencia+ruptura) sobre el array -> lista de
    {bar, side, entry, sl_pct}. Sin lookahead: cada entrada usa solo datos <= su barra.
    Para backtest walk-forward (replay completo)."""
    N = len(kl)
    if N < max(period, ema_len, tl_len * 2 + 2, 2 * n + 2) + 5:
        return []
    o = [r[1] for r in kl]; h = [r[2] for r in kl]; l = [r[3] for r in kl]; c = [r[4] for r in kl]
    ema50 = _ema(c, ema_len); atr_tl = _atr(h, l, c, tl_len); hist = _osc(kl, div_osc, period)
    up_last = up_prev = dn_last = dn_prev = None
    bull_bias_bar = bear_bias_bar = -10 ** 9
    bull_pivot = bear_pivot = None
    upper = lower = 0.0; slope_ph = slope_pl = 0.0; upos = dnos = 0
    sigs = []
    for t in range(N):
        if t - n >= 0:
            if _is_ph(h, t - n, n, n) and (not use_trend_filter or c[t - n] > ema50[t]):
                up_prev = up_last; up_last = (h[t - n], hist[t - n], t - n)
            if _is_pl(l, t - n, n, n) and (not use_trend_filter or c[t - n] < ema50[t]):
                dn_prev = dn_last; dn_last = (l[t - n], hist[t - n], t - n)
        bear = False
        if up_last and up_prev:
            HLp, HLh, HLb = up_last; HPp, HPh, HPb = up_prev
            if HLh > 0 and HPh > 0 and (HLb + div_win) > t and (HLb - HPb) < div_win:
                bear = (HLp > HPp) and (HLh < HPh)
        bull = False
        if dn_last and dn_prev:
            LLp, LLh, LLb = dn_last; LPp, LPh, LPb = dn_prev
            if LLh < 0 and LPh < 0 and (LLb + div_win) > t and (LLb - LPb) < div_win:
                bull = (LLp < LPp) and (LLh > LPh)
        if bull:
            bull_bias_bar = t; bull_pivot = dn_last[0]
        if bear:
            bear_bias_bar = t; bear_pivot = up_last[0]
        ph = _is_ph(h, t - tl_len, tl_len, tl_len) if t - tl_len >= 0 else False
        pl = _is_pl(l, t - tl_len, tl_len, tl_len) if t - tl_len >= 0 else False
        slope = atr_tl[t] / tl_len * tl_mult
        slope_ph = slope if ph else slope_ph
        slope_pl = slope if pl else slope_pl
        upper = (h[t - tl_len] if ph else (upper - slope_ph))
        lower = (l[t - tl_len] if pl else (lower + slope_pl))
        up_thr = (upper - slope_ph * tl_len) + brk_atr * atr_tl[t]
        dn_thr = (lower + slope_pl * tl_len) - brk_atr * atr_tl[t]
        up_body = (c[t] >= o[t]) if brk_confirm else True
        dn_body = (c[t] <= o[t]) if brk_confirm else True
        up_p, dn_p = upos, dnos
        upos = 0 if ph else (1 if (c[t] > up_thr and up_body) else upos)
        dnos = 0 if pl else (1 if (c[t] < dn_thr and dn_body) else dnos)
        side = None; piv = None
        if upos > up_p and (t - bull_bias_bar) <= bias_expiry and bull_pivot is not None:
            side = "LONG"; piv = bull_pivot
        elif dnos > dn_p and (t - bear_bias_bar) <= bias_expiry and bear_pivot is not None:
            side = "SHORT"; piv = bear_pivot
        if side:
            px = c[t]
            slp = ((px - piv) / px * 100.0 + sl_buf) if side == "LONG" else ((piv - px) / px * 100.0 + sl_buf)
            if slp <= 0:
                slp = sl_floor
            slp = max(sl_floor, min(sl_cap, slp))
            sigs.append({"bar": t, "side": side, "entry": px, "sl_pct": round(slp, 4)})
    return sigs


def scan(kl, **kw):
    """Compat: devuelve SOLO la entrada (divergencia + ruptura) en la ultima vela, o None."""
    return analyze(kl, **kw)["entry"]


def excursions(kl, horizon=36):
    """Distribucion empirica de la excursion favorable maxima en 'horizon' velas (para prob de alcance).
    Devuelve (up_sorted, dn_sorted) en %. horizon=36 velas de 5m = 3h (= el time-stop)."""
    h = [r[2] for r in kl]
    l = [r[3] for r in kl]
    c = [r[4] for r in kl]
    N = len(c)
    up = []
    dn = []
    for i in range(N - horizon):
        b = c[i]
        if b <= 0:
            continue
        up.append((max(h[i + 1:i + 1 + horizon]) - b) / b * 100.0)
        dn.append((b - min(l[i + 1:i + 1 + horizon])) / b * 100.0)
    up.sort()
    dn.sort()
    return up, dn


def prob_ge(sorted_list, dist):
    """% de ventanas historicas donde la excursion alcanzo >= dist% -> prob de TOCAR ese nivel."""
    import bisect
    if not sorted_list or dist is None:
        return None
    idx = bisect.bisect_left(sorted_list, dist)
    return (len(sorted_list) - idx) / len(sorted_list) * 100.0


def poc(kl, bins=48):
    """POC (Point of Control): nivel de precio con MAS volumen (perfil de volumen simple)."""
    if not kl:
        return None
    h = [r[2] for r in kl]
    l = [r[3] for r in kl]
    c = [r[4] for r in kl]
    v = [r[5] for r in kl]
    lo = min(l)
    hi = max(h)
    if hi <= lo:
        return None
    w = (hi - lo) / bins
    vol = [0.0] * bins
    for i in range(len(kl)):
        tp = (h[i] + l[i] + c[i]) / 3.0
        b = int((tp - lo) / w)
        b = max(0, min(bins - 1, b))
        vol[b] += v[i]
    bmax = max(range(bins), key=lambda b: vol[b])
    return lo + (bmax + 0.5) * w


def value_area(kl, bins=48, va_pct=0.70):
    """Perfil de volumen del rango dado -> {poc, vah, val}. Value area = ~70% del volumen
    expandiendo desde el POC al lado con mas volumen (metodo estandar). Para el filtro de ubicacion
    (LONG solo debajo del VAL, SHORT solo arriba del VAH)."""
    if not kl:
        return None
    h = [r[2] for r in kl]; l = [r[3] for r in kl]; c = [r[4] for r in kl]; v = [r[5] for r in kl]
    lo = min(l); hi = max(h)
    if hi <= lo:
        return None
    w = (hi - lo) / bins
    vol = [0.0] * bins
    for i in range(len(kl)):
        tp = (h[i] + l[i] + c[i]) / 3.0
        b = max(0, min(bins - 1, int((tp - lo) / w)))
        vol[b] += v[i]
    total = sum(vol)
    if total <= 0:
        return None
    pb = max(range(bins), key=lambda b: vol[b])
    lob = hib = pb
    acc = vol[pb]
    tgt = total * va_pct
    while acc < tgt and (lob > 0 or hib < bins - 1):
        below = vol[lob - 1] if lob > 0 else -1.0
        above = vol[hib + 1] if hib < bins - 1 else -1.0
        if above >= below:
            hib += 1; acc += vol[hib]
        else:
            lob -= 1; acc += vol[lob]
    return {"poc": round(lo + (pb + 0.5) * w, 8), "vah": round(lo + (hib + 1) * w, 8),
            "val": round(lo + lob * w, 8)}


def naked_poc(kl, recent_frac=0.4, bins=48):
    """NAKED POC = POC del tramo ANTERIOR que el precio NO volvio a tocar en el tramo reciente (imán:
    el precio tiende a volver a buscarlo). Devuelve el precio del nPOC no tocado más cercano, o None."""
    N = len(kl)
    if N < 80:
        return None
    cut = int(N * (1.0 - recent_frac))
    prior, recent = kl[:cut], kl[cut:]
    p = poc(prior, bins)
    if p is None:
        return None
    for r in recent:                 # ¿tocado en el tramo reciente? (low<=p<=high) -> ya no es naked
        if r[3] <= p <= r[2]:
            return None
    return p


def bracket_winrate(kl, target_pct, sl_pct, direction, horizon=36):
    """% de entradas historicas que tocaron +target ANTES que -sl dentro de horizon (first passage).
    = probabilidad EMPIRICA de exito del trade en la dinamica propia del activo."""
    if not kl or not target_pct or not sl_pct or target_pct <= 0 or sl_pct <= 0:
        return None
    h = [r[2] for r in kl]
    l = [r[3] for r in kl]
    c = [r[4] for r in kl]
    N = len(c)
    wins = 0
    tot = 0
    for i in range(N - horizon):
        e = c[i]
        if e <= 0:
            continue
        if direction == "up":
            tp = e * (1 + target_pct / 100.0)
            st = e * (1 - sl_pct / 100.0)
        else:
            tp = e * (1 - target_pct / 100.0)
            st = e * (1 + sl_pct / 100.0)
        res = 0
        for j in range(i + 1, i + 1 + horizon):
            if direction == "up":
                if l[j] <= st:
                    break
                if h[j] >= tp:
                    res = 1
                    break
            else:
                if h[j] >= st:
                    break
                if l[j] <= tp:
                    res = 1
                    break
        tot += 1
        wins += res
    return (wins / tot * 100.0) if tot else None


# ---- test manual contra klines reales ----
if __name__ == "__main__":
    import json, urllib.request, urllib.parse
    def klines(sym, interval=5, limit=500):
        url = "https://api.bybit.com/v5/market/kline?" + urllib.parse.urlencode(
            {"category": "linear", "symbol": sym, "interval": interval, "limit": limit})
        d = json.loads(urllib.request.urlopen(url, timeout=12).read().decode())
        rows = d["result"]["list"]
        out = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows]
        out.sort(key=lambda x: x[0])
        return out[:-1]  # descarta la vela en formacion
    for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TAOUSDT"]:
        kl = klines(s)
        # cuantas senales daria en toda la ventana (diagnostico): recorro cerrando en cada barra
        sigs = 0
        for end in range(60, len(kl)):
            r = scan(kl[:end + 1])
            if r and r["bar_ms"] == kl[end][0]:
                sigs += 1
        last = scan(kl)
        print("%-9s velas=%d  senales_en_ventana=%d  ultima=%s" % (s, len(kl), sigs, last))
