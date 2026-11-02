# -*- coding: utf-8 -*-
"""__ESTRATEGIA_SIGNALS_2026_10_01__ — señal operable de la estrategia SWING por moneda, para el Pine.

Expone la señal del edge validado (divergencia ballenas-vs-retail, spec final z>=1.5, hold 5d) por
moneda, para operativa MANUAL. Es señal DIARIA (z-score 90d de posicionamiento); cache 1h.

NO es intradía: la posición se abre en D+1 y se sostiene ~5 días. El Pine la muestra como CONTEXTO
operable (de qué lado estar hoy), no como gatillo de 5m. Datos: binance_vision.db (metrics Binance),
mismas 16 majors donde el edge valida. Sin red (lectura local).
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.path.dirname(HERE), "forward_test", "binance_vision.db")
UMBRAL, VENTANA = 1.5, 90
_TTL = 3600.0
_cache = {"ts": 0.0, "data": None}


def _zscore(vals, n):
    out = [None] * len(vals)
    for i in range(len(vals)):
        if i < n:
            continue
        w = vals[i - n:i]
        m = sum(w) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
        out[i] = (vals[i] - m) / sd if sd > 0 else 0.0
    return out


def _signal_coin(con, coin):
    """(side, z_spread) de HOY para la moneda, o None. side: +1 LONG / -1 SHORT / 0 neutral."""
    sym = "%sUSDT" % coin
    rows = con.execute("SELECT ts, tt_ratio_tamano, ls_ratio_cuentas FROM metrics "
                       "WHERE symbol=? ORDER BY ts", (sym,)).fetchall()
    if len(rows) < VENTANA + 5:
        return None
    # última muestra por día (causal)
    by_day = {}
    for ts, tt, ls in rows:
        d = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        by_day[d] = (tt, ls)
    dias = sorted(by_day)
    zW = _zscore([by_day[d][0] for d in dias], VENTANA)
    zR = _zscore([by_day[d][1] for d in dias], VENTANA)
    if zW[-1] is None or zR[-1] is None:
        return None
    z = zW[-1] - zR[-1]
    side = 1 if z >= UMBRAL else (-1 if z <= -UMBRAL else 0)
    return side, round(z, 2)


def todas(coins):
    """{coin: (side, z)} cacheado 1h. coins = lista de claves ('BTC','ETH',...)."""
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < _TTL:
        return _cache["data"]
    out = {}
    if os.path.exists(DB):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True, timeout=30)
            con.execute("PRAGMA busy_timeout=30000")
            for c in coins:
                try:
                    s = _signal_coin(con, c)
                    if s is not None:
                        out[c] = s
                except Exception:
                    pass
            con.close()
        except Exception:
            pass
    _cache["ts"], _cache["data"] = now, out
    return out


if __name__ == "__main__":
    import sys
    cs = (sys.argv[1].split(",") if len(sys.argv) > 1
          else ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC",
                "BCH", "DOT", "ATOM", "UNI", "ETC", "FIL"])
    for c, (side, z) in sorted(todas(cs).items()):
        print("%-6s %-7s z=%+.2f" % (c, "LONG" if side > 0 else "SHORT" if side < 0 else "neutral", z))
