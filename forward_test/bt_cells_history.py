# -*- coding: utf-8 -*-
"""RECONSTRUCCIÓN HISTÓRICA DEL PANEL VP (usuario 2026-09-04: "validá la mayor cantidad posible de celdas con datos descargados;
reconstruí operaciones del pasado; prioridad TQQQ").

Para cada activo del roster baja velas 5m (Binance futures = la misma fuente del panel para cripto; Bybit para acciones/
commodities tokenizadas) y, cada `step` velas, recalcula la fila del panel EXACTAMENTE con `vwap_profile.compute()` sobre las
292 velas CERRADAS previas (mismo código que el panel en vivo) → si la fila es EXTREMO (is_ext) con modo|dir (celda), la etiqueta
IGUAL que label_tracker (entrada = cierre de la última vela cerrada; MFE/MAE/net sobre las 48 velas siguientes = 4 h; una etiqueta
por activo·modo·lado cada 4 h) y además SIMULA la operación del motor VP (fill = apertura de la vela siguiente, SL −1 %, TP +2 %,
SL a +0.3 % al tocar +1.2 %, time-stop 6 h, fee 0.11 %; si SL y TP caen en la misma vela se asume SL = conservador).

Resultados → label_outcomes.db tabla `labels` (kind='bt', src='bt', done=1, eng_pnl) = la MISMA evidencia que usa panel_edge /
vp_engine para validar celdas, ahora con semanas de historia y días distintos. Resumen → bt_cells_history.json.
Uso: python bt_cells_history.py [--days 60] [--step 3] [--tqqq-days 180] [--symbols A,B] [--dry] [--reset-bt]"""
import os, sys, json, time, math, sqlite3, argparse
import urllib.request as _ur
from datetime import datetime, timezone
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE, os.path.join(ROOT, "direction_engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import vwap_profile as VP

LABELS_DB = os.path.join(HERE, "label_outcomes.db")
OUT_JSON = os.path.join(HERE, "bt_cells_history.json")
BARS = 292          # velas cerradas que ve el panel (n=288 + margen, sin la vela en formación)
FWD = 48            # 4 h de 5m (label_tracker FWD_H=4)
ENG_BARS = 72       # 6 h time-stop del motor
SL, TP, SEC_AT, SEC_LOCK, FEE = 1.0, 2.0, 1.2, 0.3, 0.11
PRIORITY = ["TQQQUSDT"]


def _get(url):
    return json.load(_ur.urlopen(_ur.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))


def klines_binance(sym, days):
    """Binance USDT-M futures 5m, paginado hacia atrás. [[ts_ms,o,h,l,c,v], ...] ascendente. None si no existe."""
    out = {}; end = None; need = int(days * 288) + BARS + ENG_BARS + 10
    for _ in range(80):
        url = "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=5m&limit=1500" % sym + ("&endTime=%d" % end if end else "")
        try:
            L = _get(url)
        except Exception as e:
            return None if not out else sorted(([float(k) for k in v] for v in out.values()), key=lambda k: k[0])
        if not isinstance(L, list) or not L:
            break
        for r in L:
            out[int(r[0])] = [r[0], r[1], r[2], r[3], r[4], r[5]]
        end = int(L[0][0]) - 1
        if len(out) >= need or len(L) < 1500:
            break
        time.sleep(0.08)
    if not out:
        return None
    ks = sorted(([float(k) for k in v] for v in out.values()), key=lambda k: k[0])
    return ks[-need:]


def klines_bybit(sym, days):
    out = {}; end = None; need = int(days * 288) + BARS + ENG_BARS + 10
    for _ in range(120):
        url = "https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=5&limit=1000" % sym + ("&end=%d" % end if end else "")
        try:
            d = _get(url)
        except Exception:
            break
        L = d.get("result", {}).get("list", []) or []
        if not L:
            break
        for r in L:
            out[int(r[0])] = [r[0], r[1], r[2], r[3], r[4], r[5]]
        end = min(int(r[0]) for r in L) - 1
        if len(out) >= need or len(L) < 1000:
            break
        time.sleep(0.08)
    if not out:
        return None
    ks = sorted(([float(k) for k in v] for v in out.values()), key=lambda k: k[0])
    return ks[-need:]


CACHE_DIR = os.path.join(HERE, "bt_cache")


def fetch(sym, days):
    """Velas 5m con CACHE en disco (bt_cache/<sym>_<days>.json, válido 6 h) para que las variantes no vuelvan a bajar todo."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp = os.path.join(CACHE_DIR, "%s_%d.json" % (sym, days))
    try:
        if os.path.exists(cp) and time.time() - os.path.getmtime(cp) < 6 * 3600:
            d = json.load(open(cp, encoding="utf-8"))
            if d.get("K") and len(d["K"]) >= BARS + FWD + 50:
                return d["K"], d.get("src")
    except Exception:
        pass
    K = klines_binance(sym, days)
    src = "binance"
    if not K or len(K) < BARS + FWD + 50:
        K = klines_bybit(sym, days); src = "bybit"
    if K and len(K) >= BARS + FWD + 50:
        try:
            json.dump({"src": src, "K": K}, open(cp, "w", encoding="utf-8"))
        except Exception:
            pass
        return K, src
    return None, None


def sim_engine(K, i, side):
    """Operación del motor desde la vela i (fill = open de K[i]). Devuelve (pnl_pct neto, reason, mfe, mae, mae_at_2)."""
    entry = K[i][1]
    up = side == "LONG"
    sl = entry * (1 - SL / 100) if up else entry * (1 + SL / 100)
    tp = entry * (1 + TP / 100) if up else entry * (1 - TP / 100)
    secured = False; mfe = 0.0; mae = 0.0; m2 = None
    end = min(i + ENG_BARS, len(K) - 1)
    for j in range(i, end + 1):
        o, h, l, c = K[j][1], K[j][2], K[j][3], K[j][4]
        fav = ((h - entry) / entry * 100) if up else ((entry - l) / entry * 100)
        adv = ((entry - l) / entry * 100) if up else ((h - entry) / entry * 100)
        hit_sl = (l <= sl) if up else (h >= sl)
        hit_tp = (h >= tp) if up else (l <= tp)
        if hit_sl:                                   # conservador: SL antes que TP en la misma vela
            raw = ((sl - entry) / entry * 100) if up else ((entry - sl) / entry * 100)
            return raw - FEE, ("sl_secure" if secured else "sl"), max(mfe, min(fav, SL if not secured else fav)), max(mae, adv if not secured else mae), m2
        mfe = max(mfe, fav); mae = max(mae, adv)
        if m2 is None and mfe >= TP:
            m2 = mae
        if hit_tp:
            return TP - FEE, "tp2", mfe, mae, m2
        if not secured and mfe >= SEC_AT:
            secured = True
            sl = entry * (1 + SEC_LOCK / 100) if up else entry * (1 - SEC_LOCK / 100)
    c = K[end][4]
    raw = ((c - entry) / entry * 100) if up else ((entry - c) / entry * 100)
    return raw - FEE, "timestop", mfe, mae, m2


def label_fwd(K, i, entry, side):
    """Igual que label_tracker: MFE/MAE sobre K[i+1..i+FWD] (highs/lows), net = cierre de K[i+FWD]."""
    up = side == "LONG"; best = -9.0; mae = 0.0
    end = min(i + FWD, len(K) - 1)
    for j in range(i + 1, end + 1):
        h, l = K[j][2], K[j][3]
        fav = ((h - entry) / entry * 100) if up else ((entry - l) / entry * 100)
        adv = ((entry - l) / entry * 100) if up else ((h - entry) / entry * 100)
        best = max(best, fav); mae = max(mae, adv)
    net = ((K[end][4] - entry) / entry * 100) * (1 if up else -1)
    return round(best, 3), round(mae, 3), round(net, 3)


def htf_series(K):
    """Tendencia 4h (EMA50 sobre cierres de velas 4h CERRADAS, agregadas de las 5m) por índice de vela 5m: 'up'/'down'/None."""
    n = len(K); out = [None] * n
    closes = []; ema = None; cur_bucket = None; last_close = None; k = 2.0 / 51.0
    trend = None
    for i in range(n):
        b = int(K[i][0] // (4 * 3600 * 1000))
        if cur_bucket is None:
            cur_bucket = b
        if b != cur_bucket:                       # cerró una vela 4h → actualiza EMA con su cierre
            closes.append(last_close)
            ema = last_close if ema is None else (last_close - ema) * k + ema
            if len(closes) >= 20:
                trend = "up" if last_close > ema else "down"
            cur_bucket = b
        last_close = K[i][4]
        out[i] = trend
    return out


def run_symbol(sym, days, step):
    K, src = fetch(sym, days)
    if not K:
        return [], None
    rows = []; last_lbl = {}
    HT = htf_series(K)
    for i in range(BARS, len(K) - FWD - 1, step):
        Ks = K[i - BARS:i]                      # velas CERRADAS que ve el panel en la vela i
        try:
            p = VP.compute(Ks, 288, sym=sym)
        except Exception:
            continue
        if not p:
            continue
        px = p["px"]
        up = (px > p["vah"]) or (px >= p["up_band"]); dn = (px < p["val"]) or (px <= p["dn_band"])
        if not (up or dn):
            continue
        adx = p.get("adx", 0.0) or 0.0
        regimen = "tendencia" if adx >= 25 else ("chop" if adx < 20 else "mixto")
        if regimen == "tendencia":
            modo, side = "TENDENCIA", ("LONG" if up else "SHORT")
        elif regimen == "chop":
            modo, side = "FADE", ("SHORT" if up else "LONG")
        else:
            continue                              # mixto: dir_reco=None → el tracker no etiqueta (sin lado)
        key = (modo, side)
        if i - last_lbl.get(key, -999) < FWD:    # una etiqueta por activo·modo·lado cada 4 h (episodio abierto)
            continue
        last_lbl[key] = i
        ts = int(K[i][0] / 1000)
        mfe, mae, net = label_fwd(K, i, px, side)
        pnl, reason, emfe, emae, m2 = sim_engine(K, i, side)
        asig = abs(p.get("sig_pos", 0) or 0)
        # FILTROS de la decisión del panel (aprox. offline): flujo a favor (flow2/CVD 5m + 1h) y no pelear la tendencia 4h
        want = 1 if side == "LONG" else -1
        vals = [p.get("flow2", 0) or 0, (1 if (p.get("recent_cvd", 0) or 0) > 0 else (-1 if (p.get("recent_cvd", 0) or 0) < 0 else 0)),
                p.get("flow2_sup", 0) or 0, p.get("cvd_sup", 0) or 0]
        fa = sum(1 for x in vals if x == want); fc = sum(1 for x in vals if x == -want)
        flow_ok = 1 if (fa >= 1 and fa > fc) else 0
        ht = HT[i - 1]
        htf_ok = 0 if ((side == "LONG" and ht == "down") or (side == "SHORT" and ht == "up")) else 1
        rows.append({"ts": ts, "asset": sym, "side": side, "modo": modo, "mfe": mfe, "mae": mae, "net": net, "hit1": 1 if mfe >= 1 else 0,
                     "entry": px, "adx": round(adx, 1), "regimen": regimen, "sig_pos": round(p.get("sig_pos", 0) or 0, 2),
                     "tier": ("EXTREMO" if asig >= 2.5 else ("FUERTE" if asig >= 2.0 else "leve")),
                     "flow_ok": flow_ok, "htf_ok": htf_ok, "htf": ht, "conv": fa,
                     "eng_pnl": round(pnl, 3), "eng_reason": reason, "eng_m2": m2, "src": src})
    return rows, src


def hb(ts):
    return "h%02d" % ((datetime.fromtimestamp(ts, timezone.utc).hour // 4) * 4)


def summarize(rows):
    g = defaultdict(list)
    for r in rows:
        k3 = "%s|%s|%s" % (r["modo"], r["side"], hb(r["ts"])); k2 = "%s|%s" % (r["modo"], r["side"])
        for k in (k3, k2, r["side"]):
            g[k].append(r)
        g["%s·%s" % (r["asset"].replace("USDT", ""), k2)].append(r)
    out = {}
    for k, v in g.items():
        n = len(v)
        c2 = sum(1 for x in v if x["mfe"] >= 2 and x["mae"] <= 1)
        days = len({datetime.fromtimestamp(x["ts"], timezone.utc).strftime("%Y-%m-%d") for x in v})
        months = defaultdict(list)
        for x in v:
            months[datetime.fromtimestamp(x["ts"], timezone.utc).strftime("%Y-%m")].append(x["eng_pnl"])
        f = [x for x in v if x.get("flow_ok") and x.get("htf_ok")]      # subpoblación que el motor SÍ operaría (flujo + no contra 4h)
        fn = len(f)
        out[k] = {"n": n, "clean2": round(100.0 * c2 / n, 1), "hit1": round(100.0 * sum(x["hit1"] for x in v) / n, 1),
                  "net4h": round(sum(x["net"] for x in v) / n, 3), "eng_pnl": round(sum(x["eng_pnl"] for x in v) / n, 3),
                  "eng_wr": round(100.0 * sum(1 for x in v if x["eng_pnl"] > 0) / n, 1),
                  "eng_tp": round(100.0 * sum(1 for x in v if x["eng_reason"] == "tp2") / n, 1), "days": days,
                  "f_n": fn,
                  "f_clean2": round(100.0 * sum(1 for x in f if x["mfe"] >= 2 and x["mae"] <= 1) / fn, 1) if fn else None,
                  "f_eng_pnl": round(sum(x["eng_pnl"] for x in f) / fn, 3) if fn else None,
                  "f_eng_wr": round(100.0 * sum(1 for x in f if x["eng_pnl"] > 0) / fn, 1) if fn else None,
                  "f_days": len({datetime.fromtimestamp(x["ts"], timezone.utc).strftime("%Y-%m-%d") for x in f}),
                  "months": {m: (round(sum(p) / len(p), 2), len(p)) for m, p in sorted(months.items())}}
    return out


def write_db(rows, reset=False):
    c = sqlite3.connect(LABELS_DB, timeout=30)
    for col, typ in (("src", "TEXT"), ("eng_pnl", "REAL"), ("eng_reason", "TEXT"), ("flow_ok", "INTEGER"), ("htf_ok", "INTEGER")):
        try:
            c.execute("ALTER TABLE labels ADD COLUMN %s %s" % (col, typ))
        except Exception:
            pass
    if reset:
        c.execute("DELETE FROM labels WHERE src='bt'")
    n = 0
    for r in rows:
        if c.execute("SELECT 1 FROM labels WHERE src='bt' AND asset=? AND kind=? AND side=? AND ts=?", (r["asset"], "bt_" + r["modo"], r["side"], r["ts"])).fetchone():
            continue
        c.execute("INSERT INTO labels(ts,asset,side,kind,entry_px,done,mfe,mae,net,hit1,measured_ts,adx,regimen,modo_reco,dir_reco,tier,sig_pos,src,eng_pnl,eng_reason,flow_ok,htf_ok) "
                  "VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,'bt',?,?,?,?)",
                  (r["ts"], r["asset"], r["side"], "bt_" + r["modo"], r["entry"], r["mfe"], r["mae"], r["net"], r["hit1"], int(time.time()),
                   r["adx"], r["regimen"], r["modo"], r["side"], r["tier"], r["sig_pos"], r["eng_pnl"], r["eng_reason"], r["flow_ok"], r["htf_ok"]))
        n += 1
    c.commit(); c.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60); ap.add_argument("--tqqq-days", type=int, default=180)
    ap.add_argument("--step", type=int, default=3); ap.add_argument("--symbols", default="")
    ap.add_argument("--dry", action="store_true"); ap.add_argument("--reset-bt", action="store_true")
    a = ap.parse_args()
    if a.symbols:
        syms = a.symbols.split(",")
    else:
        from direction_engine import sott_zone as SZ
        syms = SZ.roster()
    syms = PRIORITY + [s for s in syms if s not in PRIORITY]
    allrows = []; t0 = time.time()
    for s in syms:
        d = a.tqqq_days if s in PRIORITY else a.days
        t1 = time.time()
        rows, src = run_symbol(s, d, a.step)
        allrows += rows
        print("%-12s src=%-7s días=%3d etiquetas=%4d  (%.0fs)" % (s, src, d, len(rows), time.time() - t1), flush=True)
    S = summarize(allrows)
    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M"), "days": a.days, "tqqq_days": a.tqqq_days, "step": a.step,
               "n_labels": len(allrows), "cells": S}, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nCELDAS modo|lado|franja (n>=30) · TODAS las filas vs FILTRADAS (flujo a favor + no contra 4h = lo que opera el motor):")
    print("%-26s %5s %7s %8s %5s | %5s %8s %9s %6s %5s" % ("celda", "n", "clean2%", "eng_pnl%", "días", "f_n", "f_clean2", "f_eng_pnl", "f_wr%", "f_d"))
    for k, v in sorted(S.items(), key=lambda kv: -(kv[1]["f_eng_pnl"] if kv[1]["f_eng_pnl"] is not None else -9)):
        if k.count("|") == 2 and v["n"] >= 30:
            print("%-26s %5d %7.1f %+8.3f %5d | %5d %8s %9s %6s %5d" % (k, v["n"], v["clean2"], v["eng_pnl"], v["days"], v["f_n"],
                  ("%.1f" % v["f_clean2"]) if v["f_clean2"] is not None else "-", ("%+.3f" % v["f_eng_pnl"]) if v["f_eng_pnl"] is not None else "-",
                  ("%.1f" % v["f_eng_wr"]) if v["f_eng_wr"] is not None else "-", v["f_days"]))
    print("\nTQQQ por celda (todas | filtradas):")
    for k, v in sorted(S.items()):
        if k.startswith("TQQQ·"):
            print("  %-22s n=%3d clean2=%4.1f%% eng=%+.3f%% wr=%4.1f%% | f_n=%3d f_clean2=%s f_eng=%s f_wr=%s  meses=%s" % (
                k, v["n"], v["clean2"], v["eng_pnl"], v["eng_wr"], v["f_n"], v["f_clean2"], v["f_eng_pnl"], v["f_eng_wr"], v["months"]))
    if not a.dry:
        n = write_db(allrows, reset=a.reset_bt)
        print("\ninsertadas %d etiquetas 'bt' en label_outcomes.db (total %d) · %.0fs" % (n, len(allrows), time.time() - t0))


if __name__ == "__main__":
    main()
