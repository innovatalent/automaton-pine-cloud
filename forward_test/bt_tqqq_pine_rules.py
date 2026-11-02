# -*- coding: utf-8 -*-
"""REGLAS DEL USUARIO (Pine) medidas en TQQQ (usuario 2026-09-04: "tenés que encontrar el edge; el CRT/turtle soup me graficó la
onda long apenas comenzó; con el screener veo delta +/- en los extremos del perfil; el panel debería detectarlo").
Port FIEL de:
  A) CRT / TURTLE SOUP (PRO_Confluence ⑧e f_crt2): en velas CERRADAS de TF alto, la última cerrada [1] barre el rango de la previa
     [2] (low[1] < low[2]) y cierra ADENTRO → CRT LONG: entry = low[2] (nivel barrido), target = high[2] (otro extremo del rango);
     invalida si cierra bajo el entry. Se evalúa en 15m, 30m, 1h y 4h agregadas de las 5m. Operación: fill al open de la vela
     siguiente, SL = extremo barrido − 0.1 %, TP = target, máx 12 h.
  B) SWING TQQQ (TQQQ_Swing_Perfil_Screener_v2 PARTE 1): z75 sale del extremo (z > −1.5 con lowest(z,6)[1] ≤ −1.5) + SOTT
     (VIDYA(stoch 200, 20, CMO 9)) k ≤ 40 + anti-cuchillo (low > lowest(low,20)[1]) + proporción de flujo (20 velas, pares de delta
     positivo consecutivo ≥ 50 %). Mirror SHORT + no shortear > EMA200 + 1.5 %. Variantes: FUERTE (+ cruce MACD 12/26/9 desde
     abajo de 0 en ≤1 vela), PISO/TECHO PROFUNDO (lowest(z,6) ≤ −4, z subiendo, vela alcista, k ≤ 45), CAÍDA BRUSCA (−2.5 % en 6
     velas bajo EMA200, z subiendo, k ≤ 45). Salida del Pine: stop 1 %, trailing act 0.8 / gap 0.4.
  C) DELTA EN EL EXTREMO DEL PERFIL (PARTE 4 + panel): fila en extremo del VP 24h (fuera del VA o ±2.5σ) con el delta del NIVEL a
     favor (comprador abajo → LONG, vendedor arriba → SHORT) usando vwap_profile.compute (el mismo del panel).
Salidas comparadas: pine (stop 1 % + trail 0.8/0.4), fijo (SL −1/TP +2), mom (SL 3 % + trail 1.0/0.3), y para CRT su target.
Uso: python bt_tqqq_pine_rules.py [--symbols TQQQUSDT] [--days 180] [--today]"""
import os, sys, json, time, math, argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bt_cells_history as H
import vwap_profile as VP

FEE = 0.11
ART = timezone(timedelta(hours=-3))


# ---------------- indicadores (port) ----------------
def sma_std(x, n):
    out = [None] * len(x); s = 0.0; s2 = 0.0
    for i, v in enumerate(x):
        s += v; s2 += v * v
        if i >= n:
            s -= x[i - n]; s2 -= x[i - n] ** 2
        if i >= n - 1:
            m = s / n; var = max(s2 / n - m * m, 0.0)
            out[i] = (m, math.sqrt(var))
    return out


def ema(x, n):
    a = 2.0 / (n + 1); out = []; v = None
    for val in x:
        v = val if v is None else a * val + (1 - a) * v
        out.append(v)
    return out


def rolling(x, n, fn):
    out = [None] * len(x)
    for i in range(len(x)):
        if i >= n - 1:
            out[i] = fn(x[i - n + 1:i + 1])
    return out


def sott_k(c, h, l, n=200, sm=20):
    """k = VIDYA(stoch(close,high,low,200), 20) con alpha*|CMO(9)|/100 (port f_vidya)."""
    hh = rolling(h, n, max); ll = rolling(l, n, min)
    st = [None] * len(c)
    for i in range(len(c)):
        if hh[i] is not None and hh[i] > ll[i]:
            st[i] = 100.0 * (c[i] - ll[i]) / (hh[i] - ll[i])
    out = [None] * len(c); v = None; alpha = 2.0 / (sm + 1)
    for i in range(len(c)):
        if st[i] is None:
            continue
        # CMO(9) del stoch
        ups = 0.0; dns = 0.0
        for q in range(max(1, i - 8), i + 1):
            if st[q] is None or st[q - 1] is None:
                continue
            d = st[q] - st[q - 1]
            ups += d if d > 0 else 0.0; dns += -d if d < 0 else 0.0
        cmo = 100.0 * (ups - dns) / (ups + dns) if (ups + dns) > 0 else 0.0
        kk = abs(cmo) / 100.0
        v = st[i] if v is None else alpha * kk * st[i] + (1 - alpha * kk) * v
        out[i] = v
    return out


def macd(c, f=12, s=26, sig=9):
    ef = ema(c, f); es = ema(c, s); m = [a - b for a, b in zip(ef, es)]; sg = ema(m, sig)
    return m, sg


# ---------------- señales ----------------
def swing_signals(K):
    """Port PARTE 1: longNew/shortNew + fuerte + deep + sharp. Devuelve dict j -> lista de (tipo, lado)."""
    o = [k[1] for k in K]; h = [k[2] for k in K]; l = [k[3] for k in K]; c = [k[4] for k in K]; v = [k[5] for k in K]
    n = len(K)
    zs = sma_std(c, 75)
    z = [((c[i] - zs[i][0]) / zs[i][1]) if (zs[i] and zs[i][1] > 0) else 0.0 for i in range(n)]
    k = sott_k(c, h, l)
    delt = [v[i] * (c[i] - o[i]) / max(h[i] - l[i], 1e-9) for i in range(n)]
    f2u = [1 if (i >= 1 and delt[i] > 0 and delt[i - 1] > 0) else 0 for i in range(n)]
    f2d = [1 if (i >= 1 and delt[i] < 0 and delt[i - 1] < 0) else 0 for i in range(n)]
    e200 = ema(c, 200); m, sg = macd(c)
    low20 = rolling(l, 20, min); high20 = rolling(h, 20, max)
    zmin6 = rolling(z, 6, min); zmax6 = rolling(z, 6, max)
    out = defaultdict(list); prevL = False; prevS = False; prevDP = False; prevDT = False; prevSD = False; prevSR = False
    last_xup = -99; last_xdn = -99
    for i in range(210, n):
        if i >= 1 and m[i - 1] <= sg[i - 1] and m[i] > sg[i] and m[i] < 0:
            last_xup = i
        if i >= 1 and m[i - 1] >= sg[i - 1] and m[i] < sg[i] and m[i] > 0:
            last_xdn = i
        if k[i] is None or zmin6[i - 1] is None or low20[i - 1] is None:
            continue
        zexitL = z[i] > -1.5 and zmin6[i - 1] <= -1.5
        zexitS = z[i] < 1.5 and zmax6[i - 1] >= 1.5
        okLowL = l[i] > low20[i - 1]; okHighS = h[i] < high20[i - 1]
        buyC = sum(f2u[i - 19:i + 1]); sellC = sum(f2d[i - 19:i + 1]); tot = buyC + sellC
        prop = buyC / tot if tot > 0 else 0.5
        flowOkL = prop >= 0.5; flowOkS = prop <= 0.5
        noShortOk = c[i] <= e200[i] * 1.015
        longSig = zexitL and k[i] <= 40 and okLowL and flowOkL
        shortSig = zexitS and k[i] >= 60 and okHighS and flowOkS and noShortOk
        longNew = longSig and not prevL; shortNew = shortSig and not prevS
        prevL, prevS = longSig, shortSig
        if longNew:
            out[i].append(("SWING", "LONG"))
            if i - last_xup <= 1:
                out[i].append(("SWING_FUERTE", "LONG"))
        if shortNew:
            out[i].append(("SWING", "SHORT"))
            if i - last_xdn <= 1:
                out[i].append(("SWING_FUERTE", "SHORT"))
        deepP = zmin6[i] is not None and zmin6[i] <= -4.0 and z[i] > z[i - 1] and c[i] > o[i] and k[i] <= 45
        deepT = zmax6[i] is not None and zmax6[i] >= 4.0 and z[i] < z[i - 1] and c[i] < o[i] and k[i] >= 55
        if deepP and not prevDP:
            out[i].append(("PISO_PROFUNDO", "LONG"))
        if deepT and not prevDT:
            out[i].append(("TECHO_PROFUNDO", "SHORT"))
        prevDP, prevDT = deepP, deepT
        retN = (c[i] - c[i - 6]) / c[i - 6] * 100.0
        distE = (c[i] - e200[i]) / e200[i] * 100.0
        sharpD = retN <= -2.5 and distE < 0 and z[i] > z[i - 1] and c[i] > o[i] and k[i] <= 45
        sharpR = retN >= 2.5 and distE > 0 and z[i] < z[i - 1] and c[i] < o[i] and k[i] >= 55
        if sharpD and not prevSD:
            out[i].append(("CAIDA_BRUSCA", "LONG"))
        if sharpR and not prevSR:
            out[i].append(("SUBIDA_BRUSCA", "SHORT"))
        prevSD, prevSR = sharpD, sharpR
    return out


def crt_signals(K5, m):
    """CRT en velas de m×5m cerradas: la vela [1] barre el rango de [2] y cierra adentro. Devuelve lista de
    (j5 = índice 5m de la vela siguiente al cierre de [1], lado, entry=nivel barrido, target=otro extremo, sweep_extreme)."""
    KA = []
    start = next((q for q in range(len(K5)) if int(K5[q][0]) % (m * 300000) == 0), 0)
    idx_map = []
    for i in range(start, len(K5) - m + 1, m):
        g = K5[i:i + m]
        if int(g[0][0] // (m * 300000)) != int(g[-1][0] // (m * 300000)):
            continue
        KA.append([g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4]]); idx_map.append(i + m)   # cierra en 5m idx i+m-1 → siguiente = i+m
    out = []
    for a in range(2, len(KA)):
        ph, pl = KA[a - 1][2], KA[a - 1][3]     # rango de la vela previa [2] respecto de la que confirma [1]=a
        if not ph > pl:
            continue
        h1, l1, c1 = KA[a][2], KA[a][3], KA[a][4]
        sweptH = h1 > ph; sweptL = l1 < pl; inside = pl <= c1 <= ph; mid = (ph + pl) / 2.0; both = sweptH and sweptL
        cBull = sweptL and inside and (not both or c1 > mid)
        cBear = sweptH and inside and (not both or c1 <= mid)
        if cBull:
            out.append((idx_map[a], "LONG", pl, ph, l1))
        elif cBear:
            out.append((idx_map[a], "SHORT", ph, pl, h1))
    return out


# ---------------- salidas ----------------
def sim(K, i, side, mode, sl_pct=None, tp_px=None, maxb=72):
    entry = K[i][1]; up = side == "LONG"
    def fav(px): return ((px - entry) / entry * 100) if up else ((entry - px) / entry * 100)
    slp = {"pine": 1.0, "fix": 1.0, "mom": 3.0}.get(mode, sl_pct or 1.0)
    if mode == "crt" and sl_pct is not None:
        slp = max(0.15, min(sl_pct, 3.0))
    sl = entry * (1 - slp / 100) if up else entry * (1 + slp / 100)
    tp = tp_px if mode == "crt" else ((entry * (1 + 2.0 / 100) if up else entry * (1 - 2.0 / 100)) if mode == "fix" else None)
    peak = 0.0; end = min(i + maxb, len(K) - 1)
    for j in range(i, end + 1):
        hh, ll = K[j][2], K[j][3]
        if (ll <= sl) if up else (hh >= sl):
            return fav(sl) - FEE, "sl"
        peak = max(peak, fav(hh if up else ll))
        if tp is not None and ((hh >= tp) if up else (ll <= tp)):
            return fav(tp) - FEE, "tp"
        if mode == "pine" and peak >= 0.8:
            ns = entry * (1 + (peak - 0.4) / 100) if up else entry * (1 - (peak - 0.4) / 100)
            sl = max(sl, ns) if up else min(sl, ns)
        if mode == "mom" and peak >= 1.0:
            ns = entry * (1 + (peak - 0.3) / 100) if up else entry * (1 - (peak - 0.3) / 100)
            sl = max(sl, ns) if up else min(sl, ns)
    return fav(K[end][4]) - FEE, "time"


def fwd(K, i, side, nb=48):
    entry = K[i][1]; up = side == "LONG"; best = -9; mae = 0.0; end = min(i + nb, len(K) - 1)
    for j in range(i, end + 1):
        f = ((K[j][2] - entry) / entry * 100) if up else ((entry - K[j][3]) / entry * 100)
        a = ((entry - K[j][3]) / entry * 100) if up else ((K[j][2] - entry) / entry * 100)
        best = max(best, f); mae = max(mae, a)
    return round(best, 3), round(mae, 3)


def run(sym, days, tfs_swing=(5, 15), crt_tfs=(15, 30, 60, 240)):
    K5, src = H.fetch(sym, days)
    if not K5:
        print(sym, "sin datos"); return []
    rows = []
    # A) CRT
    for m in crt_tfs:
        for (j, side, entry_lvl, target, sweep_ext) in crt_signals(K5, m // 5):   # m = minutos → velas de 5m (fix)
            if j + 1 >= len(K5):
                continue
            fillpx = K5[j][1]
            slp = abs(fillpx - sweep_ext) / fillpx * 100.0 + 0.1
            tgt_pct = (target - fillpx) / fillpx * 100.0 * (1 if side == "LONG" else -1)
            if tgt_pct <= 0.15:
                continue                                   # target ya alcanzado / rango nulo
            mfe, mae = fwd(K5, j, side)
            # GATE del Pine (crtHighProb): giro sólo en BALANCE / dentro del Value Area y sin tendencia fuerte (ADX)
            gate = None
            try:
                pp = VP.compute(K5[max(0, j - 292):j], 288, sym=sym) or {}
                inva = pp.get("val") is not None and pp["val"] <= fillpx <= pp["vah"]
                gate = bool(inva and (pp.get("adx", 0) or 0) < 28)
            except Exception:
                pass
            r = {"sym": sym, "typ": "CRT_%dm" % m, "side": side, "ts": int(K5[j][0] / 1000), "mfe": mfe, "mae": mae, "tgt_pct": round(tgt_pct, 2), "sl_pct": round(slp, 2), "gate": gate}
            r["E_crt"], r["R_crt"] = sim(K5, j, side, "crt", sl_pct=slp, tp_px=target, maxb=144)
            for mo in ("pine", "fix", "mom"):
                r["E_" + mo], r["R_" + mo] = sim(K5, j, side, mo)
            rows.append(r)
    # B) swing (5m y 15m)
    for tf in tfs_swing:
        K = K5 if tf == 5 else H.__dict__.get("agg", None)
        if tf != 5:
            from bt_smc_zones import agg as _agg
            K = _agg(K5, tf // 5)
        sigs = swing_signals(K)
        for j in sorted(sigs):
            if j + 2 >= len(K):
                continue
            for (typ, side) in sigs[j]:
                mfe, mae = fwd(K, j + 1, side, int(4 * 60 / tf))
                r = {"sym": sym, "typ": "%s_%dm" % (typ, tf), "side": side, "ts": int(K[j + 1][0] / 1000), "mfe": mfe, "mae": mae}
                for mo in ("pine", "fix", "mom"):
                    r["E_" + mo], r["R_" + mo] = sim(K, j + 1, side, mo, maxb=int(6 * 60 / tf))
                r["E_crt"] = None
                rows.append(r)
    # C) delta en el extremo del perfil (5m, cada 3 velas): extremo VP con delta del nivel a favor
    last = {}
    for i in range(292, len(K5) - 49, 3):
        try:
            p = VP.compute(K5[i - 292:i], 288, sym=sym)
        except Exception:
            continue
        if not p:
            continue
        px = p["px"]; up = (px > p["vah"]) or (px >= p["up_band"]); dn = (px < p["val"]) or (px <= p["dn_band"])
        ld = p.get("lvl_delta", 0) or 0
        side = "LONG" if (dn and ld > 0) else ("SHORT" if (up and ld < 0) else None)
        if not side or i - last.get(side, -999) < 48:
            continue
        last[side] = i
        mfe, mae = fwd(K5, i, side)
        r = {"sym": sym, "typ": "VP_DELTA_5m", "side": side, "ts": int(K5[i][0] / 1000), "mfe": mfe, "mae": mae, "E_crt": None}
        for mo in ("pine", "fix", "mom"):
            r["E_" + mo], r["R_" + mo] = sim(K5, i, side, mo)
        rows.append(r)
    print("%-10s src=%s velas5m=%d señales=%d" % (sym, src, len(K5), len(rows)), flush=True)
    return rows


def table(rows, title, keyf, minn=15):
    g = defaultdict(list)
    for r in rows:
        g[keyf(r)].append(r)
    print("\n%s" % title)
    print("%-30s %5s %6s %7s %7s | %8s %8s %8s %8s | %s" % ("grupo", "n", "+1%", "+2%lmp", "MAEmed", "E_pine", "E_fix", "E_mom", "E_crt", "WR pine/fix/mom"))
    out = {}
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
        n = len(v)
        if n < minn:
            continue
        h1 = 100.0 * sum(1 for r in v if r["mfe"] >= 1) / n
        c2 = 100.0 * sum(1 for r in v if r["mfe"] >= 2 and r["mae"] <= 1) / n
        maes = sorted(r["mae"] for r in v); mm = maes[n // 2]
        E = {m: sum(r["E_" + m] for r in v) / n for m in ("pine", "fix", "mom")}
        W = {m: 100.0 * sum(1 for r in v if r["E_" + m] > 0) / n for m in E}
        cr = [r["E_crt"] for r in v if r.get("E_crt") is not None]
        Ec = (sum(cr) / len(cr)) if cr else None
        out[k] = {"n": n, "hit1": round(h1, 1), "clean2": round(c2, 1), "mae_med": round(mm, 2), "E": {m: round(E[m], 3) for m in E}, "WR": {m: round(W[m], 1) for m in W}, "E_crt": (round(Ec, 3) if Ec is not None else None)}
        print("%-30s %5d %6.1f %7.1f %7.2f | %+8.3f %+8.3f %+8.3f %8s | %s" % (k, n, h1, c2, mm, E["pine"], E["fix"], E["mom"], ("%+.3f" % Ec) if Ec is not None else "-", "/".join("%.0f" % W[m] for m in ("pine", "fix", "mom"))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="TQQQUSDT"); ap.add_argument("--days", type=int, default=180); ap.add_argument("--today", action="store_true")
    a = ap.parse_args()
    rows = []
    for s in a.symbols.split(","):
        rows += run(s, a.days)
    res = {"generated": time.strftime("%Y-%m-%d %H:%M"), "n": len(rows)}
    res["typ_side"] = table(rows, "Por regla · lado", lambda r: "%s %s" % (r["typ"], r["side"]))
    res["typ"] = table(rows, "Por regla", lambda r: r["typ"])
    res["crt_gate"] = table([r for r in rows if r["typ"].startswith("CRT")], "CRT con/sin GATE del Pine (en VA y ADX<28) · TF · lado",
                            lambda r: "%s %s %s" % (r["typ"], r["side"], "GATE ok" if r.get("gate") else "sin gate"))
    res["month"] = table(rows, "Por mes (todas las reglas)", lambda r: datetime.fromtimestamp(r["ts"], timezone.utc).strftime("%Y-%m"))
    res["sym_typ"] = table(rows, "Por activo · regla", lambda r: "%s %s" % (r["sym"].replace("USDT", ""), r["typ"].split("_")[0]))
    if a.today:
        day = datetime.now(ART).strftime("%Y-%m-%d")
        print("\nSEÑALES DE HOY (%s, hora ART) en %s:" % (day, a.symbols))
        for r in sorted(rows, key=lambda r: r["ts"]):
            t = datetime.fromtimestamp(r["ts"], ART)
            if t.strftime("%Y-%m-%d") != day:
                continue
            print("  %s %-18s %-5s MFE +%.2f%% MAE -%.2f%% | pine %+.2f (%s) fijo %+.2f mom %+.2f%s" % (
                t.strftime("%H:%M"), r["typ"], r["side"], r["mfe"], r["mae"], r["E_pine"], r["R_pine"], r["E_fix"], r["E_mom"],
                (" crt %+.2f (%s, tgt %+.2f%%)" % (r["E_crt"], r["R_crt"], r["tgt_pct"])) if r.get("E_crt") is not None else ""))
    json.dump(res, open(os.path.join(HERE, "bt_tqqq_pine_rules.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nguardado bt_tqqq_pine_rules.json")


if __name__ == "__main__":
    main()
