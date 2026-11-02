# -*- coding: utf-8 -*-
"""DERIVADOS EN VIVO — Coinalyze (2026-08-08, pedido usuario "cablear liquidaciones + OI").

La dirección NO está en el OHLCV (validado toda la sesión). Lo único que puede darla es el FLUJO forzado: dónde se
liquidan posiciones (cascadas) y hacia dónde crece el interés abierto. Este módulo trae, por activo:
  - open_interest (USD)  : tamaño del apalancamiento vivo.
  - liquidaciones 1h     : long_usd (largos reventados = presión de VENTA/capitulación) vs short_usd (cortos reventados
                           = squeeze/presión de COMPRA). El SKEW = quién está siendo forzado a salir.

DISCIPLINA (como todo esta sesión): se EXPONE la señal para RECORD + VALIDACIÓN forward antes de gatear a ciegas.
Usa Binance perp (exchange 'A' = el más líquido). Cache generoso. Fail-open (devuelve None si no hay dato).
"""
import json
import time
import urllib.request
import urllib.parse

_BASE = "https://api.coinalyze.net/v1"
_UA = {"User-Agent": "mozilla/5.0"}
_KEY = {"v": None, "ts": 0.0}
_KEYS_PATHS = [r"C:\Users\asusr\OneDrive\Desktop\data_keys.json"]
_mkt = {"map": None, "ts": 0.0}
_cache = {}


def _key():
    now = time.time()
    if _KEY["v"] is not None and now - _KEY["ts"] < 300:
        return _KEY["v"]
    k = None
    for p in _KEYS_PATHS:
        try:
            k = json.load(open(p, encoding="utf-8")).get("coinalyze")
            if k:
                break
        except Exception:
            continue
    _KEY["v"] = k or ""
    _KEY["ts"] = now
    return _KEY["v"]


def _get(path, params):
    k = _key()
    if not k:
        return None
    url = _BASE + path + "?" + urllib.parse.urlencode(params)
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"api_key": k, **_UA}), timeout=18)
        return json.loads(r.read().decode())
    except Exception:
        return None


def _base(sym):
    for suf in ("USDT", "USDC", "USD"):
        if sym.endswith(suf):
            return sym[:-len(suf)]
    return sym


def _cz_symbol(sym):
    """Mapea 'BTCUSDT' -> símbolo perp Binance de Coinalyze ('BTCUSDT_PERP.A'). Cache 6h de los mercados válidos."""
    now = time.time()
    if _mkt["map"] is None or now - _mkt["ts"] > 6 * 3600:
        m = {}
        data = _get("/future-markets", {})
        if isinstance(data, list):
            for row in data:
                if row.get("exchange") == "A" and row.get("is_perpetual") and row.get("quote_asset") in ("USDT", "USD"):
                    b = row.get("base_asset")
                    s = row.get("symbol") or ""
                    # preferir el USDT_PERP sobre coin-margined
                    if b and (b not in m or "USDT" in s):
                        m[b] = s
        _mkt["map"] = m
        _mkt["ts"] = now
    return (_mkt["map"] or {}).get(_base(sym))


def signal(sym):
    """{oi_usd, liq_long_1h, liq_short_1h, liq_skew, liq_total} para el activo. liq_skew>0 = squeeze de cortos (sesgo
    ALCISTA: los cortos se revientan); <0 = largos reventados (sesgo BAJISTA). None si no hay dato. Cache 4min."""
    now = time.time()
    c = _cache.get(sym)
    if c and now - c[1] < 240:
        return c[0]
    cz = _cz_symbol(sym)
    if not cz:
        _cache[sym] = (None, now)
        return None
    res = {"oi_usd": None, "liq_long_1h": 0.0, "liq_short_1h": 0.0, "liq_skew": 0.0, "liq_total": 0.0}
    oi = _get("/open-interest", {"symbols": cz, "convert_to_usd": "true"})
    if isinstance(oi, list) and oi:
        try:
            res["oi_usd"] = float(oi[0].get("value") or 0)
        except Exception:
            pass
    frm = int(now) - 3600
    lq = _get("/liquidation-history", {"symbols": cz, "interval": "5min", "from": frm, "to": int(now),
                                       "convert_to_usd": "true"})
    if isinstance(lq, list) and lq:
        hist = lq[0].get("history") or []
        for h in hist:
            try:
                res["liq_long_1h"] += float(h.get("l") or 0)   # longs liquidados
                res["liq_short_1h"] += float(h.get("s") or 0)  # shorts liquidados
            except Exception:
                continue
    tot = res["liq_long_1h"] + res["liq_short_1h"]
    res["liq_total"] = tot
    if tot > 0:
        # skew: (shorts liquidados - longs liquidados) / total -> +1 puro squeeze de cortos (alcista), -1 largos (bajista)
        res["liq_skew"] = round((res["liq_short_1h"] - res["liq_long_1h"]) / tot, 3)
    _cache[sym] = (res, now)
    return res


if __name__ == "__main__":
    print("key:", "OK" if _key() else "FALTA")
    for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "TAOUSDT"]:
        print("%-9s cz=%-16s %s" % (s, _cz_symbol(s), signal(s)))
