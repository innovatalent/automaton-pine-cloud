# -*- coding: utf-8 -*-
"""PANEL EDGE — probabilidad EMPÍRICA de "+2% LIMPIO" por fila del panel VP+VWAP (pedido usuario 2026-09-04: "mejorá el
panel para operar mejor con las señales"). Sin IA: conteo puro sobre la evidencia medida.

Evidencia: label_outcomes.db (cada etiqueta del panel medida 4h forward: MFE/MAE) + vp_engine.db (trades del motor, ×2).
Bucket jerárquico (del más fino al más grueso), se usa el más fino con n >= MIN_N:
   L3 = modo | lado | franja horaria UTC de 4h   (ej TENDENCIA|LONG|h12)   ← la hora fue el discriminador más fuerte medido
   L2 = modo | lado                              (ej TENDENCIA|LONG)
   L1 = lado                                    (LONG / SHORT)
Salida por fila: {p2: % que llegó a +2% con MAE<=1%, n, level, net, hit1, key}. Se cachea en panel_edge.json (rebuild diario).
Uso: python panel_edge.py  → reconstruye y muestra la tabla."""
import os, json, time, sqlite3
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS_DB = os.path.join(HERE, "label_outcomes.db")
ENGINE_DB = os.path.join(HERE, "vp_engine.db")
CACHE = os.path.join(HERE, "panel_edge.json")
MIN_N = 20
_mem = {"ts": 0.0, "data": None}


def hour_bucket(ts=None):
    h = datetime.fromtimestamp(ts, timezone.utc).hour if ts else datetime.now(timezone.utc).hour
    return "h%02d" % ((h // 4) * 4)


def _day(ts):
    try:
        return datetime.fromtimestamp(float(ts or 0), timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def keys_for(modo, side, hb):
    modo = modo or "mixto"
    return ["%s|%s|%s" % (modo, side, hb), "%s|%s" % (modo, side), "%s" % side]


def _acc(g, keys, mfe, mae, net, w=1, day=None, eng=None):
    for k in keys:
        a = g.setdefault(k, {"n": 0, "c2": 0, "h1": 0, "net": 0.0, "days": set(), "eng_n": 0, "eng": 0.0})
        a["n"] += w; a["net"] += net * w
        if eng is not None:                       # resultado SIMULADO con la salida del motor (reconstrucción bt / journal real)
            a["eng_n"] += w; a["eng"] += eng * w
        if day:
            a["days"].add(day)
        if mfe >= 2.0 and mae <= 1.0:
            a["c2"] += w
        if mfe >= 1.0:
            a["h1"] += w


def rebuild(write=True):
    g = {}
    try:
        c = sqlite3.connect("file:%s?mode=ro" % LABELS_DB.replace("\\", "/"), uri=True)
        try:
            _cols = [r[1] for r in c.execute("PRAGMA table_info(labels)")]
        except Exception:
            _cols = []
        _q = "SELECT ts, modo_reco, dir_reco, side, mfe, mae, net, " + ("eng_pnl" if "eng_pnl" in _cols else "NULL") + " FROM labels WHERE done=1"
        for ts, modo, dirr, side, mfe, mae, net, eng in c.execute(_q):
            sd = dirr or side
            if sd not in ("LONG", "SHORT"):
                continue
            _acc(g, keys_for(modo, sd, hour_bucket(ts)), mfe or 0.0, abs(mae or 0.0), net or 0.0, day=_day(ts), eng=eng)
        c.close()
    except Exception:
        pass
    try:
        if os.path.exists(ENGINE_DB):
            c = sqlite3.connect("file:%s?mode=ro" % ENGINE_DB.replace("\\", "/"), uri=True)
            for ts, modo, side, mfe, mae, m2, pnl in c.execute("SELECT ts_open_epoch, modo, side, mfe, mae, mae_at_2, pnl_pct FROM trades WHERE status='CLOSED'"):
                mae_eff = m2 if m2 is not None else abs(mae or 0.0)
                _acc(g, keys_for(modo, side, hour_bucket(ts)), mfe or 0.0, mae_eff, pnl or 0.0, w=2, day=_day(ts), eng=pnl)
            c.close()
    except Exception:
        pass
    out = {}
    for k, a in g.items():
        n = a["n"]
        out[k] = {"n": n, "p2": round(100.0 * a["c2"] / n, 1), "hit1": round(100.0 * a["h1"] / n, 1), "net": round(a["net"] / n, 3),
                  "days": len(a["days"]),   # días distintos en la muestra (anti-artefacto de un solo día)
                  "eng_n": a["eng_n"], "eng_pnl": round(a["eng"] / a["eng_n"], 3) if a["eng_n"] else None}
    data = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "min_n": MIN_N, "buckets": out}
    if write:
        try:
            json.dump(data, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
    _mem.update(ts=time.time(), data=data)
    return data


def table():
    if _mem["data"] and time.time() - _mem["ts"] < 600:
        return _mem["data"]
    try:
        if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 24 * 3600:
            d = json.load(open(CACHE, encoding="utf-8")); _mem.update(ts=time.time(), data=d); return d
    except Exception:
        pass
    return rebuild(write=True)


def lookup(modo, side, hb=None):
    """Estimación más fina con n>=MIN_N. Devuelve dict o None si ni el lado tiene muestra."""
    if side not in ("LONG", "SHORT"):
        return None
    b = (table() or {}).get("buckets") or {}
    for lvl, k in zip(("L3", "L2", "L1"), keys_for(modo, side, hb or hour_bucket())):
        s = b.get(k)
        if s and s["n"] >= MIN_N:
            return {"key": k, "level": lvl, **s}
    return None


def for_row(row):
    side = row.get("dir_reco") or (row.get("verdict") or {}).get("side") or row.get("signal")
    return lookup(row.get("modo_reco"), side)


if __name__ == "__main__":
    d = rebuild()
    rows = sorted(d["buckets"].items(), key=lambda kv: (-kv[1]["p2"], -kv[1]["n"]))
    print("%-28s %5s %6s %6s %7s %5s" % ("bucket", "n", "p2%", "hit1%", "net", "días"))
    for k, s in rows:
        if s["n"] >= MIN_N:
            print("%-28s %5d %6.0f %6.0f %+7.2f %5d" % (k, s["n"], s["p2"], s["hit1"], s["net"], s["days"]))
