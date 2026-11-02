# -*- coding: utf-8 -*-
"""
gex_equity.py — GEX de opciones de RENTA VARIABLE (QQQ, SPY, NVDA...) reutilizando
el motor de gex_engine.py. Pensado para operar TQQQ con el regimen del Nasdaq.

POR QUE QQQ Y NO TQQQ
    Medido en vivo 2026-09-08, 6 vencimientos cercanos:

        QQQ   1181 contratos   OI 944.654   nocional  67,9 B USD
        TQQQ   523 contratos   OI 605.605   nocional   4,4 B USD

    QQQ es 15 veces mas grande. Y sobre todo, los creadores de mercado de TQQQ NO
    cubren en TQQQ: cubren en QQQ y en futuros del NDX. La gamma que realmente
    frena o acelera al complejo Nasdaq esta en QQQ (y en SPX/NDX), asi que el
    regimen para operar TQQQ se lee ahi.

DIFERENCIA CLAVE CON CRIPTO
    En cripto el contrato esta denominado en la moneda base (multiplicador 1). En
    acciones el contrato son 100 ACCIONES. Por eso aca oi_coin = openInterest * 100:
    son las acciones del subyacente que el dealer debe cubrir.

COMO SE TRADUCE A TQQQ
    NO se mapean niveles absolutos. TQQQ replica 3x el rendimiento DIARIO de QQQ,
    asi que por el reseteo diario y el decaimiento por volatilidad el precio de
    TQQQ no tiene una correspondencia estable con un strike de QQQ.

    Lo que SI se traduce es la DISTANCIA en porcentaje, multiplicada por 3:

        QQQ a +1,0% de su gamma flip  ->  TQQQ a ~+3,0% del equivalente

    Y el regimen (positivo/negativo) se lee tal cual, porque es una propiedad del
    mercado, no del instrumento.

ADEMAS: EL REBALANCEO DEL PROPIO TQQQ
    Un ETF apalancado 3x tiene que comprar fuerza y vender debilidad cerca del
    cierre para mantener su exposicion. Eso es un flujo de gamma NEGATIVA propio,
    independiente de las opciones, y amplifica los movimientos de la ultima media
    hora. Se estima con `rebalance_usd()`: es informativo, no una senal.

USO
    python -X utf8 gex_equity.py            # QQQ + lectura para TQQQ
    python -X utf8 gex_equity.py SPY NVDA
"""

from __future__ import annotations

import math
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gex_engine import OptionRow, build_state          # noqa: E402

CONTRACT_SHARES = 100          # 1 contrato de opcion de acciones = 100 acciones
# __EXPIRIES_2026_09_08__ — medido contra el open interest OFICIAL de la OCC
# (marketdata.theocc.com/series-search, gratis y sin clave):
#   yfinance capta el 100% del OI de la OCC en los vencimientos que baja, pero con
#   8 vencimientos eso era solo el 7% del OI total de QQQ (12,9 M de contratos en
#   32 vencimientos, hasta 2028-12).
# Efecto medido al pasar de 8 a 32 vencimientos:
#   GEX SCALP (<=7 DTE)  +975.3 -> +960.5   (1,5%: no cambia, es lo que manda el panel)
#   GEX ESTRUCTURAL     +2183.3 ->  +724.7   (estaba 3 veces inflado)
#   VEX                  +214.9 -> +3798.5   (17x: la vanna vive en los plazos largos)
#   CEX                 -1247.6 -> -2291.8
# Se sube a 24 como compromiso: recupera casi todo el efecto sin que cada refresco
# de yfinance tarde eternidades (el cache es de 10 min).
MAX_EXPIRIES = 24              # vencimientos a agregar
_CACHE = {}
_TTL = 600.0                   # yfinance es lento y el OI se publica 1 vez por dia


def _expiry_ts(datestr):
    """'2026-09-19' -> epoch del cierre (21:00 UTC ~ 16:00 ET, vencen al cierre)."""
    y, m, d = (int(x) for x in datestr.split("-"))
    import datetime as dt
    return dt.datetime(y, m, d, 21, 0, 0, tzinfo=dt.timezone.utc).timestamp()


def fetch_equity(ticker="QQQ", max_exp=MAX_EXPIRIES, dte_max=None):
    """Cadena de opciones de una accion/ETF via yfinance -> lista de OptionRow."""
    import yfinance as yf
    t = yf.Ticker(ticker)
    try:
        spot = float(t.fast_info.get("lastPrice") or 0)
    except Exception:
        spot = 0.0
    if spot <= 0:
        try:
            spot = float(t.history(period="1d")["Close"].iloc[-1])
        except Exception:
            return [], 0.0
    now = time.time()
    rows = []
    for e in (t.options or [])[:max_exp]:
        ets = _expiry_ts(e)
        if ets <= now:
            continue
        if dte_max is not None and (ets - now) / 86400.0 > dte_max:
            continue
        try:
            ch = t.option_chain(e)
        except Exception:
            continue
        for df, is_call in ((ch.calls, True), (ch.puts, False)):
            for k, oi, iv in zip(df["strike"], df["openInterest"].fillna(0),
                                 df["impliedVolatility"].fillna(0)):
                try:
                    oi = float(oi); iv = float(iv); k = float(k)
                except (TypeError, ValueError):
                    continue
                if oi <= 0 or iv <= 0 or k <= 0:
                    continue
                rows.append(OptionRow(
                    exchange="occ", coin=ticker, strike=k, is_call=is_call,
                    expiry_ts=ets, oi_coin=oi * CONTRACT_SHARES, iv=iv,
                    underlying=spot))
    return rows, spot


def state(ticker="QQQ", dte_max=None, dte_ramp_days=0.0, cache=True):
    key = (ticker, dte_max)
    if cache:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < _TTL:
            return hit[1]
    rows, spot = fetch_equity(ticker, dte_max=dte_max)
    if not rows:
        return None
    st = build_state(ticker, rows=rows, dte_max=dte_max,
                     dte_ramp_days=dte_ramp_days)
    if cache:
        _CACHE[key] = (time.time(), st)
    return st


def rebalance_usd(ticker="TQQQ", lev=3.0):
    """
    Estimacion del rebalanceo diario del ETF apalancado, en USD por cada 1% de
    movimiento del indice. Un 3x debe comprar fuerza y vender debilidad cerca del
    cierre: es gamma NEGATIVA estructural, propia del vehiculo.

        flujo ~ AUM * lev * (lev - 1) * mov

    Es una aproximacion: el AUM de yfinance puede venir vacio o desactualizado.
    Informativo, NO una senal.
    """
    import yfinance as yf
    try:
        t = yf.Ticker(ticker)
        aum = None
        for k in ("totalAssets", "netAssets"):
            v = (t.info or {}).get(k)
            if v:
                aum = float(v); break
        if not aum:
            return None
        return {"aum_usd": aum, "lev": lev,
                "usd_por_1pct": aum * lev * (lev - 1) * 0.01}
    except Exception:
        return None


def lectura_tqqq(st_qqq, mult=3.0):
    """Traduce el regimen de QQQ a la lectura para operar TQQQ."""
    if st_qqq is None:
        return None
    d = st_qqq.dist_flip_pct
    pos = st_qqq.regimen == "POSITIVO"
    cerca = d is not None and abs(d) <= 0.5
    if cerca:
        modo = "BISAGRA: regimen indefinido, reducir tamano"
    elif pos:
        modo = "PINNING: el fade a la media tiene el flujo a favor; las rupturas mueren"
    else:
        modo = "VIOLENTA: los dealers persiguen; favorece continuacion y barrido"
    return {
        "regimen": st_qqq.regimen,
        "estado": st_qqq.velocidad_esperada,
        "dist_flip_qqq_pct": d,
        "dist_flip_tqqq_pct": (d * mult) if d is not None else None,
        "call_wall_qqq": st_qqq.call_wall,
        "put_wall_qqq": st_qqq.put_wall,
        "dist_call_wall_x_pct": ((st_qqq.call_wall - st_qqq.spot) / st_qqq.spot * 100 * mult)
                                   if st_qqq.call_wall else None,
        "dist_put_wall_x_pct": ((st_qqq.put_wall - st_qqq.spot) / st_qqq.spot * 100 * mult)
                                  if st_qqq.put_wall else None,
        "lectura": modo,
    }


def main():
    tickers = sys.argv[1:] or ["QQQ"]
    for tk in tickers:
        st = state(tk, dte_max=7.0, dte_ramp_days=1.0)
        if st is None:
            print("%s: sin cadena" % tk); continue
        print("=" * 74)
        print("%s  spot %.2f" % (tk, st.spot))
        print("=" * 74)
        print("  regimen %s   %+.1f M$ por 1%%   estado %s"
              % (st.regimen, st.net_gex_musd, st.velocidad_esperada))
        print("  gamma flip %.2f (%+.2f%% del spot)"
              % (st.gamma_flip or 0, st.dist_flip_pct or 0))
        print("  call wall %.2f   put wall %.2f   max|GEX| %.2f"
              % (st.call_wall or 0, st.put_wall or 0, st.max_abs_strike or 0))
        print("  %d contratos · OI %.0f acciones · vto mas proximo en %.1f h (%.0f%% del |GEX|)"
              % (st.n_contratos, st.oi_total_coin,
                 st.proximo_vto_horas or 0, (st.proximo_vto_share_abs or 0) * 100))
        for w in st.warnings:
            print("  ! " + w)

        if tk.upper() == "QQQ":
            L = lectura_tqqq(st)
            print()
            print("  ---- LECTURA PARA OPERAR TQQQ (3x QQQ) ----")
            print("  %s" % L["lectura"])
            print("  distancia al flip: QQQ %+.2f%%  ->  TQQQ ~%+.2f%%"
                  % (L["dist_flip_qqq_pct"] or 0, L["dist_flip_tqqq_pct"] or 0))
            print("  techo (call wall QQQ %.2f):  TQQQ ~%+.2f%% de aca"
                  % (L["call_wall_qqq"] or 0, L["dist_call_wall_x_pct"] or 0))
            print("  piso  (put wall QQQ %.2f):   TQQQ ~%+.2f%% de aca"
                  % (L["put_wall_qqq"] or 0, L["dist_put_wall_x_pct"] or 0))
            rb = rebalance_usd("TQQQ")
            if rb:
                print("  rebalanceo diario del propio TQQQ: ~%.0f M$ por cada 1%% del indice"
                      % (rb["usd_por_1pct"] / 1e6))
                print("  (gamma NEGATIVA estructural del vehiculo: compra fuerza y vende")
                print("   debilidad cerca del cierre, amplifica la ultima media hora)")
            print()
            print("  OJO: no se mapean NIVELES absolutos. TQQQ replica 3x el rendimiento")
            print("  DIARIO, asi que por el reseteo y el decaimiento no hay correspondencia")
            print("  estable entre un strike de QQQ y un precio de TQQQ. Se traduce la")
            print("  DISTANCIA en porcentaje (x3) y el REGIMEN, que es propiedad del mercado.")
        print()


if __name__ == "__main__":
    main()
