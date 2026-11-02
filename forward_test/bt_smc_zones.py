# -*- coding: utf-8 -*-
"""MEDICIÓN de las entradas SMC del Pine del usuario (OB ≥70 % KM, iFVG, BPR con rechazo) sobre velas históricas 5m (y 15m
agregadas) — prioridad TQQQ. Para cada señal: MFE/MAE a 4 h, +1 %/+2 % limpio, y 3 salidas simuladas:
  E_zone : SL bajo/sobre la zona (buffer 0.15 %, cap 2 %) y TP = 2R (teoría SMC), máx 6 h
  E_mom  : SL de zona (cap 3 %) + trailing +1.0/0.3 (salida del brazo mom), máx 12 h
  E_fix  : SL −1 % / TP +2 % (motor VP), máx 6 h
Reporte por tipo·lado, OB por tramo de %KM (≥70 vs <70), por franja UTC y por mes. Uso:
  python bt_smc_zones.py [--symbols TQQQUSDT,SOXLUSDT] [--days 60] [--tf 5,15]"""
import os, sys, json, time, argparse
from datetime import datetime, timezone
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bt_cells_history as H
import smc_zones as Z

FEE = 0.11


def agg(K, m):
    """Agrega velas 5m a m×5m (15m = 3)."""
    out = []
    start = next((q for q in range(len(K)) if int(K[q][0]) % (m * 300000) == 0), 0)   # alinear al borde del TF (fix 15m)
    for i in range(start, len(K) - m + 1, m):
        g = K[i:i + m]
        if int(g[0][0] // (m * 300000)) != int(g[-1][0] // (m * 300000)):
            continue
        out.append([g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4], sum(x[5] for x in g)])
    return out


def sim(K, i, side, zone_top, zone_bot, mode, bars_per_h):
    entry = K[i][1]; up = side == "LONG"
    def fav(px): return ((px - entry) / entry * 100) if up else ((entry - px) / entry * 100)
    if mode == "fix":
        slp = 1.0
    else:
        raw = ((entry - zone_bot) / entry * 100 + 0.15) if up else ((zone_top - entry) / entry * 100 + 0.15)
        slp = max(0.3, min(raw, 2.0 if mode == "zone" else 3.0))
    sl = entry * (1 - slp / 100) if up else entry * (1 + slp / 100)
    tp = None
    if mode == "fix":
        tp = entry * (1 + 2.0 / 100) if up else entry * (1 - 2.0 / 100)
    elif mode == "zone":
        tp = entry * (1 + 2 * slp / 100) if up else entry * (1 - 2 * slp / 100)
    maxb = int((12 if mode == "mom" else 6) * bars_per_h)
    end = min(i + maxb, len(K) - 1); peak = 0.0
    for j in range(i, end + 1):
        hh, ll = K[j][2], K[j][3]
        if (ll <= sl) if up else (hh >= sl):
            return fav(sl) - FEE, "sl"
        peak = max(peak, fav(hh if up else ll))
        if tp is not None and ((hh >= tp) if up else (ll <= tp)):
            return fav(tp) - FEE, "tp"
        if mode == "mom" and peak >= 1.0:
            ns = entry * (1 + (peak - 0.3) / 100) if up else entry * (1 - (peak - 0.3) / 100)
            sl = max(sl, ns) if up else min(sl, ns)
    return fav(K[end][4]) - FEE, "time"


def fwd(K, i, entry, side, nb):
    up = side == "LONG"; best = -9.0; mae = 0.0; end = min(i + nb, len(K) - 1)
    for j in range(i + 1, end + 1):
        f = ((K[j][2] - entry) / entry * 100) if up else ((entry - K[j][3]) / entry * 100)
        a = ((entry - K[j][3]) / entry * 100) if up else ((K[j][2] - entry) / entry * 100)
        best = max(best, f); mae = max(mae, a)
    return best, mae


def run(sym, days, tfs):
    K5, src = H.fetch(sym, days)
    if not K5:
        print(sym, "sin datos"); return []
    rows = []
    for tf in tfs:
        K = K5 if tf == 5 else agg(K5, tf // 5)
        bph = 60.0 / tf
        sig, _ = Z.compute(K)
        HT = H.htf_series(K)                        # tendencia 4h (EMA50 sobre 4h agregadas) = proxy del sesgo del dashboard
        dopen = [None] * len(K); cur_day = None; dop = None
        for q in range(len(K)):
            dd = int(K[q][0] // 86400000)
            if dd != cur_day:
                cur_day = dd; dop = K[q][1]
            dopen[q] = dop                          # apertura del día UTC → dirección del día (cierre vs apertura)
        last = {}
        for (j, typ, side, zt, zb, km, age) in sig:
            if j + 2 >= len(K) or j - last.get((typ, side), -999) < 12:
                continue
            last[(typ, side)] = j
            entry = K[j][4]
            mfe, mae = fwd(K, j, entry, side, int(4 * bph))
            ht = HT[j]
            day_dir = "LONG" if (dopen[j] and entry > dopen[j]) else ("SHORT" if (dopen[j] and entry < dopen[j]) else None)
            r = {"sym": sym, "tf": tf, "ts": int(K[j][0] / 1000), "typ": typ, "side": side, "km": km, "age": age,
                 "htf": ht, "htf_ok": (ht is None) or ((ht == "up") == (side == "LONG")), "day_ok": (day_dir == side),
                 "bias_ok": (ht is not None and ((ht == "up") == (side == "LONG")) and day_dir == side),
                 "hb": "h%02d" % ((datetime.fromtimestamp(K[j][0] / 1000, timezone.utc).hour // 4) * 4),
                 "month": datetime.fromtimestamp(K[j][0] / 1000, timezone.utc).strftime("%Y-%m"),
                 "mfe": round(mfe, 3), "mae": round(mae, 3), "zone_pct": round(abs(zt - zb) / entry * 100, 3)}
            for m in ("zone", "mom", "fix"):
                p, why = sim(K, j + 1, side, zt, zb, m, bph)
                r["E_" + m] = round(p, 3); r["R_" + m] = why
            rows.append(r)
        print("%-10s tf=%2dm velas=%5d señales=%4d (%s)" % (sym, tf, len(K), sum(1 for r in rows if r["tf"] == tf), src), flush=True)
    return rows


def table(rows, title, keyf, minn=20):
    g = defaultdict(list)
    for r in rows:
        g[keyf(r)].append(r)
    print("\n%s" % title)
    print("%-30s %5s %6s %7s %7s | %8s %8s %8s | %s" % ("grupo", "n", "+1%", "+2%lmp", "MAEmed", "E_zone", "E_mom", "E_fix", "WR zone/mom/fix"))
    out = {}
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
        n = len(v)
        if n < minn:
            continue
        h1 = 100.0 * sum(1 for r in v if r["mfe"] >= 1) / n
        c2 = 100.0 * sum(1 for r in v if r["mfe"] >= 2 and r["mae"] <= 1) / n
        maes = sorted(r["mae"] for r in v); mm = maes[n // 2]
        E = {m: sum(r["E_" + m] for r in v) / n for m in ("zone", "mom", "fix")}
        W = {m: 100.0 * sum(1 for r in v if r["E_" + m] > 0) / n for m in E}
        out[k] = {"n": n, "hit1": round(h1, 1), "clean2": round(c2, 1), "mae_med": round(mm, 2), "E": {m: round(E[m], 3) for m in E}, "WR": {m: round(W[m], 1) for m in W}}
        print("%-30s %5d %6.1f %7.1f %7.2f | %+8.3f %+8.3f %+8.3f | %s" % (k, n, h1, c2, mm, E["zone"], E["mom"], E["fix"], "/".join("%.0f" % W[m] for m in ("zone", "mom", "fix"))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="TQQQUSDT"); ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--tf", default="5,15")
    a = ap.parse_args()
    tfs = [int(x) for x in a.tf.split(",")]
    rows = []
    for s in a.symbols.split(","):
        rows += run(s, a.days, tfs)
    res = {"generated": time.strftime("%Y-%m-%d %H:%M"), "n": len(rows)}
    def kmb(r):
        if r["typ"] != "OB":
            return r["typ"]
        return "OB %s" % ("KM>=70" if (r["km"] is not None and r["km"] >= 70) else ("KM<70" if r["km"] is not None else "KM n/d"))
    res["tf_typ_side"] = table(rows, "Por TF · tipo · lado", lambda r: "%dm %s %s" % (r["tf"], kmb(r), r["side"]), 15)
    res["typ_side"] = table(rows, "Por tipo · lado (5m+15m)", lambda r: "%s %s" % (kmb(r), r["side"]), 15)
    res["ob_km"] = table([r for r in rows if r["typ"] == "OB" and r["km"] is not None],
                         "OB por tramo de % KM", lambda r: "KM %s" % ("90+" if r["km"] >= 90 else "70-89" if r["km"] >= 70 else "50-69" if r["km"] >= 50 else "<50"), 15)
    res["bias"] = table(rows, "SESGO: a favor (4h Y día) vs en contra · por tipo", lambda r: "%s %s" % (r["typ"], "A FAVOR 4h+día" if r["bias_ok"] else ("solo 4h a favor" if r["htf_ok"] else "CONTRA 4h")), 15)
    FAV = [r for r in rows if r["bias_ok"]]
    res["fav_typ_side"] = table(FAV, "A FAVOR del sesgo (4h+día) · tipo · lado", lambda r: "%s %s" % (kmb(r), r["side"]), 15)
    res["fav_tf"] = table(FAV, "A FAVOR del sesgo · TF · tipo", lambda r: "%dm %s" % (r["tf"], r["typ"]), 15)
    res["fav_month"] = table(FAV, "A FAVOR del sesgo · mes", lambda r: r["month"], 15)
    res["typ_hb"] = table(rows, "Por tipo · franja UTC", lambda r: "%s %s" % (r["typ"], r["hb"]), 15)
    res["typ_month"] = table(rows, "Por tipo · mes (consistencia)", lambda r: "%s %s" % (r["typ"], r["month"]), 15)
    res["sym_typ"] = table(rows, "Por activo · tipo", lambda r: "%s %s" % (r["sym"].replace("USDT", ""), r["typ"]), 15)
    json.dump(res, open(os.path.join(HERE, "bt_smc_zones.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nguardado bt_smc_zones.json")


if __name__ == "__main__":
    main()
