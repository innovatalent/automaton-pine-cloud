"""
gex_live.py — capa de cache EN PROCESO del motor GEX para el dashboard (8890).

Por que en proceso y no como servicio HTTP aparte:
  1. gex_service.py venia con --port 8899 por defecto, que en este repo es el
     MUTEX de instancia unica de forward_runner.py (linea 6361). Si el servicio
     tomaba el puerto primero, el bot se negaba a arrancar en silencio.
  2. El dashboard ya tiene el patron de hilo refrescador (_vp_refresher,
     _stock_liq_refresher). Un proceso menos que vigilar.
  3. Sin round-trip HTTP: el panel de extremos valida sus filas en microsegundos.

COBERTURA — por que solo BTC y ETH:
  Deribit concentra ~85-89% del OI de opciones cripto. Verificado en vivo
  2026-09-07: BTC deribit 428.101 / okx 32.583 / bybit 17.916 (89,4% / 6,8% /
  3,7%). Para SOL y XRP, Deribit devuelve 0 y OKX corta con HTTP 400: solo queda
  Bybit, o sea el ~5% del mercado. Un GEX sobre esa base describe un libro
  retail, no la cobertura agregada de los dealers, asi que NO se calcula.
  Para el resto del roster, el regimen de BTC se expone como CONTEXTO de mercado
  (informativo, sin veto).
"""

from __future__ import annotations

import json
import os
import threading
import time

COINS = ("BTC", "ETH")
VIEWS = {
    "scalp":       dict(dte_max=7.0,  dte_ramp_days=1.0),   # la gamma que pesa hoy (1m/5m)
    "estructural": dict(dte_max=None, dte_ramp_days=0.0),   # niveles de sesion/semana
}
EXCHANGES = ("deribit", "bybit", "okx")

REFRESH_S = 60      # el OI de opciones no se mueve mas rapido que esto
STALE_S = 300       # a partir de aca el snapshot se marca viejo

_cache = {}
_errors = {}
_lock = threading.Lock()
_started = False

_LOG = os.path.join(os.path.dirname(__file__), "gex_signal_log.jsonl")

# symbol del roster -> moneda con cadena de opciones representativa
_MAP = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}

# Simbolos de RENTA VARIABLE cuyo regimen se lee del subyacente real, no del propio
# instrumento: TQQQ es 3x QQQ y sus creadores de mercado cubren en QQQ / futuros NDX.
# valor = (ticker del subyacente, multiplicador de apalancamiento)
EQUITY_MAP = {"TQQQUSDT": ("QQQ", 3.0)}

# __STOCK_GEX_2026_09_08__ — ACCIONES con cadena de opciones PROPIA.
# A diferencia de TQQQ (que hereda de QQQ porque su libro es 15x menor), estas
# tienen su propio mercado de opciones y sus strikes ya estan en su precio, asi
# que la gamma se lee directo. Verificado en vivo: las 16 devuelven entre 85 y 730
# contratos con OI e IV. El caso que muestra el valor: MSFT en gamma NEGATIVA /
# VIOLENTA cuando las otras 15 estan en PINNING, o sea que ahi NO corresponde fade.
# El multiplicador es 1.0: no son apalancadas, el token replica al subyacente.
STOCK_GEX = {
    "NVDAUSDT": "NVDA", "TSLAUSDT": "TSLA", "AAPLUSDT": "AAPL", "METAUSDT": "META",
    "MSTRUSDT": "MSTR", "COINUSDT": "COIN", "SOXLUSDT": "SOXL", "PLTRUSDT": "PLTR",
    "AMDUSDT": "AMD", "GOOGLUSDT": "GOOGL", "AMZNUSDT": "AMZN", "MSFTUSDT": "MSFT",
    "HOODUSDT": "HOOD", "MUUSDT": "MU", "NFLXUSDT": "NFLX", "AVGOUSDT": "AVGO",
}

# MATERIAS PRIMAS via el ETF con opciones liquidas. valor = (ticker, multiplicador)
COMMODITY_MAP = {"XAUUSDT": ("GLD", 1.0), "CLUSDT": ("USO", 1.0)}

# Calidad del dato por ticker. NO es cosmetico: el aviso exige mas cuando la
# cobertura es debil, porque el libro del ETF no es el libro que mueve al mercado.
COBERTURA = {
    "QQQ":  ("dominante", "libro que mueve al Nasdaq (67,9 B de nocional)"),
    "TQQQ": ("propio", "libro chico (4,4 B) pero sus strikes ya estan en precio de TQQQ"),
    "GLD":  ("parcial", "9,0 B; las opciones sobre futuros de COMEX son mayores y no estan aca"),
    "USO":  ("debil", "6,7 B; el crudo se opera sobre todo en opciones CME, que no estan aca"),
}
MERCADOS = [("nasdaq", "QQQ", "NASDAQ"), ("oro", "GLD", "ORO"), ("petroleo", "USO", "PETROLEO")]

# QUIEN LIDERA A CADA ALT — medido 2026-09-08. Regresion de retornos de 4h sobre
# 3 factores (BTC, ETH, cesta DeFi de 8 monedas), roster COMPLETO de 30 alts.
# Cuando manda la cesta DeFi se reporta ETH, que es su vehiculo liquido.
# R2 alto = el lider explica mucho; R2 bajo = el contexto es flojo y hay que decirlo.
# Rango medido: LINK 0.833 (el mejor) hasta XMR 0.133 (practicamente independiente).
# (UNI ~ b1*BTC + b2*ETH, 1499 barras): manda el de beta mayor. NO es universal:
# ETH lidera 7 de 12 y BTC 5 de 12. Los tokens del ecosistema Ethereum siguen a ETH.
# El R2 va al lado porque mide cuanto del movimiento explica el lider: con R2 bajo el
# contexto es flojo y hay que decirlo.
#   alt   bBTC  bETH   R2        alt   bBTC  bETH   R2
#   UNI   0.38  0.83  0.274      SOL   0.57  0.47  0.623
#   LINK  0.22  0.82  0.545      XRP   0.69  0.51  0.495
#   AAVE  0.33  0.74  0.357      ADA   0.69  0.58  0.422
#   DOGE  0.49  0.62  0.518      LTC   0.46  0.33  0.366
#   AVAX  0.42  0.57  0.473      SUI   0.67  0.59  0.492
#   OP    0.31  0.79  0.337      TAO   0.50  0.56  0.367
LIDER = {
    "AAVEUSDT": ("ETH", 0.656), "ADAUSDT": ("ETH", 0.651), "APTUSDT": ("ETH", 0.629),
    "ARBUSDT": ("ETH", 0.522), "AVAXUSDT": ("ETH", 0.698), "BNBUSDT": ("BTC", 0.687),
    "DASHUSDT": ("ETH", 0.272), "DOGEUSDT": ("ETH", 0.695), "DOTUSDT": ("ETH", 0.599),
    "ENAUSDT": ("ETH", 0.515), "GALAUSDT": ("ETH", 0.600), "HBARUSDT": ("ETH", 0.582),
    "INJUSDT": ("ETH", 0.420), "LDOUSDT": ("ETH", 0.662), "LINKUSDT": ("ETH", 0.833),
    "LTCUSDT": ("ETH", 0.658), "NEARUSDT": ("ETH", 0.441), "ONDOUSDT": ("ETH", 0.557),
    "OPUSDT": ("ETH", 0.609), "ORDIUSDT": ("ETH", 0.294), "SOLUSDT": ("BTC", 0.793),
    "STXUSDT": ("ETH", 0.453), "SUIUSDT": ("ETH", 0.701), "TAOUSDT": ("ETH", 0.405),
    "UNIUSDT": ("ETH", 0.503), "WLDUSDT": ("ETH", 0.336), "XLMUSDT": ("ETH", 0.402),
    "XMRUSDT": ("ETH", 0.133), "XRPUSDT": ("BTC", 0.648), "ZECUSDT": ("ETH", 0.305),
}
_EQ_CACHE = {}
_EQ_TTL = 600.0          # yfinance es lento y el OI de opciones se publica 1 vez por dia

# __RELOJ_GRUPO_EQ_2026_09_20__ — UN SOLO RELOJ para los cuatro tickers de renta
# variable, en vez de cuatro relojes en fases arbitrarias.
#
# EL DEFECTO, MEDIDO sobre `gex_levels.db` (1.625 pasadas, 2026-09-08 -> 09-20):
# cada ticker vencia en SU propia fase, asi que la tarjeta del Nasdaq ensamblaba
# dos instantes distintos — QQQ vs TQQQ **max 649,2 s, p95 491,6 s, 37,5 % de las
# pasadas por encima de 60 s**. El control BTC/ETH (cache de 60 s, mismo hilo,
# misma pasada) da 0,0 % > 60 s: no es «el panel es lento», es `_EQ_TTL`.
#
# LO QUE DESFASA NO SON «cuatro relojes independientes» — GLD/USO comparten
# `_EQ_TTL`, hilo y pasada y dan p50 1,3 s. Lo que resetea la fase es una CAIDA
# recurrente: TQQQ falta en **173 de 1.620 pasadas (10,68 %)** en **5 rachas de
# 5,8 h que arrancan TODAS entre las 04:34 y las 04:45**.
#
# POR ESO EL SELLO SE ESTAMPA AUNQUE ALGUNO FALLE. Si exigiera que los cuatro
# tuvieran exito, durante las rachas quedaria vencido para siempre y cada pasada
# del refrescador (60 s) dispararia un grupo entero: es la regla INGENUA, que
# cuesta 44,74 fetch/h = 1,553x. Medido (400 corridas x 24 h, p por ticker y
# rachas empiricas): hoy 28,81 · ingenua 44,74 (1,553x) · grupo 23,99 (0,833x) ·
# **grupo + k=2 reintentos 24,93 (0,865x)**. La reparacion cuesta MENOS que hoy.
#
# SKEW RESIDUAL = LA DURACION DE LA PASADA. `st.ts` se estampa en `build_state`,
# despues del fetch de cada ticker; los cuatro fetch de yfinance tardan 1,1-4,6 s
# cada uno y la pasada **8,87 s como maximo** -> 8,87 / 596,5 = **1,49 %** del
# skew de hoy.
#
# LA AUSENCIA SE SIGUE REPORTANDO COMO AUSENCIA (`_EQ_GRUPO_OK`). La forma
# «obvia» de esta reparacion — sello fresco => devolve lo que haya en el cache —
# serviria un TQQQ de HORAS durante la racha y `coincide` compararia un QQQ
# fresco contra un TQQQ viejo: seria 35x PEOR que el defecto que se vino a
# arreglar, y borraria el `falta_tqqq` que iter.22 hizo visible. El sello cubre
# **los tickers que realmente entraron en esa pasada**, no un instante.
#
# LO QUE ESTO CUESTA, dicho con numero (no se esconde detras del 0,865x): hoy un
# TQQQ caido se reintenta en CADA pasada del refrescador (10 chances por ventana
# de 600 s); con grupo + k=2 son 3. Si la racha termina justo despues del tercer
# intento, TQQQ queda ausente hasta el siguiente sello aunque yfinance ya ande.
_EQ_GRUPO = ("QQQ", "TQQQ", "GLD", "USO")
_EQ_GRUPO_TS = [0.0]         # UN solo sello para los cuatro
_EQ_GRUPO_OK = set()         # que tickers cubre el sello VIGENTE
_EQ_REINTENTO = {}           # ticker -> reintentos que le quedan
_EQ_REINTENTO_K = 2
_EQ_GRUPO_LOCK = threading.Lock()

# __REINTENTO_RELOJ_2026_09_20__ — el reintento es del RELOJ, no del que llame.
# MEDIDO (iter.24, tramo R): `_eq_grupo_tick` lo llama CUALQUIER hilo, y los
# lectores de Flask encolados detras de la pasada queman los k=2 reintentos
# milisegundos despues del sello, cuando yfinance sigue igual de caido, en vez
# de a +60 s y +120 s. La cadencia se DERIVA de REFRESH_S: un segundo literal
# que tuviera que coincidir seria el defecto latente de iter.21.
_EQ_REINTENTO_TS = [0.0]
_EQ_REINTENTO_CADA_S = REFRESH_S


def _eq_fetch(ticker):
    """Un fetch crudo a yfinance, sin cache. None si falla. __RELOJ_GRUPO_EQ_2026_09_20__"""
    try:
        import gex_equity as _ge
        return _ge.state(ticker, dte_max=7.0, dte_ramp_days=1.0, cache=False)
    except Exception:                               # noqa: BLE001
        return None


def _eq_grupo_pasada():
    """Una pasada del grupo: los CUATRO y UN SOLO sello, que se estampa aunque alguno falle."""
    ok, fallo = [], []
    _EQ_GRUPO_OK.clear()
    _EQ_REINTENTO.clear()
    for _tk in _EQ_GRUPO:
        _st = _eq_fetch(_tk)
        if _st is not None:
            _EQ_CACHE[_tk] = (time.time(), _st)
            _EQ_GRUPO_OK.add(_tk)
            ok.append(_tk)
        else:
            _EQ_REINTENTO[_tk] = _EQ_REINTENTO_K
            fallo.append(_tk)
    _EQ_GRUPO_TS[0] = time.time()                   # SE ESTAMPA AUNQUE ALGUNO FALLE
    _EQ_REINTENTO_TS[0] = _EQ_GRUPO_TS[0]           # __REINTENTO_RELOJ_2026_09_20__
    return ok, fallo


def _eq_grupo_reintento():
    """k=2 reintentos, SOLO de los que fallaron, SIN mover el sello del grupo."""
    hechos = []
    for _tk in list(_EQ_REINTENTO):
        _n = _EQ_REINTENTO.get(_tk, 0)
        if _n <= 0:
            _EQ_REINTENTO.pop(_tk, None)
            continue
        _EQ_REINTENTO[_tk] = _n - 1
        _st = _eq_fetch(_tk)
        hechos.append(_tk)
        if _st is not None:
            _EQ_CACHE[_tk] = (time.time(), _st)
            _EQ_GRUPO_OK.add(_tk)
            _EQ_REINTENTO.pop(_tk, None)
        elif _EQ_REINTENTO[_tk] <= 0:
            _EQ_REINTENTO.pop(_tk, None)
    return hechos


def _eq_grupo_tick():
    """Un latido del reloj de grupo. Lo llama `refresh_once` cada REFRESH_S."""
    with _EQ_GRUPO_LOCK:
        if time.time() - _EQ_GRUPO_TS[0] >= _EQ_TTL:
            ok, fallo = _eq_grupo_pasada()
            return {"pasada": True, "ok": ok, "fallo": fallo}
        # __REINTENTO_RELOJ_2026_09_20__ — sin esta guarda, cada lector encolado
        # detras de la pasada quema un reintento al instante (medido: el ultimo
        # caia a +0,0 s en vez de +120 s, y el 89 % los disparaba un LECTOR).
        if time.time() - _EQ_REINTENTO_TS[0] < _EQ_REINTENTO_CADA_S:
            return {"pasada": False, "reintentos": []}
        _EQ_REINTENTO_TS[0] = time.time()
        return {"pasada": False, "reintentos": _eq_grupo_reintento()}


def equity_state(ticker="QQQ"):
    """Snapshot de gamma de una accion/ETF (cacheado). None si falla."""
    if ticker in _EQ_GRUPO:
        # __RELOJ_GRUPO_EQ_2026_09_20__ — el grupo NO se refresca por ticker: o el
        # sello esta vigente y se sirve lo que esa pasada trajo, o se hace una
        # pasada entera. Un ticker ausente NO dispara fetch por su cuenta (seria
        # la regla ingenua durante las rachas): para eso estan los k reintentos.
        if time.time() - _EQ_GRUPO_TS[0] >= _EQ_TTL:
            _eq_grupo_tick()
        hit = _EQ_CACHE.get(ticker)
        return hit[1] if (hit and ticker in _EQ_GRUPO_OK) else None
    # fuera del grupo (SPY, SMH, IWM, acciones sueltas): reloj propio, como siempre
    hit = _EQ_CACHE.get(ticker)
    if hit and time.time() - hit[0] < _EQ_TTL:
        return hit[1]
    st = _eq_fetch(ticker)
    if st is not None:
        _EQ_CACHE[ticker] = (time.time(), st)
    return st


# __GRIEGAS_API_2026_09_14__ — los NIVELES de las otras griegas (vanna, charm,
# delta, vol trigger, gamma max) ya se calculaban en gex_griegas.py para el Pine,
# pero no salian por levels()/_eq_levels(), asi que el dashboard solo podia
# mostrar gamma. Fail-soft: si el calculo falla, la clave viaja en None y la
# tarjeta pinta lo de siempre.
def _griegas3(st):
    try:
        import gex_griegas as _gg
        g = _gg.niveles_de_estado(st)
    except Exception:
        g = None
    return {"griegas": g or None}


# Precio del PERPETUO, para poder llevar un nivel de GLD/USO a la escala de
# XAUUSDT.P / CLUSDT.P. Cache corto: es precio, no estructura.
_PERP_PX = {}
_PERP_TTL = 60.0


def perp_px(sym):
    import json as _j
    import time as _t
    import urllib.request as _u
    hit = _PERP_PX.get(sym)
    if hit and _t.time() - hit[0] < _PERP_TTL:
        return hit[1]
    px = None
    try:
        d = _j.load(_u.urlopen(_u.Request(
            "https://api.bybit.com/v5/market/tickers?category=linear&symbol=%s" % sym,
            headers={"User-Agent": "x"}), timeout=8))
        px = float(d["result"]["list"][0]["lastPrice"])
    except Exception:
        px = None
    if px:
        _PERP_PX[sym] = (_t.time(), px)
    return px


def _eq_levels(st, ticker):
    """Version liviana de un snapshot de renta variable, con la forma del panel."""
    if st is None:
        return None
    import time as _t
    conc = (abs(st.net_gex) / st.abs_gex) if st.abs_gex > 0 else 0.0
    return {
        "ticker": ticker, "spot": round(st.spot, 2),
        "regimen": st.regimen, "estado": st.velocidad_esperada,
        "net_gex_musd": round(st.net_gex_musd, 1),
        "concentracion": round(conc, 3),
        "gamma_flip": st.gamma_flip,
        "dist_flip_pct": (round(st.dist_flip_pct, 2)
                          if st.dist_flip_pct is not None else None),
        "call_wall": st.call_wall, "put_wall": st.put_wall,
        "max_abs_strike": st.max_abs_strike,
        "n_contratos": st.n_contratos,
        "oi_acciones": round(st.oi_total_coin),
        "proximo_vto_horas": (round(st.proximo_vto_horas, 2)
                              if st.proximo_vto_horas else None),
        "proximo_vto_share_abs": (round(st.proximo_vto_share_abs, 3)
                                  if st.proximo_vto_share_abs else None),
        "edad_s": round(_t.time() - st.ts, 1),
        # __AISLAMIENTO_LECTURA_2026_09_20__ — el instante CRUDO. Los 19 campos de esta tarjeta comparten
        # este mismo `st.ts` (medido: 19 de 19), asi que una `edad_s` por
        # campo seria la misma constante repetida. Lo que el consumidor NO
        # puede saber hoy es si DOS tarjetas son del mismo instante: para
        # eso hace falta el `ts`, no otra edad.
        "ts": st.ts,
        **_exp3(st),
        **_griegas3(st),
    }


# __BETA_V2_2026_09_08__ — modelo mejorado, medido contra el anterior:
#     BTC+ETH en 1h   R2 0.274   (el que habia)
#     BTC+ETH en 4h   R2 0.378
#     + cesta DeFi 4h R2 0.503   <-- el que se usa
#     + DeFi + alts   R2 0.507   (agregar alts no aporta: +0.004)
# Dos cambios: (a) retornos de 4h en vez de 1h, que ademas es el horizonte correcto
# porque la proyeccion es a 6h; (b) una cesta del SECTOR, porque UNI es sobre todo
# una beta de DeFi y no de ETH. Casi duplica el poder explicativo y achica la banda.
CESTA_DEFI = ("AAVEUSDT", "LINKUSDT", "CRVUSDT", "MKRUSDT", "LDOUSDT",
              "SUSHIUSDT", "COMPUSDT", "SNXUSDT")
BETA_ITV = "4h"

_BETA_CACHE = {}
_BETA_TTL = 6 * 3600.0     # la beta es estable (dispersion 0.09 entre tercios): 6 h sobra

# __KL_CACHE_2026_09_13__ — BTC, ETH y la cesta DeFi se piden UNA vez por alt si
# no se cachean: 30 alts x 11 series = 330 pedidos HTTP dentro de un request web.
# Con velas de 4h, media hora de cache no cambia ninguna beta y lo vuelve viable.
_KL_CACHE = {}
_KL_TTL = 1800.0


def _klines_1h(sym, lim=1500, itv=None):
    import json as _j, time as _t, urllib.request as _u
    key = (str(sym).upper(), itv or BETA_ITV, lim)
    hit = _KL_CACHE.get(key)
    if hit and _t.time() - hit[0] < _KL_TTL:
        return hit[1]
    u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=%s&limit=%d"
         % (key[0], key[1], lim))
    L = _j.loads(_u.urlopen(_u.Request(u, headers={"User-Agent": "gexbeta"}),
                            timeout=20).read())
    out = {int(k[0]): float(k[4]) for k in L}
    _KL_CACHE[key] = (_t.time(), out)
    if len(_KL_CACHE) > 120:                 # no crecer sin limite
        for k2 in sorted(_KL_CACHE, key=lambda x: _KL_CACHE[x][0])[:40]:
            _KL_CACHE.pop(k2, None)
    return out


def beta_lider(sym):
    """
    Regresion de retornos de 4h: alt ~ b_btc*BTC + b_eth*ETH + b_defi*cestaDeFi.

    El lider reportado es el factor de mayor beta. El R2 y la vol residual son lo
    que impide vender falsa precision: con R2 0.50 la mitad del movimiento sigue
    siendo propio del activo, y de ahi sale la banda de la proyeccion.
    """
    import math as _m, time as _t
    key = str(sym).upper()
    hit = _BETA_CACHE.get(key)
    if hit and _t.time() - hit[0] < _BETA_TTL:
        return hit[1]
    try:
        U = _klines_1h(key); B = _klines_1h("BTCUSDT"); E = _klines_1h("ETHUSDT")
        cesta = [d for d in (_klines_1h(x) for x in CESTA_DEFI) if d]
        ts = sorted(set(U) & set(B) & set(E))
        for d in cesta:
            ts = [t for t in ts if t in d]
        if len(ts) < 300:
            return None
        r = lambda d: [_m.log(d[ts[i]] / d[ts[i - 1]]) for i in range(1, len(ts))]
        u, b, e = r(U), r(B), r(E)
        rc = [r(d) for d in cesta]
        n = len(u)
        cf = [sum(x[i] for x in rc) / len(rc) for i in range(n)] if rc else [0.0] * n
        X = [b, e, cf] if rc else [b, e]
        k = len(X)
        A = [[sum(X[i][t] * X[j][t] for t in range(n)) for j in range(k)] for i in range(k)]
        rhs = [sum(X[i][t] * u[t] for t in range(n)) for i in range(k)]
        M = [A[i][:] + [rhs[i]] for i in range(k)]
        for c in range(k):                                   # eliminacion gaussiana
            p = max(range(c, k), key=lambda rr: abs(M[rr][c]))
            M[c], M[p] = M[p], M[c]
            if abs(M[c][c]) < 1e-18:
                return None
            for rr in range(k):
                if rr != c:
                    f = M[rr][c] / M[c][c]
                    for cc in range(c, k + 1):
                        M[rr][cc] -= f * M[c][cc]
        co = [M[i][k] / M[i][i] for i in range(k)]
        pred = [sum(co[i] * X[i][t] for i in range(k)) for t in range(n)]
        sst = sum(v * v for v in u)
        ssr = sum((u[t] - pred[t]) ** 2 for t in range(n))
        nombres = ["BTC", "ETH", "DEFI"][:k]
        idx = max(range(k), key=lambda i: abs(co[i]))

        # __CORR_2026_09_13__ — la CORRELACION simple con cada lider. El R2 de
        # la regresion multiple mide cuanto explican los tres factores JUNTOS;
        # para decidir si vale la pena proyectar el nivel de BTC sobre el alt,
        # lo que manda es que tan pegado va el alt a ESE lider. Pearson sobre
        # los mismos retornos de 4h.
        def _corr(x, y):
            mx = sum(x) / len(x)
            my = sum(y) / len(y)
            sxy = sum((x[i] - mx) * (y[i] - my) for i in range(len(x)))
            sxx = sum((v - mx) ** 2 for v in x)
            syy = sum((v - my) ** 2 for v in y)
            den = _m.sqrt(sxx * syy)
            return (sxy / den) if den > 0 else 0.0

        c_btc = _corr(u, b)
        c_eth = _corr(u, e)
        out = {"lider": nombres[idx], "beta": round(co[idx], 3),
               "b_btc": round(co[0], 3), "b_eth": round(co[1], 3),
               "b_defi": (round(co[2], 3) if k > 2 else None),
               "corr_btc": round(c_btc, 3), "corr_eth": round(c_eth, 3),
               "r2": round(1 - ssr / sst, 3) if sst > 0 else 0.0,
               "vol_h_pct": round(_m.sqrt(sst / n) * 100, 3),
               "resid_h_pct": round(_m.sqrt(ssr / n) * 100, 3),
               "itv": BETA_ITV, "spot": U[ts[-1]], "n": n,
               # __AISLAMIENTO_LECTURA_2026_09_20__ — el instante de ESTE spot. `_BETA_CACHE` congela el
               # dict entero hasta 6 h y `_DERIV_CACHE` del panel otros
               # 1800 s, asi que el ancla de `derivado()` puede tener
               # 7,0 h. Verificado contra Binance: la ultima vela que
               # devuelve /fapi/v1/klines ESTA EN CURSO, o sea que
               # `U[ts[-1]]` es el ultimo precio AL MOMENTO DEL FETCH.
               "ts_spot": _t.time()}
        _BETA_CACHE[key] = (_t.time(), out)
        return out
    except Exception:                                   # noqa: BLE001
        return None


CORR_MIN_DERIVADO = 0.55


def derivado(sym="UNIUSDT", horas=6.0, corr_min=None):
    """
    Niveles de gamma Y DEL RESTO DE LAS GRIEGAS del LIDER, proyectados al alt.

    __DERIVADO_GRIEGAS_2026_09_13__ — TAO, SOL, XRP y compania no tienen opciones
    (verificado contra Deribit: solo BTC y ETH tienen cadena viva). No hay dealer
    cubriendose EN el alt, asi que el alt NO tiene un muro propio. Lo unico que se
    puede afirmar es otra cosa, mas debil pero util:

        "cuando BTC llegue a SU nivel de gamma, el alt va a estar por aca"

    Es una TRADUCCION del nivel del lider a la escala de precio del alt via beta.
    La reaccion mecanica ocurre en el LIDER y llega al alt por correlacion. Por eso
    estos niveles se marcan como DERIVADOS y nunca como propios.

    EL NUMERO QUE MANDA ES LA CORRELACION (pedido del usuario 2026-09-13):
      · se elige como lider el que tenga MAYOR |correlacion| con el alt, no el de
        mayor beta (una beta grande con correlacion baja es ruido amplificado);
      · si |corr| < `corr_min` no se proyecta nada y se devuelve el motivo;
      · la beta escala la DISTANCIA, la correlacion decide SI se proyecta.

    Un nivel cuya distancia proyectada es MENOR que la banda de ruido residual
    esta tapado: se marca `tapado` y no debe usarse como nivel.
    """
    import math as _m
    cmin = CORR_MIN_DERIVADO if corr_min is None else float(corr_min)
    bl = beta_lider(sym)
    if not bl:
        return None

    # Lider POR CORRELACION, no por beta.
    c_btc = abs(bl.get("corr_btc") or 0.0)
    c_eth = abs(bl.get("corr_eth") or 0.0)
    _ref = "ETH" if c_eth > c_btc else "BTC"
    _corr = bl.get("corr_eth") if _ref == "ETH" else bl.get("corr_btc")
    _corr = float(_corr or 0.0)
    _bet = bl["b_eth"] if _ref == "ETH" else bl["b_btc"]

    base = {"symbol": sym, "spot": bl["spot"], "lider": bl["lider"], "ref": _ref,
            "beta_ref": round(_bet, 3), "beta": bl["beta"],
            "corr": round(_corr, 3), "corr_btc": bl.get("corr_btc"),
            "corr_eth": bl.get("corr_eth"), "corr_min": cmin,
            "r2": bl["r2"], "n": bl["n"], "horas": horas,
            "vol_h_pct": bl["vol_h_pct"], "resid_h_pct": bl["resid_h_pct"]}

    if abs(_corr) < cmin:
        base.update({"aplica": False, "niveles": [], "tapados": 0, "total": 0,
                     "nota": ("Correlacion con %s = %.2f, por debajo del minimo "
                              "%.2f: proyectar el nivel del lider seria inventar "
                              "precision. No se dibuja nada."
                              % (_ref, _corr, cmin))})
        return base

    st = state(_ref, "scalp")
    lv = levels(_ref, "scalp")
    if not lv or not lv.get("spot"):
        return None
    try:
        import gex_griegas as _gg
        gr = _gg.niveles_de_estado(st)
    except Exception:                                    # noqa: BLE001
        gr = {}

    spot = bl["spot"]
    banda = bl["resid_h_pct"] * _m.sqrt(max(horas / 4.0, 1.0))   # residual en barras de 4h
    sp = lv["spot"]

    fuentes = (("techo", lv.get("call_wall")), ("piso", lv.get("put_wall")),
               ("flip", lv.get("gamma_flip")),
               ("gamma_max", gr.get("gamma_max")),
               ("vanna_wall", gr.get("vanna_wall")),
               ("vanna_flip", gr.get("vanna_flip")),
               ("charm_wall", gr.get("charm_wall")),
               ("delta_wall", gr.get("delta_wall")),
               ("delta_zero", gr.get("delta_zero")),
               ("vol_trigger", gr.get("vol_trigger")))
    out = []
    for nom, px in fuentes:
        if not px:
            continue
        d = (px - sp) / sp * 100.0
        du = d * _bet
        out.append({"nombre": nom, "origen_px": px, "dist_origen_pct": round(d, 2),
                    "dist_pct": round(du, 2), "px": round(spot * (1 + du / 100.0), 8),
                    "tapado": abs(du) < banda})

    # __AISLAMIENTO_LECTURA_2026_09_20__ — APPEND-ONLY, FAIL-SOFT. El nivel `px` de cada fila de `niveles`
    # sale a /api/tv_niveles (la cadena que se pega en TradingView ANTES de
    # operar) y se calcula como `spot_alt * (1 + d*beta)`, donde `d` usa un
    # spot del LIDER de <= 60 s pero `spot_alt` viene congelado. Medido
    # sobre 42 activos: a 6 h el movimiento del alt supera la propia
    # `banda_pct` declarada el 35,5 % de las veces (45,3 % a 7,0 h), con
    # p95 3,359 % contra una banda mediana de 0,865 %. No cambia ningun
    # nivel: solo dice de cuando es el ancla.
    _aisl = {}
    try:
        import time as _t2
        _ts_sp = bl.get("ts_spot")
        _e_sp = round(_t2.time() - _ts_sp, 1) if _ts_sp else None
        _e_li = lv.get("edad_s")
        _aisl = {"edad_spot_s": _e_sp, "edad_lider_s": _e_li,
                 "skew_ancla_s": (round(abs(_e_sp - _e_li), 1)
                                  if _e_sp is not None and _e_li is not None
                                  else None)}
    except Exception:                               # noqa: BLE001
        _aisl = {"edad_spot_s": None, "edad_lider_s": None, "skew_ancla_s": None}
    base.update({
        "aplica": True,
        "regimen": lv.get("regimen"), "estado": lv.get("velocidad_esperada"),
        "banda_pct": round(banda, 2), **_aisl,
        "niveles": out,
        "tapados": sum(1 for x in out if x["tapado"]), "total": len(out),
        "nota": ("Proyeccion por beta desde %s (correlacion %.2f, beta %.2f). La "
                 "reaccion ocurre EN %s y llega al alt por correlacion: el alt no "
                 "tiene cadena de opciones ni muro propio. El lider explica el "
                 "%.0f%% del movimiento; el resto es propio del activo y de ahi "
                 "sale la banda de %.2f%%. Contexto, NO nivel operable."
                 % (_ref, _corr, _bet, _ref, bl["r2"] * 100, banda))})
    return base


def mercados():
    """Bloque de todos los mercados con GEX de renta variable / materias primas."""
    out = {}
    for clave, ticker, etiqueta in MERCADOS:
        lv = _eq_levels(equity_state(ticker), ticker)
        if not lv:
            continue
        cob, why = COBERTURA.get(ticker, ("?", ""))
        lv["cobertura"] = cob
        lv["cobertura_why"] = why
        lv["etiqueta"] = etiqueta
        # __PERP_2026_09_14__ — GLD y USO cotizan en otra escala que los
        # perpetuos que se operan (GLD ~395 contra XAU ~4.400; USO ~155 contra
        # CL ~97). Cada nivel viaja tambien convertido por la razon de spots:
        # es el mismo subyacente, asi que un x % en uno es un x % en el otro.
        _pp = {"oro": "XAUUSDT", "petroleo": "CLUSDT"}.get(clave)
        if _pp and lv.get("spot"):
            _px = perp_px(_pp)
            if _px:
                _f = float(_px) / float(lv["spot"])
                _cv = {}
                for _k in ("gamma_flip", "call_wall", "put_wall", "max_abs_strike"):
                    _v = lv.get(_k)
                    _cv[_k] = round(_v * _f, 2) if _v else None
                _g = lv.get("griegas") or {}
                for _k in ("vanna_wall", "vanna_flip", "charm_wall",
                           "delta_wall", "delta_zero", "vol_trigger", "gamma_max"):
                    _v = _g.get(_k)
                    _cv[_k] = round(_v * _f, 2) if _v else None
                # __AISLAMIENTO_LECTURA_2026_09_20__ — `factor` = `_PERP_PX` (TTL 60 s) / `lv["spot"]`
                # (`_EQ_CACHE`, TTL 600 s): un COCIENTE ENTRE DOS INSTANTES.
                # iter.21 midio el error que inyecta condicional a que el
                # subyacente se mueva: p95 0,27 % (GLD) / 0,65 % (USO),
                # max 2,16 % / 3,58 %. Append-only y fail-soft.
                _ap = {}
                try:
                    _hp = _PERP_PX.get(_pp)
                    _ahora = time.time()
                    _e_px = round(_ahora - _hp[0], 1) if _hp else None
                    _e_lv = lv.get("edad_s")
                    _ap = {"edad_px_s": _e_px, "edad_lv_s": _e_lv,
                           "skew_s": (round(abs(_e_lv - _e_px), 1)
                                      if _e_px is not None and _e_lv is not None
                                      else None)}
                except Exception:                   # noqa: BLE001
                    _ap = {"edad_px_s": None, "edad_lv_s": None, "skew_s": None}
                lv["perp"] = {"symbol": _pp, "px": round(_px, 4),
                              "factor": round(_f, 6), "niveles": _cv, **_ap}
        out[clave] = lv
    return out


def permite_movimiento(lv, pct_objetivo, lado):
    """
    ¿La estructura de gamma permite un movimiento de `pct_objetivo` % hacia `lado`?

    La direccion NO sale de aca: la pone el panel. Esto solo responde si el camino
    esta libre para esa MAGNITUD.
    """
    if not lv or not pct_objetivo:
        return None
    spot = lv.get("spot") or 0
    if spot <= 0:
        return None
    obj = spot * (1 + pct_objetivo / 100.0) if lado == "LONG" else spot * (1 - pct_objetivo / 100.0)
    motivos, contras = [], []
    permite = True

    # vencimiento grande e inminente: hasta esa hora manda el pinning
    h = lv.get("proximo_vto_horas")
    sh = lv.get("proximo_vto_share_abs") or 0
    if h is not None and h <= 12 and sh >= 0.25:
        permite = False
        contras.append("vencimiento en %.1f h con %.0f%% del |GEX|: hasta entonces "
                       "domina el pinning, la expansion es DESPUES" % (h, sh * 100))

    if lv.get("regimen") == "NEGATIVO":
        motivos.append("gamma negativa: los dealers amplifican, el movimiento se extiende")
    else:
        # muro estabilizador entre el precio y el objetivo
        muro = lv.get("call_wall") if lado == "LONG" else lv.get("put_wall")
        if muro:
            entre = (spot < muro < obj) if lado == "LONG" else (obj < muro < spot)
            if entre:
                permite = False
                d = abs(muro - spot) / spot * 100.0
                contras.append("muro estabilizador en %s a %.2f%%: ahi frena, ese es el "
                               "objetivo realista" % (muro, d))
            else:
                motivos.append("camino libre: el muro de %s queda mas alla del objetivo" % muro)
        else:
            motivos.append("sin muro identificado en esa direccion")

    d = lv.get("dist_flip_pct")
    if d is not None and abs(d) <= 0.5:
        motivos.append("sobre el gamma flip (%.2f%%): punto de ignicion, pero el regimen "
                       "puede darse vuelta dentro del trade -> reducir tamano" % d)
    return {"permite": permite, "motivos": motivos, "contras": contras,
            "objetivo_px": round(obj, 4)}


def aviso_proyeccion(rows, min_pct=1.0):
    """
    Avisos de proyeccion >= min_pct% permitida por la gamma, sobre los activos que
    tienen estructura medible. La DIRECCION y el OBJETIVO salen del panel; la gamma
    solo dice si la estructura permite esa magnitud.
    """
    if not rows:
        return []
    mkt = mercados()
    por_sym = {}
    for clave, ticker, _et in MERCADOS:
        if clave in mkt:
            por_sym[ticker] = mkt[clave]
    avisos = []
    for r in rows:
        sym = r.get("symbol")
        lado = r.get("dir_reco")
        dist = r.get("dist_target")
        if lado not in ("LONG", "SHORT") or dist is None:
            continue
        if abs(dist) < min_pct:
            continue
        # de que estructura se lee este simbolo
        lv = None
        if sym in COMMODITY_MAP:
            lv = por_sym.get(COMMODITY_MAP[sym][0])
        elif sym in EQUITY_MAP:
            lv = por_sym.get(EQUITY_MAP[sym][0])
        elif coin_for(sym):
            lv = levels(coin_for(sym), "scalp")
            if lv:
                lv = dict(lv, spot=lv.get("spot"), cobertura="dominante",
                          cobertura_why="Deribit 85-89% del OI de opciones cripto",
                          etiqueta=coin_for(sym))
        if not lv:
            continue
        ok = permite_movimiento(lv, abs(dist), lado)
        if not ok or not ok["permite"]:
            continue
        cob = lv.get("cobertura", "?")
        avisos.append({
            "symbol": sym, "lado": lado, "pct": round(abs(dist), 2),
            "target": r.get("target"), "target_kind": r.get("target_kind"),
            "estructura": lv.get("etiqueta"), "cobertura": cob,
            "cobertura_why": lv.get("cobertura_why"),
            "regimen": lv.get("regimen"), "estado": lv.get("estado") or lv.get("velocidad_esperada"),
            "motivos": ok["motivos"],
            "reduce": any("reducir tamano" in m for m in ok["motivos"]),
            # el panel define la direccion; se adjunta su evidencia
            "panel": {"modo": r.get("modo_reco"), "op_score": r.get("op_score"),
                      "target_reach": r.get("target_reach"), "sig_pos": r.get("sig_pos"),
                      "sott": r.get("sott_side"), "htf": r.get("htf_side"),
                      "decision": (r.get("decision") or {}).get("dec")},
        })
    avisos.sort(key=lambda a: -a["pct"])
    return avisos


def nasdaq():
    """
    Bloque combinado del Nasdaq para la tarjeta de GEX.

    REGIMEN de QQQ (libro 15x mayor, los dealers cubren ahi) + NIVELES de TQQQ
    (sus strikes ya estan en precio de TQQQ, asi que son operables directo) +
    el rebalanceo diario del propio ETF apalancado. Ver el docstring del parche
    add_nasdaq_card.py para el detalle del razonamiento.
    """
    q = _eq_levels(equity_state("QQQ"), "QQQ")
    t = _eq_levels(equity_state("TQQQ"), "TQQQ")
    if q is None and t is None:
        return None
    coincide = bool(q and t and q["regimen"] == t["regimen"])
    reb = None
    try:
        import gex_equity as _ge
        r = _ge.rebalance_usd("TQQQ")
        if r:
            reb = round(r["usd_por_1pct"] / 1e6)
    except Exception:
        pass
    # la lectura manda el regimen de QQQ; TQQQ solo confirma
    base = q or t
    cerca = base["dist_flip_pct"] is not None and abs(base["dist_flip_pct"]) <= 0.5
    if cerca:
        lectura = "BISAGRA: regimen indefinido, reducir tamano"
    elif base["regimen"] == "POSITIVO":
        lectura = "PINNING: el fade a la media tiene el flujo a favor; las rupturas mueren"
    else:
        lectura = "VIOLENTA: los dealers persiguen; favorece continuacion y barrido"
    # __AISLAMIENTO_LECTURA_2026_09_20__ — APPEND-ONLY, FAIL-SOFT. No cambia `coincide` ni `lectura`.
    #   `skew_s`   = |ts(QQQ) - ts(TQQQ)|. Medido sobre 1.448 tarjetas con
    #                los dos presentes: max 584,6 s, p95 359,0 s, 35,7 %
    #                por encima de 60 s. El techo es `_EQ_TTL = 600`.
    #   `coincide_motivo` — `coincide=False` MEZCLA dos cosas: que los dos
    #                regimenes difieran, y que TQQQ NO ESTE. Medido: de los
    #                691 `False` publicados, 173 (25,04 %) son ausencia; en
    #                el ultimo 30 % del periodo, 76,92 %. El panel pinta
    #                «TQQQ discrepa» y afirma un mecanismo que ahi es falso.
    _aisl = {}
    try:
        _ahora = time.time()
        _eq = {"qqq": (q or {}).get("ts"), "tqqq": (t or {}).get("ts")}
        _ed = {k: (round(_ahora - v, 1) if v else None) for k, v in _eq.items()}
        _sk = (round(abs(_eq["qqq"] - _eq["tqqq"]), 1)
               if _eq["qqq"] and _eq["tqqq"] else None)
        _mot = ("ambos" if (q and t) else
                "falta_tqqq" if q else "falta_qqq" if t else "faltan_los_dos")
        _aisl = {"edades_s": _ed, "skew_s": _sk,
                 "instante_comun": (_sk is not None and _sk <= 60.0),
                 "coincide_motivo": _mot}
    except Exception:                               # noqa: BLE001
        _aisl = {"edades_s": None, "skew_s": None, "instante_comun": None,
                 "coincide_motivo": None}
    return {"qqq": q, "tqqq": t, "coincide": coincide, "lectura": lectura,
            "rebalanceo_musd_por_1pct": reb, **_aisl,
            "nota": ("Regimen del libro de QQQ (nocional del OI 15x el de TQQQ). "
                     "Niveles del libro de TQQQ, que ya estan en su propio precio. "
                     "Medido: la gamma positiva mejora el edge de IBS (+2.00%/op vs "
                     "+1.19%), pero las dos cohortes son rentables, asi que es senal "
                     "de TAMANO y PRIORIDAD, no de veto. En REBOTE no se sostiene.")}


def for_equity_row(symbol):
    """
    Contexto de gamma para un simbolo de renta variable apalancado (hoy solo TQQQ).
    Devuelve SIEMPRE aplica=False: es contexto, nunca vetea. No hay ninguna
    validacion del efecto sobre operaciones de TQQQ.
    """
    m = EQUITY_MAP.get(str(symbol).upper())
    if not m:
        return None
    ticker, mult = m
    st = equity_state(ticker)
    if st is None:
        return None
    import gex_equity as _ge
    L = _ge.lectura_tqqq(st, mult=mult)
    return {"aplica": False, "contexto": True, "equity": True,
            "coin": ticker, "subyacente": ticker, "mult": mult,
            "regimen": st.regimen, "gamma_flip": st.gamma_flip,
            "dist_flip_pct": (round(st.dist_flip_pct, 2)
                              if st.dist_flip_pct is not None else None),
            "dist_flip_x_pct": (round(L["dist_flip_tqqq_pct"], 2)
                                if L and L.get("dist_flip_tqqq_pct") is not None else None),
            "call_wall": st.call_wall, "put_wall": st.put_wall,
            "dist_call_wall_x_pct": (round(L["dist_call_wall_x_pct"], 2)
                                     if L and L.get("dist_call_wall_x_pct") is not None else None),
            "velocidad_esperada": st.velocidad_esperada,
            "net_gex_musd": round(st.net_gex_musd, 1),
            "spot_sub": round(st.spot, 2),
            "nota": ("regimen del NASDAQ leido en %s (nocional del OI 15x el de TQQQ; "
                     "los dealers cubren ahi). Distancias x%.0f. Es CONTEXTO: no vetea."
                     % (ticker, mult))}


def coin_for(symbol):
    if not symbol:
        return None
    return _MAP.get(str(symbol).upper())


# ---------------------------------------------------------------------------
# REFRESCO
# ---------------------------------------------------------------------------

def refresh_once():
    from gex_engine import build_state
    # __RELOJ_GRUPO_EQ_2026_09_20__ — renta variable: UN latido del reloj de grupo.
    # Antes se llamaba `equity_state(tk)` uno por uno y cada ticker vencia en su
    # propia fase; ahora la pasada entera comparte sello. El literal de los cuatro
    # tickers deja de estar aca: vive en `_EQ_GRUPO`, un solo sitio.
    try:
        _eq_grupo_tick()
    except Exception:                                  # noqa: BLE001
        pass
    for coin in COINS:
        for view, kw in VIEWS.items():
            key = (coin, view)
            try:
                st = build_state(coin, exchanges=EXCHANGES, **kw)
                with _lock:
                    _cache[key] = st
                    _errors.pop(key, None)
            except Exception as e:                      # noqa: BLE001
                with _lock:
                    _errors[key] = "%s: %s" % (type(e).__name__, e)


_ULTIMA_GRABACION = [0.0]
GRABAR_CADA_S = 600.0      # los niveles se mueven en horas; 10 min sobra


def _grabar_niveles():
    """
    __GEX_REC_2026_09_08__ — graba los niveles para poder validarlos despues.

    Los NIVELES son perecederos: nadie publica el open interest por strike de ayer.
    Si no se graban hoy, se pierden. Los PRECIOS no lo son (las velas se bajan
    cuando uno quiera), asi que gex_levels_study.py reconstruye la accion del precio
    a posteriori y compara cada nivel contra un placebo puesto en el mismo barrio.
    """
    import time as _t
    if _t.time() - _ULTIMA_GRABACION[0] < GRABAR_CADA_S:
        return
    _ULTIMA_GRABACION[0] = _t.time()
    try:
        import gex_recorder as _gr
        _gr.tomar()
    except Exception:                                   # noqa: BLE001
        pass


def _refresher():
    while True:
        t0 = time.time()
        try:
            refresh_once()
            _grabar_niveles()
        except Exception:                               # noqa: BLE001
            pass
        time.sleep(max(5.0, REFRESH_S - (time.time() - t0)))


def start():
    """Arranca el hilo de refresco una sola vez (idempotente)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_refresher, daemon=True, name="gex-refresh").start()


def state(coin="BTC", view="scalp"):
    with _lock:
        return _cache.get((str(coin).upper(), view))


def error(coin="BTC", view="scalp"):
    with _lock:
        return _errors.get((str(coin).upper(), view))


def ready():
    with _lock:
        return bool(_cache)


# ---------------------------------------------------------------------------
# VISTAS
# ---------------------------------------------------------------------------

def _ramp_de(st):
    """
    __EXP3_VISTA_2026_09_20__ — la rampa que le corresponde a ESTE `st`.

    Antes `_exp3` leia `VIEWS['scalp']['dte_ramp_days']` y se la aplicaba a
    `st._rows` fuera cual fuera el `st`. La rampa viajaba por NOMBRE DE VISTA y
    las filas por OBJETO. Medido: con `view='estructural'` eso desvia el CEX
    +1,79 % (BTC) / +2,51 % (ETH) — nunca le cambia el signo, 0 de 8 — y con
    `view='scalp'` el desvio es 0,0000 % exacto, porque ahi coinciden por
    casualidad. Tres respaldos, en orden de confianza:
        1. el valor que `build_state` guardo en el propio estado;
        2. la vista cuyo `dte_max` coincide con el `st.dte_max` (cubre los
           estados de `gex_equity`, que se construyen con dte_max=7.0/ramp=1.0);
        3. 0.0, que es el primitivo declarado de `exposiciones`.
    """
    r = getattr(st, "_dte_ramp_days", None)
    if r is not None:
        return float(r)
    dmax = getattr(st, "dte_max", None)
    for v in VIEWS.values():
        if v.get("dte_max") == dmax:
            return float(v.get("dte_ramp_days", 0.0))
    return 0.0


def _exp3(st):
    """__VEX_CEX_2026_09_08__: GEX/VEX/CEX normalizados y comparables."""
    try:
        from gex_engine import exposiciones
        e = exposiciones(getattr(st, "_rows", None) or [], st.spot,
                         dte_ramp_days=_ramp_de(st))
        return {"vex_musd": round(e["vex"] / 1e6, 3), "cex_musd": round(e["cex"] / 1e6, 3)}
    except Exception:
        return {"vex_musd": None, "cex_musd": None}


_ZCACHE = {}


def _zonas_de(st, atr_pct=None):
    """Zonas del snapshot, memorizadas por (coin, ts) para no recalcular por fila."""
    try:
        import gex_zonas as gz
    except Exception:
        return None
    k = (st.coin, st.ts, atr_pct)
    z = _ZCACHE.get(k)
    if z is None:
        try:
            z = gz.zonas_desde_ladder(st.ladder, st.spot, atr_pct=atr_pct)
        except Exception:
            return None
        _ZCACHE.clear()          # una foto por moneda alcanza
        _ZCACHE[k] = z
    return z


def estructura(coin="BTC", view="scalp", atr_pct=None,
               poc=None, val=None, vah=None):
    """
    __GEX_ZONAS_2026_09_09__ — P1/P2/N1/N2/A1/A2 por clustering sobre la MISMA
    escalera agregada (Deribit + Bybit + OKX) que ya produce el flip y los muros.
    No baja nada nuevo: es una lectura distinta del snapshot que ya esta en cache.

    Devuelve {"zonas": {...}, "confluencia": [...], "regimen":..., "flip":...}
    o None si todavia no hay foto.
    """
    st = state(coin, view)
    if st is None:
        return None
    try:
        import gex_zonas as gz
    except Exception:
        return None
    z = gz.zonas_desde_ladder(st.ladder, st.spot, atr_pct=atr_pct)
    c = gz.confluencias(z, poc=poc, val=val, vah=vah)
    return {"coin": st.coin, "spot": st.spot, "regimen": st.regimen,
            "gamma_flip": st.gamma_flip, "zonas": z, "confluencia": c,
            "ts": st.ts, "edad_s": round(time.time() - st.ts, 1)}


def levels(coin="BTC", view="scalp"):
    """Version liviana: regimen, flip y muros. Es lo que se loguea por senal."""
    st = state(coin, view)
    if st is None:
        return None
    top = sorted(st.ladder, key=lambda l: l.abs_gex, reverse=True)[:8]
    edad = time.time() - st.ts
    return {
        "coin": st.coin, "view": view, "spot": st.spot, "ts": st.ts,
        "edad_s": round(edad, 1), "stale": edad > STALE_S,
        "regimen": st.regimen,
        "net_gex_musd": round(st.net_gex_musd, 2),
        "gamma_flip": st.gamma_flip,
        "dist_flip_pct": (round(st.dist_flip_pct, 2)
                          if st.dist_flip_pct is not None else None),
        "call_wall": st.call_wall, "put_wall": st.put_wall,
        "max_abs_strike": st.max_abs_strike,
        "zero_gamma_zone": st.zero_gamma_zone,
        "velocidad_esperada": st.velocidad_esperada,
        "proximo_vto_horas": (round(st.proximo_vto_horas, 2)
                              if st.proximo_vto_horas else None),
        "proximo_vto_share_abs": (round(st.proximo_vto_share_abs, 3)
                                  if st.proximo_vto_share_abs else None),
        "oi_total_coin": round(st.oi_total_coin, 1),
        "n_contratos": st.n_contratos,
        "muros": [{"strike": l.strike,
                   "net_gex_musd": round(l.net_gex / 1e6, 2),
                   "abs_gex_musd": round(l.abs_gex / 1e6, 2),
                   "tipo": "ESTABILIZADOR" if l.net_gex > 0 else "ACELERADOR"}
                  for l in top],
        "exchanges": st.exchanges, "warnings": st.warnings,
        # __GEX_ZONAS_2026_09_09__: zonas agrupadas sobre la misma escalera
        "zonas": _zonas_de(st),
        **_exp3(st),
        **_griegas3(st),
    }


def perfil(coin="BTC", view="scalp", n=121):
    """Curva de GEX neto vs spot, submuestreada para el grafico del panel."""
    st = state(coin, view)
    if st is None:
        return None
    xs, ys = st.profile_x, st.profile_y
    step = max(1, len(xs) // n)
    return {"x": xs[::step], "y": [v / 1e6 for v in ys[::step]],
            "spot": st.spot, "flip": st.gamma_flip}


# ---------------------------------------------------------------------------
# VALIDACION
# ---------------------------------------------------------------------------

def validar_senal(coin, lado, entrada, tp, tipo="REVERSION", sl=None,
                  view="scalp", origen="manual", symbol=None, log=True):
    """
    Veredicto de una senal contra la estructura de gamma.
    Devuelve SIEMPRE un dict; si no hay snapshot, `aplica=False` y el llamador
    debe seguir de largo (fail-open: la capa GEX no bloquea por caerse).
    """
    from gex_filter import validar

    st = state(coin, view)
    if st is None:
        return {"aplica": False, "veredicto": "SIN_DATO", "coin": coin,
                "motivo": error(coin, view) or "snapshot no disponible todavia"}
    try:
        v = validar(st, lado=lado, entrada=float(entrada), tp=float(tp),
                    sl=sl, tipo=tipo)
    except Exception as e:                              # noqa: BLE001
        return {"aplica": False, "veredicto": "SIN_DATO", "coin": coin,
                "motivo": "%s: %s" % (type(e).__name__, e)}

    out = v.to_dict()
    out["aplica"] = True
    out["coin"] = coin
    out["view"] = view
    out["edad_s"] = round(time.time() - st.ts, 1)
    out["stale"] = (time.time() - st.ts) > STALE_S
    out["exchanges"] = st.exchanges
    out["net_gex_musd"] = round(st.net_gex_musd, 2)
    if log:
        try:
            log_signal(out, origen=origen, symbol=symbol or coin)
        except Exception:                               # noqa: BLE001
            pass
    return out


def _estructura_fila(coin, row):
    """
    __GEX_ZONAS_2026_09_09__ — zonas + confluencia con el perfil + aporte al score,
    para UNA fila del panel. Fail-open: si algo falta devuelve None y el llamador
    sigue de largo. NO genera senales; solo describe y puntua.
    """
    try:
        import gex_zonas as gz
    except Exception:
        return None
    st = state(coin, "scalp")
    if st is None:
        return None
    atr = row.get("atr_pct") or row.get("atr5m_pct")
    try:
        atr = float(atr) if atr else None
    except (TypeError, ValueError):
        atr = None
    z = _zonas_de(st, atr)
    if not z:
        return None
    c = gz.confluencias(z, poc=row.get("poc"), val=row.get("val"), vah=row.get("vah"))
    ap = gz.aporte_score(z, c, lado=row.get("dir_reco"), modo=row.get("modo_reco"),
                         spot=st.spot)
    return {"zonas": z, "confluencia": c, "aporte": ap,
            "gamma_flip": st.gamma_flip, "regimen": st.regimen}


def for_row(row):
    """
    Inyeccion por fila del panel PERFIL DE VOLUMEN + VWAP EXTREMOS.

    Mapea el modo del panel al tipo de senal del filtro:
        FADE      (volver al POC/VWAP)  -> REVERSION
        TENDENCIA (seguir el SOTT)      -> CONTINUACION

    Para los simbolos sin cadena propia devuelve solo el CONTEXTO de BTC, con
    aplica=False, para que nunca vete una fila cuya estructura no medimos.
    """
    sym = row.get("symbol") or row.get("sym")
    if str(sym).upper() in EQUITY_MAP:          # TQQQ: el regimen se lee en QQQ
        return for_equity_row(sym)
    coin = coin_for(sym)
    if coin is None:
        # El lider NO siempre es BTC: medido por regresion de retornos horarios, ETH
        # lidera 7 de 12 alts (ver LIDER). Se usa el que explica mas, y se adjunta el
        # R2 para que se vea cuan flojo es el contexto.
        _lid, _r2 = LIDER.get(str(sym).upper(), ("BTC", None))
        ctx = levels(_lid, "scalp")
        if not ctx:
            return None
        return {"aplica": False, "contexto": True, "coin": _lid, "lider_r2": _r2,
                "regimen": ctx["regimen"], "gamma_flip": ctx["gamma_flip"],
                "velocidad_esperada": ctx["velocidad_esperada"],
                "net_gex_musd": ctx["net_gex_musd"],
                "nota": ("contexto de mercado leido en %s (lidera a %s por beta%s). "
                         "%s no tiene cadena de opciones representativa."
                         % (_lid, (sym or "?").replace("USDT", ""),
                            (", R2 %.2f" % _r2) if _r2 else "", sym or "?"))}

    lado = row.get("dir_reco")
    entrada = row.get("px")
    tp = row.get("target")
    modo = row.get("modo_reco")
    lv = levels(coin, "scalp")
    if not lv:
        return None
    # Fila de VIGILANCIA sin perfil de volumen: _inject_confluence_rows() rellena
    # vah = val = poc = vwap = target = px como marcadores de posicion (is_ext False,
    # sig_pos 0). Validar eso daria recorrido 0 y una falsa "Incoherencia", asi que se
    # muestra solo la estructura. NO es un setup de fade: es un sesgo de direccion por
    # confluencia SOTT/z, y el fade necesita que el precio ESTE en un borde del perfil.
    _sin_perfil = bool(
        entrada and tp and abs(float(tp) - float(entrada)) / float(entrada) < 1e-4)
    if lado not in ("LONG", "SHORT") or not entrada or not tp or _sin_perfil:
        return {"aplica": False, "coin": coin, "regimen": lv["regimen"],
                "gamma_flip": lv["gamma_flip"], "call_wall": lv["call_wall"],
                "put_wall": lv["put_wall"],
                "velocidad_esperada": lv["velocidad_esperada"],
                "net_gex_musd": lv["net_gex_musd"],
                "nota": ("fila de vigilancia sin perfil de volumen (objetivo = precio): "
                         "es sesgo de direccion, no un setup de fade"
                         if _sin_perfil else
                         "sin direccion/objetivo definidos en la fila"),
                "estructura": _estructura_fila(coin, row)}

    tipo = "CONTINUACION" if modo == "TENDENCIA" else "REVERSION"
    v = validar_senal(coin, lado, entrada, tp, tipo=tipo,
                      origen="panel_vp", symbol=sym, log=True)
    if not v.get("aplica"):
        return v
    return {
        "aplica": True, "coin": coin,
        "veredicto": v["veredicto"], "score": v["score"],
        "regimen": v["regimen"], "tipo_senal": v["tipo_senal"], "lado": v["lado"],
        "velocidad_esperada": v["velocidad_esperada"],
        "net_gex_musd": v["net_gex_musd"],
        "gamma_flip": v["gamma_flip"], "dist_flip_pct": v["dist_flip_pct"],
        "call_wall": lv["call_wall"], "put_wall": lv["put_wall"],
        "tp_original": v["tp_original"], "tp_sugerido": v["tp_sugerido"],
        "sl_sugerido": v["sl_sugerido"],
        "muro_en_camino": v["muro_en_camino"], "cruza_flip": v["cruza_flip"],
        "motivos": v["motivos"], "penalizaciones": v["penalizaciones"],
        "stale": v["stale"], "edad_s": v["edad_s"],
        # __GEX_ZONAS_2026_09_09__
        "estructura": _estructura_fila(coin, row),
    }


# ---------------------------------------------------------------------------
# LOG PARA CALIBRACION
# ---------------------------------------------------------------------------
# Los cortes 70/45 de gex_filter.Umbrales son una hipotesis ordenada, NO un edge
# medido. Esto deja el rastro para moverlos donde la curva se separe de verdad:
# cada validacion se guarda con su score y su estructura. Cruzar despues contra
# el resultado real del trade (toco TP antes que SL).

def log_signal(v, origen="manual", symbol=""):
    rec = {
        "ts": time.time(), "origen": origen, "symbol": symbol,
        "coin": v.get("coin"), "lado": v.get("lado"),
        "tipo": v.get("tipo_senal"), "veredicto": v.get("veredicto"),
        "score": v.get("score"), "regimen": v.get("regimen"),
        "velocidad": v.get("velocidad_esperada"),
        "net_gex_musd": v.get("net_gex_musd"),
        "gamma_flip": v.get("gamma_flip"), "dist_flip_pct": v.get("dist_flip_pct"),
        "entrada": v.get("entrada"), "tp_original": v.get("tp_original"),
        "tp_sugerido": v.get("tp_sugerido"), "sl_sugerido": v.get("sl_sugerido"),
        "cruza_flip": v.get("cruza_flip"), "stale": v.get("stale"),
    }
    with open(_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    refresh_once()
    for c in COINS:
        lv = levels(c)
        if lv:
            print("%-4s spot %10.2f  flip %-10s  %-8s  %+7.1f M$/1%%  %-8s  [%s]" % (
                lv["coin"], lv["spot"],
                ("%.0f" % lv["gamma_flip"]) if lv["gamma_flip"] else "-",
                lv["regimen"], lv["net_gex_musd"], lv["velocidad_esperada"],
                ",".join(lv["exchanges"])))
            for w in lv["warnings"]:
                print("     ! " + w)
        else:
            print(c, "ERROR:", error(c))
