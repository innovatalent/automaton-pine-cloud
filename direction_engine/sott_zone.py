# -*- coding: utf-8 -*-
"""SOTT -- Stochastic Optimized Trend Tracker (Anil Ozeksi / @KivancOzbilgic) como MAPA DE ZONA.

Replica FIEL el Pine v4:
  - stoch(close,high,low, periodK=500)  -> oscilador 0..100
  - k = VIDYA(stoch, smoothK=200)        (Var_Func: EMA CMO-adaptativa, CMO sobre 9)
  - src = k + 1000                       (banda baja 1020 = k<=20 COMPRA ; banda alta 1080 = k>=80 VENTA)
  - MAvg = VIDYA(src, length=2) ; OTT = trailing stop de OTT (percent=0.5) ; senal = cross(src, OTT[2])

Uso: pedido del usuario -> "compra cuando esta en la linea baja, venta cuando en la linea alta".
zone(symbol) devuelve {k, zone (COMPRA/VENTA/NEUTRO), ott, dir (1 up/-1 down), signal (BUY/SELL/None), cross}.
Datos publicos de Bybit (linear), SIN keys. Cache TTL para no golpear la API por cada candidato del panel.
"""
import json
import time
import urllib.request

PERIOD_K = 500
SMOOTH_K = 200
OTT_LENGTH = 2
OTT_PERCENT = 0.5
LOWER_BAND = 1020.0   # k <= 20  -> zona de COMPRA (sobreventa)
UPPER_BAND = 1080.0   # k >= 80  -> zona de VENTA  (sobrecompra)

_CACHE = {}           # (symbol, interval) -> (ts, result)
_TTL = 90.0


def _klines(symbol, interval="15", limit=1000):
    url = ("https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=%s&limit=%d"
           % (symbol, interval, limit))
    req = urllib.request.Request(url, headers={"User-Agent": "automaton-sott"})
    d = json.load(urllib.request.urlopen(req, timeout=12))
    lst = d.get("result", {}).get("list", []) or []
    lst = list(reversed(lst))  # Bybit da mas nuevo primero -> ordenar cronologico (viejo->nuevo)
    highs = [float(r[2]) for r in lst]
    lows = [float(r[3]) for r in lst]
    closes = [float(r[4]) for r in lst]
    return highs, lows, closes


def _stoch(close, high, low, n):
    """100*(close - lowest(low,n))/(highest(high,n)-lowest(low,n)); NaN->arrastra ultimo valido, warmup=50."""
    out = [0.0] * len(close)
    for i in range(len(close)):
        j0 = max(0, i - n + 1)
        hh = max(high[j0:i + 1]); ll = min(low[j0:i + 1])
        rng = hh - ll
        out[i] = 100.0 * (close[i] - ll) / rng if rng > 0 else (out[i - 1] if i else 50.0)
    return out


def _var_func(src, length):
    """VIDYA / Var_Func del Pine: EMA con alpha modulado por |CMO(9)|. Recursivo, arranca en src[0]."""
    valpha = 2.0 / (length + 1)
    out = [0.0] * len(src)
    ud = [0.0] * len(src)
    dd = [0.0] * len(src)
    for i in range(len(src)):
        if i > 0:
            ud[i] = src[i] - src[i - 1] if src[i] > src[i - 1] else 0.0
            dd[i] = src[i - 1] - src[i] if src[i] < src[i - 1] else 0.0
        vUD = sum(ud[max(0, i - 8):i + 1])
        vDD = sum(dd[max(0, i - 8):i + 1])
        den = vUD + vDD
        vCMO = (vUD - vDD) / den if den else 0.0
        f = valpha * abs(vCMO)
        out[i] = f * src[i] + (1.0 - f) * (out[i - 1] if i else src[i])
    return out


def _ott(mavg, percent):
    """Trailing OTT (mismo algoritmo del Pine). Devuelve (ott_series, dir_series)."""
    n = len(mavg)
    longStop = [0.0] * n; shortStop = [0.0] * n; dir_ = [1] * n; ott = [0.0] * n
    for i in range(n):
        fark = mavg[i] * percent * 0.01
        ls = mavg[i] - fark
        lsp = longStop[i - 1] if i else ls
        longStop[i] = max(ls, lsp) if mavg[i] > lsp else ls
        ss = mavg[i] + fark
        ssp = shortStop[i - 1] if i else ss
        shortStop[i] = min(ss, ssp) if mavg[i] < ssp else ss
        d = dir_[i - 1] if i else 1
        if d == -1 and mavg[i] > (shortStop[i - 1] if i else ss):
            d = 1
        elif d == 1 and mavg[i] < (longStop[i - 1] if i else ls):
            d = -1
        dir_[i] = d
        mt = longStop[i] if d == 1 else shortStop[i]
        ott[i] = mt * (200 + percent) / 200 if mavg[i] > mt else mt * (200 - percent) / 200
    return ott, dir_


def compute(highs, lows, closes):
    """Corre toda la cadena SOTT y devuelve el estado del ULTIMO bar cerrado."""
    st = _stoch(closes, highs, lows, PERIOD_K)
    k = _var_func(st, SMOOTH_K)
    src = [x + 1000.0 for x in k]
    mavg = _var_func(src, OTT_LENGTH)
    ott, dir_ = _ott(mavg, OTT_PERCENT)
    i = len(src) - 1
    ott2 = ott[i - 2] if i >= 2 else ott[i]     # el Pine plotea/senaliza contra OTT[2]
    ott2p = ott[i - 3] if i >= 3 else ott2
    kv = round(k[i], 2)
    # zona por banda
    if src[i] <= LOWER_BAND:
        zone = "COMPRA"
    elif src[i] >= UPPER_BAND:
        zone = "VENTA"
    else:
        zone = "NEUTRO"
    # senal de cruce (con OTT[2], como el indicador)
    signal = None
    if src[i] > ott2 and src[i - 1] <= ott2p:
        signal = "BUY"
    elif src[i] < ott2 and src[i - 1] >= ott2p:
        signal = "SELL"
    return {"k": kv, "src": round(src[i], 2), "ott": round(ott2, 2), "dir": dir_[i],
            "zone": zone, "signal": signal,
            "trend": "UP" if dir_[i] == 1 else "DOWN"}


def compute_series(highs, lows, closes):
    """Igual que compute() pero devuelve la SERIE completa por bar (para backtest). Cada item:
    {k, src, ott2 (OTT[2]), dir, zone, signal}. signal usa cruce de src contra OTT[2] como el Pine."""
    st = _stoch(closes, highs, lows, PERIOD_K)
    k = _var_func(st, SMOOTH_K)
    src = [x + 1000.0 for x in k]
    mavg = _var_func(src, OTT_LENGTH)
    ott, dir_ = _ott(mavg, OTT_PERCENT)
    out = []
    for i in range(len(src)):
        ott2 = ott[i - 2] if i >= 2 else ott[i]
        ott2p = ott[i - 3] if i >= 3 else ott2
        if src[i] <= LOWER_BAND:
            z = "COMPRA"
        elif src[i] >= UPPER_BAND:
            z = "VENTA"
        else:
            z = "NEUTRO"
        sig = None
        if i >= 1:
            if src[i] > ott2 and src[i - 1] <= ott2p:
                sig = "BUY"
            elif src[i] < ott2 and src[i - 1] >= ott2p:
                sig = "SELL"
        out.append({"k": k[i], "src": src[i], "ott2": ott2, "dir": dir_[i], "zone": z, "signal": sig})
    return out


def zone(symbol, interval="15"):
    """Estado SOTT del simbolo (cacheado). Nunca revienta -> {'zone':'NEUTRO','error':...} si falla."""
    key = (symbol, interval)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        highs, lows, closes = _klines(symbol, interval)
        if len(closes) < PERIOD_K + 50:
            res = {"zone": "NEUTRO", "k": None, "error": "pocas velas"}
        else:
            res = compute(highs, lows, closes)
        res["symbol"] = symbol
        res["interval"] = interval
    except Exception as e:
        res = {"zone": "NEUTRO", "k": None, "symbol": symbol, "error": str(e)[:80]}
    _CACHE[key] = (now, res)
    return res


# --- SOTT NUEVO (params del Pine/alert: %K=200, Suav=20, bandas 1020/1080). Solo la SRC (azul) para la ZONA. ---
NEW_PERIOD_K = 200
NEW_SMOOTH_K = 20


def src_new(symbol, interval="15"):
    """SOTT src (azul) del ULTIMO bar cerrado con los params NUEVOS (200/20 = los del alert/Pine). Zona: <1020 COMPRA /
    >1080 VENTA. None si pocas velas. Cache propia (no pisa la del SOTT viejo). Data Bybit (mismo _klines)."""
    key = ("srcnew", symbol, interval)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = None
    try:
        highs, lows, closes = _klines(symbol, interval, 300)     # NEW_PERIOD_K=200 -> con 300 velas alcanza (más rápido que 1000)
        if len(closes) >= NEW_PERIOD_K + 40:
            st = _stoch(closes, highs, lows, NEW_PERIOD_K)
            k = _var_func(st, NEW_SMOOTH_K)
            val = round(k[-1] + 1000.0, 2)      # src = k + 1000
    except Exception:
        val = None
    _CACHE[key] = (now, val)
    return val


def src_series(symbol, interval="60", n=14):
    """Últimos n valores de la SOTT src (200/20) = la TRAYECTORIA (para proyectar la oscilación techo↔piso, usuario 2026-09-03:
    'el mercado se mueve de arriba a abajo en el sott; la dirección se ve desde DÓNDE VIENE la confluencia'). Cache 90s."""
    key = ("srcser", symbol, interval, n)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    out = None
    try:
        highs, lows, closes = _klines(symbol, interval, 300)
        if len(closes) >= NEW_PERIOD_K + 40:
            st = _stoch(closes, highs, lows, NEW_PERIOD_K)
            k = _var_func(st, NEW_SMOOTH_K)
            out = [round(x + 1000.0, 2) for x in k[-n:]]
    except Exception:
        out = None
    _CACHE[key] = (now, out)
    return out


def _phase(s):
    """Fase de la oscilación SOTT desde la trayectoria (de dónde VIENE → a dónde va). LONG = viene del piso subiendo / está en
    el piso (rebota). SHORT = viene del techo bajando / está en el techo (cae). RANGO = oscila en el medio sin extremo.
    Devuelve (dir, nota)."""
    if not s or len(s) < 4:
        return (None, "sin datos")
    cur = s[-1]
    if cur <= LOWER_BAND:
        return ("LONG", "en el piso (rebota)")
    if cur >= UPPER_BAND:
        return ("SHORT", "en el techo (cae)")
    last_ext = None                              # el extremo MÁS RECIENTE que tocó = de dónde viene
    for v in reversed(s):
        if v <= LOWER_BAND:
            last_ext = "bottom"; break
        if v >= UPPER_BAND:
            last_ext = "top"; break
    slope = s[-1] - s[-4]                         # pendiente corta (subiendo/bajando)
    if last_ext == "bottom":
        return ("LONG", "viene del piso subiendo") if slope > -2 else ("RANGO", "volvió del piso, sin dirección")
    if last_ext == "top":
        return ("SHORT", "viene del techo bajando") if slope < 2 else ("RANGO", "volvió del techo, sin dirección")
    if slope >= 4:
        return ("LONG", "src subiendo al techo")
    if slope <= -4:
        return ("SHORT", "src bajando al piso")
    return ("RANGO", "oscila en el medio")


def sott_project(symbol):
    """PROYECCIÓN de dirección para la SESIÓN (usuario 2026-09-03): sintetiza la oscilación SOTT en las TFs LENTAS (1h/4h/1d,
    las que marcan dirección) desde su TRAYECTORIA → LONG / SHORT / RANGO. venta(techo)→SHORT · compra(piso)→LONG · sin
    extremo/medio→RANGO. Devuelve {dir, agree, phases:{tf:(dir,nota)}, why}."""
    tfs = [("1h", "60"), ("4h", "240"), ("1d", "D")]
    phases = {}
    for lbl, code in tfs:
        phases[lbl] = _phase(src_series(symbol, code, 14))
    dirs = [p[0] for p in phases.values() if p[0] and p[0] != "RANGO"]
    nL = dirs.count("LONG"); nS = dirs.count("SHORT")
    if nL > nS:
        proj = "LONG"; agree = nL
    elif nS > nL:
        proj = "SHORT"; agree = nS
    else:
        proj = "RANGO"; agree = 0
    why = " · ".join("%s %s" % (t, phases[t][0] or "—") for t, _ in tfs if phases[t][0])
    return {"dir": proj, "agree": agree, "phases": phases, "why": why}


# --- CONFLUENCIA SOTT MULTI-TF (pedido usuario 2026-09-02: "prioridad = confluencia de extremos SOTT anclada en 5m:
# 5m+15m / 5m+1h / 5m+1d; 3+ TFs mejor, para destacar activos operables"). Fuente = src_new (200/20 = alert/Pine).
_MTF_ORDER = [("5m", "5"), ("15m", "15"), ("1h", "60"), ("1d", "D")]


def sott_mtf_confluence(symbol):
    """Extremos SOTT (src_new) en 5m/15m/1h/1d. Confluencia OPERABLE = ANCLADA en 5m + al menos 1 TF superior
    (5m+15m / 5m+1h / 5m+1d). LONG = piso <1020 en esas TFs · SHORT = techo >1080. Devuelve el lado con MAS TFs
    en extremo (None si no hay confluencia 5m+HTF). n>=3 -> grade 'MAXIMA'. Dict: {side, tfs:[...], htf:[...], n, grade}."""
    vals = {}
    for lbl, code in _MTF_ORDER:
        try:
            v = src_new(symbol, code)
        except Exception:
            v = None
        if v is not None:
            vals[lbl] = v
    floor_tfs = [lbl for lbl, v in vals.items() if v < LOWER_BAND]   # <1020 barato  -> LONG
    ceil_tfs  = [lbl for lbl, v in vals.items() if v > UPPER_BAND]   # >1080 caro    -> SHORT

    def _pack(tfs, side):
        if "5m" not in tfs:                      # ancla obligatoria en 5m (pedido usuario)
            return None
        htf = [t for t in tfs if t != "5m"]
        if not htf:                              # 5m solo no cuenta (necesita 5m + al menos 1 TF superior)
            return None
        n = len(tfs)
        return {"side": side, "tfs": [t for t, _ in _MTF_ORDER if t in tfs], "htf": htf,
                "n": n, "grade": ("MAXIMA" if n >= 3 else "ALTA")}

    cands = [c for c in (_pack(floor_tfs, "LONG"), _pack(ceil_tfs, "SHORT")) if c]
    if not cands:
        return None
    return max(cands, key=lambda c: c["n"])


# --- UNIVERSO DINÁMICO de Bybit (pedido usuario 2026-08-25): perps que se MUEVEN >=1%/dia + alta mcap/liquidez O stocks/commodities ---
_MOVERS_CACHE = {"ts": 0.0, "syms": []}
_MOVERS_TTL = 600.0                     # 10 min (los stats de 24h no cambian rápido)
STOCK_COMMO = {"NVDAUSDT", "TSLAUSDT", "AAPLUSDT", "METAUSDT", "MSTRUSDT", "COINUSDT", "TQQQUSDT", "SOXLUSDT",
               "SNDKUSDT", "PLTRUSDT", "AMDUSDT", "GOOGLUSDT", "AMZNUSDT", "MSFTUSDT", "HOODUSDT", "MUUSDT",
               "CLUSDT", "XAUUSDT", "XAGUSDT", "SKHYNIXUSDT", "NFLXUSDT", "AVGOUSDT",
               # __COMMODITIES_2026_09_22__ (pedido del usuario): los que Bybit tiene
               # con turnover real. Medido hoy: CL 160 M, XAU 172 M, XAG 57 M ya estaban;
               # faltaban XAUT (41 M, oro tokenizado) y SPX (12 M, indice). Los demas del
               # rubro NO entran por iliquidos: SPY 1,4 M · SPXL 0,5 M · UVXY 0,1 M ·
               # TLT y ZS 0,0 M. SPX queda por debajo del umbral automatico de 20 M, asi
               # que tiene que ir explicito o el roster no lo toma.
               # CORRECCION en el acto: SPXUSDT NO es el indice S&P sino el memecoin
               # SPX6900 (cotiza 0,54 y su baseCoin es SPX). Lo agregue por el nombre y
               # lo saco al verificar el precio: Bybit no lista el indice S&P.
               "XAUTUSDT"}   # tokenized stocks + commodities en Bybit

# Top-20 cripto por market cap (perps USDT, excluye stablecoins). Curado/actualizable.
TOP20_CRYPTO = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT", "TRXUSDT",
                "LINKUSDT", "AVAXUSDT", "SUIUSDT", "TONUSDT", "BCHUSDT", "DOTUSDT", "LTCUSDT", "UNIUSDT",
                "NEARUSDT", "APTUSDT", "XLMUSDT", "HBARUSDT"]


def roster():
    """Universo OPERABLE del dashboard (pedido usuario 2026-08-26): SOLO acciones + commodities + top-20 cripto.
    PRIORIDAD TAO y TQQQ (siempre incluidos y PRIMEROS). Orden: TAO, TQQQ, resto top-20 cripto, acciones/commodities."""
    out = ["TAOUSDT", "TQQQUSDT"]                              # prioridad: TAO (cripto principal) + TQQQ (equity apalancado)
    for s in TOP20_CRYPTO + sorted(STOCK_COMMO):
        if s not in out:
            out.append(s)
    # __ROSTER_AMPLIADO_2026_09_08__: se suman los perps LIQUIDOS que faltaban. El panel
    # miraba 43 simbolos mientras Bybit tiene 63 con turnover >= 20M USD/dia, o sea que
    # 36 candidatos liquidos nunca se evaluaban. Mas universo = mas oportunidades con la
    # MISMA vara (los filtros validados no se aflojan). Cache 10 min.
    try:
        for s in _liquidos_extra():
            if s not in out:
                out.append(s)
    except Exception:
        pass
    return out


_LIQ_CACHE = {"ts": 0.0, "syms": []}
LIQ_MIN_TURNOVER_ROSTER = 2e7      # 20M USD/dia
LIQ_MAX_EXTRA = 40


def _liquidos_extra():
    """Perps USDT de Bybit con turnover alto que no estan en el roster fijo."""
    now = time.time()
    if _LIQ_CACHE["syms"] and now - _LIQ_CACHE["ts"] < 600:
        return _LIQ_CACHE["syms"]
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    req = urllib.request.Request(url, headers={"User-Agent": "automaton-sott"})
    d = json.load(urllib.request.urlopen(req, timeout=15))
    picks = []
    for t in d.get("result", {}).get("list", []) or []:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        try:
            tv = float(t.get("turnover24h") or 0)
        except (TypeError, ValueError):
            continue
        if tv >= LIQ_MIN_TURNOVER_ROSTER:
            picks.append((tv, sym))
    picks.sort(reverse=True)
    _LIQ_CACHE["syms"] = [s for _tv, s in picks[:LIQ_MAX_EXTRA]]
    _LIQ_CACHE["ts"] = now
    return _LIQ_CACHE["syms"]


def bybit_movers(min_range=1.0, min_turn=5e6, min_turn_sc=3e6, max_syms=70):
    """Lista dinámica de perps USDT de Bybit que se MUEVEN >=min_range%/dia (rango 24h) Y son de alta liquidez/mcap
    (turnover>=min_turn) O son acciones/commodities (bar más bajo min_turn_sc). Ordenada por turnover. Cache 10min."""
    now = time.time()
    if _MOVERS_CACHE["syms"] and now - _MOVERS_CACHE["ts"] < _MOVERS_TTL:
        return _MOVERS_CACHE["syms"]
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        req = urllib.request.Request(url, headers={"User-Agent": "automaton-sott"})
        d = json.load(urllib.request.urlopen(req, timeout=15))
        picks = []
        for t in d.get("result", {}).get("list", []) or []:
            s = t.get("symbol", "")
            if not s.endswith("USDT"):
                continue
            try:
                last = float(t.get("lastPrice") or 0); hi = float(t.get("highPrice24h") or 0)
                lo = float(t.get("lowPrice24h") or 0); turn = float(t.get("turnover24h") or 0)
            except Exception:
                continue
            if last <= 0:
                continue
            rng = (hi - lo) / last * 100.0
            if rng < min_range:
                continue
            is_sc = s in STOCK_COMMO
            if turn >= min_turn or (is_sc and turn >= min_turn_sc):
                picks.append((s, turn))
        picks.sort(key=lambda x: -x[1])
        syms = [s for s, _ in picks[:max_syms]]
        if syms:
            _MOVERS_CACHE["ts"] = now; _MOVERS_CACHE["syms"] = syms
        return syms
    except Exception:
        return _MOVERS_CACHE["syms"]


if __name__ == "__main__":
    import sys
    for s in (sys.argv[1:] or ["BTCUSDT", "SOLUSDT", "TAOUSDT"]):
        print(s, zone(s))
