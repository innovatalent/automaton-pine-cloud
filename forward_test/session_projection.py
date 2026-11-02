# -*- coding: utf-8 -*-
"""PROYECCIÓN por SESIÓN (pedido usuario 2026-09-03): guardar los activos con CONFLUENCIA SOTT en TFs altas (≥2 de 1h/4h/1d)
y medir cómo se MUEVEN dentro de la PRÓXIMA sesión de Wall Street / Asia / Londres → así el panel muestra qué activos tienen
MEJOR PROYECCIÓN según desde dónde viene el SOTT/z. Reconstruye eventos históricos desde velas (determinístico) sobre grilla
de 1h (~41 días) → agrega por (lado × sesión) global y por activo → session_proj.json. El dashboard lo lee (rápido).
build(): reconstruye+guarda · load(): lee · project(asset,side): proyección por sesión. Correr: python session_projection.py"""
import os, sys, json, time
import urllib.request as _ur
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from direction_engine import sott_zone as SZ

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "session_proj.json")
DAY = 86400000
# Sesiones en UTC (aprox; el usuario opera por bloque horario): Asia 00-08, Londres 07-16, Wall Street (NY) 13:30-20:00.
SESSIONS = {"asia": (0.0, 8.0), "london": (7.0, 16.0), "ny": (13.5, 20.0)}
TFS = [("1h", "60", 1000), ("4h", "240", 1000), ("1d", "D", 400)]
TF_MS = {"1h": 3600000, "4h": 14400000, "1d": 86400000}
ZLB = 75


def _kl(sym, interval, limit):
    url = ("https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=%s&limit=%d" % (sym, interval, limit))
    try:
        d = json.load(_ur.urlopen(_ur.Request(url, headers={"User-Agent": "x"}), timeout=15))
        L = list(reversed(d.get("result", {}).get("list", []) or []))
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in L]
    except Exception:
        return None


def _src_series(h, l, c):
    if len(c) < SZ.NEW_PERIOD_K + 40:
        return None
    st = SZ._stoch(c, h, l, SZ.NEW_PERIOD_K)
    return [x + 1000.0 for x in SZ._var_func(st, SZ.NEW_SMOOTH_K)]


def _at(ts, starts, tf_ms, series):
    lo, hi, idx = 0, len(starts) - 1, -1
    while lo <= hi:
        m = (lo + hi) // 2
        if starts[m] + tf_ms <= ts:
            idx = m; lo = m + 1
        else:
            hi = m - 1
    return series[idx] if idx >= 0 else None


def _next_window(ts, sess):
    a, b = SESSIONS[sess]
    d0 = (ts // DAY) * DAY
    for k in range(0, 3):
        s = d0 + k * DAY + int(a * 3600000); e = d0 + k * DAY + int(b * 3600000)
        if s > ts:
            return s, e
    return None


def _last_window(now_ms, sess):
    """Última sesión de tipo `sess` YA COMPLETADA antes de ahora (para ver 'de dónde viene' la confluencia)."""
    a, b = SESSIONS[sess]
    d0 = (now_ms // DAY) * DAY
    for k in (0, -1, -2):
        s = d0 + k * DAY + int(a * 3600000); e = d0 + k * DAY + int(b * 3600000)
        if e <= now_ms:
            return s, e
    return None


def _trace_for(K1, S, now_ms):
    """Estado de la CONFLUENCIA SOTT (TFs altas 1h/4h/1d) durante la última sesión completada de cada tipo (WS/Asia/Londres)
    + cuánto se movió el precio en esa sesión. Devuelve lista cronológica: [{sess, side, agree, move}]. = 'de dónde viene'."""
    starts = [b[0] for b in K1]
    def _htf_at(i, ts):
        zs = []
        for lbl in ("1h", "4h", "1d"):
            Sx = S.get(lbl)
            if not Sx or Sx["src"] is None:
                zs.append(None); continue
            sv = Sx["src"][i] if lbl == "1h" else _at(ts, Sx["starts"], TF_MS[lbl], Sx["src"])
            zs.append("compra" if (sv is not None and sv < SZ.LOWER_BAND) else ("venta" if (sv is not None and sv > SZ.UPPER_BAND) else None))
        nc = zs.count("compra"); nv = zs.count("venta")
        side = "LONG" if nc > nv else ("SHORT" if nv > nc else None)
        return side, max(nc, nv)
    out = []
    for sess in ("asia", "london", "ny"):
        w = _last_window(now_ms, sess)
        if not w:
            continue
        s, e = w
        idx = None; px_s = None
        for i in range(len(starts)):
            if s <= starts[i] <= e:
                if px_s is None:
                    px_s = K1[i][4]
                idx = i
        if idx is None:
            continue
        side, agree = _htf_at(idx, starts[idx])
        px_e = K1[idx][4]
        mv = ((px_e - px_s) / px_s * 100) if px_s else None
        out.append({"sess": sess, "side": side, "agree": agree, "move": round(mv, 2) if mv is not None else None, "e": e})
    out.sort(key=lambda x: x["e"])
    return out


class Agg:
    __slots__ = ("net", "mfe", "hit", "n")
    def __init__(self): self.net = 0.0; self.mfe = 0.0; self.hit = 0; self.n = 0
    def add(self, net, mfe):
        self.net += net; self.mfe += mfe; self.n += 1
        if mfe >= 1.0:
            self.hit += 1
    def out(self):
        if not self.n:
            return None
        return {"n": self.n, "net": round(self.net / self.n, 2), "mfe": round(self.mfe / self.n, 2),
                "hit": round(100.0 * self.hit / self.n)}


def _blank():
    return {"LONG": {s: Agg() for s in SESSIONS}, "SHORT": {s: Agg() for s in SESSIONS}}


def build():
    U = SZ.roster()
    glob = _blank(); by_asset = {}; traces = {}; nev = 0; nas = 0
    for sym in U:
        K1 = _kl(sym, "60", 1000)
        if not K1 or len(K1) < SZ.NEW_PERIOD_K + 60:
            continue
        S = {}
        okall = True
        for lbl, code, lim in TFS:
            K = K1 if lbl == "1h" else _kl(sym, code, lim)
            if not K or len(K) < SZ.NEW_PERIOD_K + 40:
                if lbl == "1d":
                    S[lbl] = None; continue
                okall = False; break
            h = [b[2] for b in K]; l = [b[3] for b in K]; c = [b[4] for b in K]
            S[lbl] = {"starts": [b[0] for b in K], "src": _src_series(h, l, c)}
        if not okall or S.get("1h") is None or S["1h"]["src"] is None:
            continue
        nas += 1
        traces[sym] = _trace_for(K1, S, int(time.time() * 1000))   # 'de dónde viene': estado de confluencia en la última sesión de cada tipo
        ba = by_asset.setdefault(sym, _blank())
        prev_side = None
        for i in range(len(K1)):
            ts = K1[i][0]; px = K1[i][4]
            zones = []
            for lbl in ("1h", "4h", "1d"):
                Sx = S.get(lbl)
                if not Sx or Sx["src"] is None:
                    zones.append(None); continue
                sv = Sx["src"][i] if lbl == "1h" else _at(ts, Sx["starts"], TF_MS[lbl], Sx["src"])
                zones.append("compra" if (sv is not None and sv < SZ.LOWER_BAND) else ("venta" if (sv is not None and sv > SZ.UPPER_BAND) else None))
            nc = zones.count("compra"); nv = zones.count("venta")
            side = "LONG" if nc >= 2 else ("SHORT" if nv >= 2 else None)   # confluencia FUERTE en TFs altas (>=2 de 3)
            if side and side != prev_side:                                  # EVENTO = arranque de la confluencia (no repite)
                nev += 1
                for sess in SESSIONS:
                    w = _next_window(ts, sess)
                    if not w:
                        continue
                    ws, we = w
                    seg = [b for b in K1 if ws <= b[0] <= we]
                    if not seg:
                        continue
                    up = side == "LONG"
                    mfe = max(((b[2] - px) / px * 100) if up else ((px - b[3]) / px * 100) for b in seg)
                    net = ((seg[-1][4] - px) / px * 100) * (1 if up else -1)
                    glob[side][sess].add(net, mfe)
                    ba[side][sess].add(net, mfe)
            prev_side = side
    def _dump(blk):
        return {sd: {s: blk[sd][s].out() for s in SESSIONS if blk[sd][s].out()} for sd in ("LONG", "SHORT")}
    res = {"built_ts": int(time.time()), "n_events": nev, "n_assets": nas,
           "global": _dump(glob),
           "by_asset": {a: _dump(v) for a, v in by_asset.items()},
           "traces": traces}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f)
    os.replace(tmp, OUT)
    return res


_CACHE = {"ts": 0.0, "data": None}


def load():
    """Lee session_proj.json (cache 5min por mtime)."""
    try:
        mt = os.path.getmtime(OUT)
    except Exception:
        return None
    if _CACHE["data"] is not None and _CACHE["ts"] == mt:
        return _CACHE["data"]
    try:
        with open(OUT, encoding="utf-8") as f:
            _CACHE["data"] = json.load(f); _CACHE["ts"] = mt
    except Exception:
        return None
    return _CACHE["data"]


def project(asset, side):
    """Proyección por sesión para (asset, side): usa stats del activo si n>=3, si no las globales. {sess:{n,net,mfe,hit}}."""
    d = load()
    if not d or side not in ("LONG", "SHORT"):
        return None
    g = (d.get("global") or {}).get(side, {})
    a = ((d.get("by_asset") or {}).get(asset) or {}).get(side, {})
    out = {}
    for sess in SESSIONS:
        st = a.get(sess) if (a.get(sess) and a[sess].get("n", 0) >= 3) else g.get(sess)
        if st:
            out[sess] = st
    return out or None


def session_trace(asset):
    """'De dónde viene': estado de la confluencia SOTT (TFs altas) en la última sesión completada de cada tipo + el move
    de esa sesión. Lista cronológica [{sess, side, agree, move}]. None si no hay data."""
    d = load()
    if not d:
        return None
    return (d.get("traces") or {}).get(asset)


if __name__ == "__main__":
    r = build()
    print("session_proj.json construido: %d eventos · %d activos" % (r["n_events"], r["n_assets"]))
    print("\n=== PROYECCIÓN GLOBAL por (lado × sesión) — move NETO en la dirección durante la próxima sesión ===")
    for sd in ("LONG", "SHORT"):
        for s in SESSIONS:
            st = r["global"].get(sd, {}).get(s)
            if st:
                print("  %-5s %-7s n=%-4d net=%+.2f%% mfe=%.2f%% hit+1%%=%2d%%" % (sd, s, st["n"], st["net"], st["mfe"], st["hit"]))
