# -*- coding: utf-8 -*-
"""ZONAS SMC = port FIEL del Pine del usuario (PRO_Confluence_definitive_MERGED.pine, secciones ④ FVG/iFVG/BPR y ⑤ ORDER BLOCKS
AlgoAlpha HP-OB con % de supervivencia Kaplan-Meier). Causal: sólo velas CERRADAS. Usuario 2026-09-04: "uso los order blocks
con más de 70 % de fuerza (más nuevo = más sólido), o BPR / iFVG bien utilizados".

compute(K) → por vela j devuelve el estado de zonas vivas y las señales de ENTRADA:
  · OB alcista = última vela bajista antes de un impulso alcista con z(updist,50) cruzando 4.0; % = P(KM) de seguir viva a su edad
    (eventos = OB del mismo lado mitigados antes, censura = expirados a 2000 velas). Mitigado si 2 cierres bajo el piso.
    Rechazo alcista (Pine obBullRej): close > top y close[1] < top. Bajista: close < bot y close[1] > top.
  · FVG alcista: high[2] < low (tamaño ≥ 0.05 %); bajista: low[2] > high. iFVG: el FVG se invierte cuando una vela de
    DESPLAZAMIENTO (|Δ| > umbral = 2 × media histórica de |Δ|) cierra del otro lado. Máx 2 iFVG por lado; mitigado si cierra más allá.
    Entrada: low toca la zona iFVG alcista (tol 0.5 %) y cierra por encima de su techo (rechazo). Simétrico bajista.
  · BPR: solapamiento de un FVG alcista y uno bajista (≤ 50 velas). Se borra cuando una vela barre TODO el rango.
    Entrada: cierre dentro del BPR y a la vela siguiente cierre fuera (arriba → LONG, abajo → SHORT).
K = [[ts, o, h, l, c, v], ...] ascendente."""
import math

OB_ZWIN = 50; OB_ZTHR = 4.0; OB_MAXAGE = 2000; OB_KMMIN = 5
FVG_MIN_PCT = 0.05; IFVG_MAX = 2; BPR_MAX = 3; FVG_LB = 50; FVG_TOL = 0.50


def _sma_std(vals):
    n = len(vals)
    if n == 0:
        return 0.0, 0.0
    m = sum(vals) / n
    v = sum((x - m) ** 2 for x in vals) / n
    return m, math.sqrt(v)


def _km(events, censored, N0, age):
    """Supervivencia Kaplan-Meier a `age` (port f_kmOB). None si N0 < OB_KMMIN o sin eventos."""
    if N0 < OB_KMMIN or not events:
        return None
    E = sorted(events); C = sorted(censored)
    S = 1.0; nrisk = N0; ei = 0; ci = 0
    while ei < len(E):
        ti = E[ei]
        if ti > age:
            break
        while ci < len(C) and C[ci] < ti:
            nrisk -= 1; ci += 1
        d = 0
        while ei < len(E) and E[ei] == ti:
            d += 1; ei += 1
        if nrisk > 0:
            S *= (1.0 - float(d) / float(nrisk))
        nrisk -= d
    return S


def compute(K, want_signals=True):
    """Devuelve lista `sig` de señales [(j, tipo, lado, zona_top, zona_bot, km_pct|None, edad)] y el estado final `state`
    (zonas vivas al cierre de la última vela: obs, ifvgs, bprs con sus % / edades)."""
    n = len(K)
    o = [k[1] for k in K]; h = [k[2] for k in K]; l = [k[3] for k in K]; c = [k[4] for k in K]
    # --- FVG/iFVG/BPR estado ---
    cumabs = 0.0
    aBull = []   # [T, B, x]  (unshift → índice 0 = más nuevo)
    aBear = []
    ifBull = []  # [T, B, x]
    ifBear = []
    bpr = []     # [T, B, x]
    # --- OB estado ---
    updist = 0.0; dndist = 0.0
    up_hist = []; dn_hist = []
    zUp_prev = None; zDn_prev = None
    obBull = []  # dict(top, bot, start)
    obBear = []
    km_ev = {1: [], -1: []}; km_cen = {1: [], -1: []}; km_n0 = {1: 0, -1: 0}
    lastDn = None; lastUp = None   # (idx, high, low)
    sig = []
    for j in range(n):
        dpct = (c[j] - o[j]) / o[j] * 100.0 if o[j] else 0.0
        cumabs += abs(dpct)
        thr = cumabs / max(j, 1) * 2.0
        # ---------- FVG raw ----------
        if j >= 2 and c[j] > 0:
            szL = (l[j] - h[j - 2]) / c[j] * 100.0
            szS = (l[j - 2] - h[j]) / c[j] * 100.0
            isBull = h[j - 2] < l[j] and szL >= FVG_MIN_PCT
            isBear = l[j - 2] > h[j] and szS >= FVG_MIN_PCT
            if isBull:
                aBull.insert(0, [l[j], h[j - 2], j])
                if len(aBull) > 30:
                    aBull.pop()
                for T, B, x in list(aBear):
                    if j - x <= FVG_LB:
                        oT = min(l[j], T); oB = max(h[j - 2], B)
                        if oT > oB:
                            bpr.insert(0, [oT, oB, j])
                            while len(bpr) > BPR_MAX:
                                bpr.pop()
            if isBear:
                aBear.insert(0, [l[j - 2], h[j], j])
                if len(aBear) > 30:
                    aBear.pop()
                for T, B, x in list(aBull):
                    if j - x <= FVG_LB:
                        oT = min(T, l[j - 2]); oB = max(B, h[j])
                        if oT > oB:
                            bpr.insert(0, [oT, oB, j])
                            while len(bpr) > BPR_MAX:
                                bpr.pop()
        bullBreak = dpct < -thr; bearBreak = dpct > thr
        # Bull FVG → iFVG bajista
        for i in range(len(aBull) - 1, -1, -1):
            T, B, x = aBull[i]
            if c[j] < B and bullBreak:
                ifBear.insert(0, [T, B, j])
                while len(ifBear) > IFVG_MAX:
                    ifBear.pop()
                aBull.pop(i)
        for i in range(len(aBear) - 1, -1, -1):
            T, B, x = aBear[i]
            if c[j] > T and bearBreak:
                ifBull.insert(0, [T, B, j])
                while len(ifBull) > IFVG_MAX:
                    ifBull.pop()
                aBear.pop(i)
        # mitigación iFVG / BPR
        ifBull = [z for z in ifBull if not (c[j] < z[1])]
        ifBear = [z for z in ifBear if not (c[j] > z[0])]
        bpr = [z for z in bpr if not (h[j] >= z[0] and l[j] <= z[1])]
        # ---------- OB ----------
        updist = updist + (c[j] - o[j]) if c[j] > o[j] else 0.0
        dndist = dndist + (o[j] - c[j]) if c[j] < o[j] else 0.0
        up_hist.append(updist); dn_hist.append(dndist)
        if len(up_hist) > OB_ZWIN:
            up_hist.pop(0); dn_hist.pop(0)
        um, us = _sma_std(up_hist); dm, ds = _sma_std(dn_hist)
        zUp = (updist - um) / us if us > 0 else None
        zDn = (dndist - dm) / ds if ds > 0 else None
        bullOB = zUp is not None and zUp_prev is not None and zUp_prev <= OB_ZTHR and zUp > OB_ZTHR and zUp_prev != 0
        negDn = (-zDn) if zDn is not None else None; negDn_prev = (-zDn_prev) if zDn_prev is not None else None
        bearOB = negDn is not None and negDn_prev is not None and negDn_prev >= -OB_ZTHR and negDn < -OB_ZTHR and negDn_prev != 0
        if c[j] < o[j]:
            lastDn = (j, h[j], l[j])
        if c[j] > o[j]:
            lastUp = (j, h[j], l[j])
        if bullOB and lastDn is not None:
            obBull.insert(0, {"top": lastDn[1], "bot": lastDn[2], "start": lastDn[0]}); km_n0[1] += 1
        if bearOB and lastUp is not None:
            obBear.insert(0, {"top": lastUp[1], "bot": lastUp[2], "start": lastUp[0]}); km_n0[-1] += 1
        zUp_prev, zDn_prev = zUp, zDn
        rejB = False; rejS = False; rejB_z = None; rejS_z = None
        for i in range(len(obBull) - 1, -1, -1):
            b = obBull[i]; age = j - b["start"]
            mit = j >= 1 and c[j] < b["bot"] and c[j - 1] < b["bot"]
            exp = age >= OB_MAXAGE
            if mit or exp:
                (km_ev if mit else km_cen)[1].append(float(age)); obBull.pop(i); continue
            if j >= 1 and c[j] > b["top"] and c[j - 1] < b["top"]:
                rejB = True; rejB_z = b
        for i in range(len(obBear) - 1, -1, -1):
            b = obBear[i]; age = j - b["start"]
            mit = j >= 1 and c[j] > b["top"] and c[j - 1] > b["top"]
            exp = age >= OB_MAXAGE
            if mit or exp:
                (km_ev if mit else km_cen)[-1].append(float(age)); obBear.pop(i); continue
            if j >= 1 and c[j] < b["bot"] and c[j - 1] > b["top"]:
                rejS = True; rejS_z = b
        if not want_signals:
            continue
        # ---------- SEÑALES de entrada (al cierre de j) ----------
        tol = c[j] * FVG_TOL / 100.0
        if rejB and rejB_z is not None:
            age = j - rejB_z["start"]; km = _km(km_ev[1], km_cen[1], km_n0[1], age)
            sig.append((j, "OB", "LONG", rejB_z["top"], rejB_z["bot"], (km * 100.0) if km is not None else None, age))
        if rejS and rejS_z is not None:
            age = j - rejS_z["start"]; km = _km(km_ev[-1], km_cen[-1], km_n0[-1], age)
            sig.append((j, "OB", "SHORT", rejS_z["top"], rejS_z["bot"], (km * 100.0) if km is not None else None, age))
        for T, B, x in ifBull:
            if l[j] <= T + tol and l[j] >= B - tol and c[j] > T:
                sig.append((j, "IFVG", "LONG", T, B, None, j - x)); break
        for T, B, x in ifBear:
            if h[j] >= B - tol and h[j] <= T + tol and c[j] < B:
                sig.append((j, "IFVG", "SHORT", T, B, None, j - x)); break
        if j >= 1:
            for T, B, x in bpr:
                inside_prev = c[j - 1] <= T + tol and c[j - 1] >= B - tol
                if inside_prev and c[j] > T:
                    sig.append((j, "BPR", "LONG", T, B, None, j - x)); break
                if inside_prev and c[j] < B:
                    sig.append((j, "BPR", "SHORT", T, B, None, j - x)); break
    state = {"ob_bull": [dict(z, age=n - 1 - z["start"], km=(lambda v: round(v * 100) if v is not None else None)(_km(km_ev[1], km_cen[1], km_n0[1], n - 1 - z["start"]))) for z in obBull],
             "ob_bear": [dict(z, age=n - 1 - z["start"], km=(lambda v: round(v * 100) if v is not None else None)(_km(km_ev[-1], km_cen[-1], km_n0[-1], n - 1 - z["start"]))) for z in obBear],
             "ifvg_bull": [{"top": z[0], "bot": z[1], "age": n - 1 - z[2]} for z in ifBull],
             "ifvg_bear": [{"top": z[0], "bot": z[1], "age": n - 1 - z[2]} for z in ifBear],
             "bpr": [{"top": z[0], "bot": z[1], "age": n - 1 - z[2]} for z in bpr]}
    return sig, state


# ---------------- EN VIVO (dashboard) ----------------
_LIVE = {}


def smc_live(sym, tol_pct=0.5, ttl=240):
    """Zonas SMC vivas del símbolo sobre 5m (Binance = la fuente del panel; fallback Bybit): por tipo la zona MÁS CERCANA al
    precio con su distancia %, % KM de los OB, si el precio está DENTRO (tolerancia) y la señal de la última vela cerrada."""
    import time as _t
    now = _t.time()
    h = _LIVE.get(sym)
    if h and now - h[0] < ttl:
        return h[1]
    out = None
    try:
        import imminent_move_alert as _IMM
        K = _IMM._klines(sym, "5m", 1500)
        if K and len(K) > 120:
            K = [[float(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]) if len(x) > 5 else 0.0] for x in K][:-1]
            sig, st = compute(K)
            px = K[-1][4]; tol = px * tol_pct / 100.0
            def _near(zs, key):
                best = None
                for z in zs:
                    mid = (z["top"] + z["bot"]) / 2.0
                    d = (mid - px) / px * 100.0
                    if best is None or abs(d) < abs(best["dist"]):
                        best = {"top": round(z["top"], 6), "bot": round(z["bot"], 6), "dist": round(d, 2), "age": z.get("age"), "km": z.get("km"),
                                "in": bool(z["bot"] - tol <= px <= z["top"] + tol)}
                return best
            out = {"px": px, "ob_bull": _near(st["ob_bull"], "ob"), "ob_bear": _near(st["ob_bear"], "ob"),
                   "ifvg_bull": _near(st["ifvg_bull"], "i"), "ifvg_bear": _near(st["ifvg_bear"], "i"), "bpr": _near(st["bpr"], "b"),
                   "n": {"ob_bull": len(st["ob_bull"]), "ob_bear": len(st["ob_bear"]), "ifvg_bull": len(st["ifvg_bull"]),
                         "ifvg_bear": len(st["ifvg_bear"]), "bpr": len(st["bpr"])}, "sig": None, "ts": now}
            last_j = len(K) - 1
            for (j, typ, side, zt, zb, km, age) in reversed(sig):
                if j >= last_j - 1:
                    out["sig"] = {"typ": typ, "side": side, "km": (round(km) if km is not None else None), "bars_ago": last_j - j}
                    break
            out["in"] = [k for k in ("ob_bull", "ob_bear", "ifvg_bull", "ifvg_bear", "bpr") if out.get(k) and out[k]["in"]]
    except Exception as e:
        out = None
    _LIVE[sym] = (now, out)
    return out
