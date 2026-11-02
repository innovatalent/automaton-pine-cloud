# -*- coding: utf-8 -*-
"""__RETAIL_WHALE_2026_09_28__ — posicionamiento RETAIL vs BALLENAS, diferenciados.

Pedido usuario: ver dónde está el retail y dónde las ballenas, CLARAMENTE distintos, para operar
A FAVOR de las ballenas y EN CONTRA del retail. El edge validado (bt_posicionamiento) es fadear la
multitud; seguir a las ballenas es el complemento.

FUENTES (públicas, sin clave):
  RETAIL  = % de CUENTAS long (cada cuenta 1 voto; dominado por retail). Binance
            globalLongShortAccountRatio (+ Bybit account-ratio buyRatio de respaldo/cross-check).
  BALLENAS= % long por TAMAÑO de posición entre top-traders. Binance topLongShortPositionRatio.
  (OKX se puede sumar para enriquecer el retail de majors; Binance ya trae ambos lados.)

OJO honesto: este dato es un producto de los exchanges de perp CRIPTO. Verificado 2026-10-11:
Binance y OKX SÍ publican los ratios de los perps de acciones/metales tokenizados (AAPLUSDT,
TQQQUSDT, XAUUSDT, CLUSDT...), así que hay dato; pero es la multitud de ESE perp (OI chico),
no el posicionamiento del mercado real de la acción. Si una fuente no responde, queda n/d.
No se inventa.

Devuelve por símbolo: retail_long%, whale_long%, y el lado NETO (seguir ballenas / fadear retail),
marcando DIVERGENCIA (ballenas de un lado, retail del otro = setup más limpio).

__BASELINE_2026_10_11__ El %long absoluto NO se lee contra 50: el ratio de ballenas está
estructuralmente long (marca LONG el 93 % de los días, E=+0,002 %/op, t=0,02 — o sea nada).
Se lee contra su propia MEDIANA de 90 días (E=+0,664 %/op, t=5,74, 23/25 símbolos). Eso lo
aporta pos_baseline.py y viaja en base_side / whale_med / z_spread.
"""
from __future__ import annotations

import json
import time
import urllib.request as _ur

_CACHE = {}
_TTL = 300.0
_BINANCE = "https://fapi.binance.com/futures/data"
_UA = {"User-Agent": "atmn-rw/1.0"}


def _get(url):
    return json.load(_ur.urlopen(_ur.Request(url, headers=_UA), timeout=10))


def _binance_ratio(path, symbol):
    """longAccount (fracción long) del último dato, o None."""
    try:
        d = _get("%s/%s?symbol=%s&period=1h&limit=1" % (_BINANCE, path, symbol))
        if d and isinstance(d, list):
            return float(d[-1].get("longAccount"))
    except Exception:
        return None
    return None


def _bybit_retail(symbol):
    """buyRatio (fracción long de cuentas) de Bybit, o None. Cubre símbolos que Binance no lista."""
    try:
        d = _get("https://api.bybit.com/v5/market/account-ratio?category=linear&symbol=%s&period=1h&limit=1"
                 % symbol)
        lst = (d.get("result") or {}).get("list") or []
        if lst:
            return float(lst[0]["buyRatio"])
    except Exception:
        return None
    return None


def _okx_retail(coin):
    """Long/short de CUENTAS de OKX por moneda (cripto). Devuelve fracción long, o None.
    OKX da el RATIO long/short; fracción long = r/(1+r). Solo cripto (ccy), no acciones/commodities."""
    try:
        d = _get("https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=%s&period=1H"
                 % coin)
        rows = d.get("data") or []
        if rows and d.get("code") == "0":
            r = float(rows[0][1])          # rows viene nuevo->viejo; [ts, ratio]
            if r > 0:
                return r / (1.0 + r)
    except Exception:
        return None
    return None


def snapshot(symbol):
    """{retail_long, whale_long (0-100), retail_side, whale_side, diverge, net_side, strength, src}."""
    now = time.time()
    hit = _CACHE.get(symbol)
    if hit and now - hit[0] < _TTL:
        return hit[1]

    coin = symbol[:-4] if symbol.endswith("USDT") else symbol
    whale = _binance_ratio("topLongShortPositionRatio", symbol)     # ballenas (top por tamaño)
    # RETAIL CRUZADO: promedio de las fuentes de CUENTAS disponibles (Binance + OKX). Bybit de
    # respaldo si ninguna de las dos principales responde. Cruzar fuentes = lectura mas robusta.
    _b = _binance_ratio("globalLongShortAccountRatio", symbol)      # cuentas Binance
    _o = _okx_retail(coin)                                          # cuentas OKX (cripto)
    cand = [x for x in (_b, _o) if x is not None]
    fuentes = [n for n, v in (("bin", _b), ("okx", _o)) if v is not None]
    if not cand:
        _y = _bybit_retail(symbol)
        if _y is not None:
            cand.append(_y); fuentes.append("bybit")
    retail = sum(cand) / len(cand) if cand else None
    nsrc = len(cand)
    src = "+".join(fuentes) if fuentes else "n/d"

    out = {"retail_long": None, "whale_long": None, "retail_side": None, "whale_side": None,
           "diverge": False, "net_side": None, "strength": 0, "src": src, "nsrc": nsrc,
           "base_side": None, "whale_med": None, "z_spread": None, "base_dias": 0,
           "retail_med": None, "zw": None, "zr": None}
    if retail is not None:
        out["retail_long"] = round(retail * 100, 1)
        out["retail_side"] = "LONG" if retail >= 0.5 else "SHORT"
    if whale is not None:
        out["whale_long"] = round(whale * 100, 1)
        out["whale_side"] = "LONG" if whale >= 0.5 else "SHORT"

    # lado a operar: A FAVOR de ballenas, EN CONTRA de retail. Si ambos coinciden, se suma la fuerza.
    fade_retail = None
    if out["retail_side"]:
        fade_retail = "SHORT" if out["retail_side"] == "LONG" else "LONG"
    follow_whale = out["whale_side"]

    votos = [x for x in (fade_retail, follow_whale) if x]
    if votos:
        longs = votos.count("LONG"); shorts = votos.count("SHORT")
        if longs != shorts:
            out["net_side"] = "LONG" if longs > shorts else "SHORT"
    # divergencia = ballenas de un lado y retail del MISMO lado que las ballenas NO (o sea retail opuesto a ballenas)
    if out["retail_side"] and out["whale_side"]:
        out["diverge"] = out["retail_side"] != out["whale_side"]
    # fuerza 0-3: extremo de retail + extremo de ballenas + divergencia
    st = 0
    if out["retail_long"] is not None and (out["retail_long"] >= 62 or out["retail_long"] <= 42):
        st += 1
    if out["whale_long"] is not None and (out["whale_long"] >= 58 or out["whale_long"] <= 42):
        st += 1
    if out["diverge"]:
        st += 1
    out["strength"] = st

    # __BASELINE_2026_10_11__ lado contra la linea base PROPIA (lo unico con contenido
    # direccional medido). Si no hay 90 dias de historia propia queda None = sin dato,
    # que es mejor que el LONG falso que daba el umbral 50.
    try:
        from direction_engine import pos_baseline as _pb
        _ld, _b = _pb.lado(symbol, out["whale_long"])
        if _b:
            out["z_spread"], out["base_dias"] = _b["z"], _b["dias"]
            if _ld:
                # la mediana solo se publica si hay lado: "vs base 67%L => sin base"
                # (dato vivo caido con historia presente) seria texto contradictorio
                out["base_side"] = "LONG" if _ld > 0 else "SHORT"
                out["whale_med"] = _b["med_w"]
            # __ZW_ZR_SEPARADOS_2026_10_19__ van SIEMPRE que existan, con o sin lado: la
            # mediana del retail y las dos z por separado no dependen del lado de ballenas.
            out["retail_med"] = _b.get("med_r")
            out["zw"] = _b.get("zw")
            out["zr"] = _b.get("zr")
    except Exception:
        pass

    _CACHE[symbol] = (now, out)
    return out


if __name__ == "__main__":
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "TQQQUSDT", "CLUSDT", "XAUUSDT", "TAOUSDT"):
        r = snapshot(s)
        print("%-9s retail=%s%%L(%s) ballenas=%s%%L(%s) neto=%s div=%s str=%d [%s]\n"
              "          base=%s (mediana90 %s%%L, z-spread %s, %d dias)" % (
                  s.replace("USDT", ""), r["retail_long"], r["retail_side"],
                  r["whale_long"], r["whale_side"], r["net_side"], r["diverge"], r["strength"], r["src"],
                  r["base_side"], r["whale_med"], r["z_spread"], r["base_dias"]))
