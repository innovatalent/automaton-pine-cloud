# -*- coding: utf-8 -*-
"""Gate de ENTRADA + regla de SALIDA por IMÁN DE LIQUIDACIÓN (pedido usuario 2026-08-25/26). Helpers compartidos para
que TODAS las estrategias operen HACIA la mayor liquidez, y CIERREN si el imán se invierte en contra + hay flujo en contra.
Usa liq_magnets (ΔOI+vol+delta). Cache 5min por símbolo. Fail-open: sin data no bloquea la entrada ni fuerza el cierre."""
import time

ENABLED = True
_CACHE = {}   # sym -> (ts, dir)


def magnet_dir(sym):
    """Dirección del imán de liquidación DOMINANTE ('LONG'/'SHORT'/None). Cache 5min."""
    now = time.time(); hit = _CACHE.get(sym)
    if hit and now - hit[0] < 300:
        return hit[1]
    d = None
    try:
        import liq_magnets as _LM
        d = (_LM.magnets(sym) or {}).get("dir")
    except Exception:
        d = None
    _CACHE[sym] = (now, d)
    return d


def ok(sym, side):
    """ENTRADA: True si el imán dominante va A FAVOR del lado (o sin data = fail-open, no bloquea).
    + SIDE-LOCK (2026-08-31): si el símbolo tiene lado fijo en symbol_config y `side` es el OPUESTO -> False SIEMPRE
    (regla dura, no fail-open). Como TODOS los brazos llaman `if not ok(sym,side): skip`, el lado queda airtight aquí."""
    if side_locked(sym, side):
        return False
    if not ENABLED:
        return True
    d = magnet_dir(sym)
    return (d == side) if d in ("LONG", "SHORT") else True


def against(sym, side):
    """True si el imán dominante va CLARAMENTE EN CONTRA del lado (se invirtió / desapareció a favor del otro lado)."""
    d = magnet_dir(sym)
    return d in ("LONG", "SHORT") and d != side


def _cvd_dir(K, n=6):
    """Dirección del flujo (delta close-location * volumen) sobre las últimas n velas. 'LONG'/'SHORT'/None."""
    if not K or len(K) < n + 1:
        return None
    s = 0.0
    for b in K[-n:]:
        try:
            h, l, c, v = float(b[2]), float(b[3]), float(b[4]), float(b[5])
        except Exception:
            continue
        rng = h - l
        s += (((c - l) - (h - c)) / rng * v) if rng > 0 else 0.0
    return "LONG" if s > 0 else ("SHORT" if s < 0 else None)


def flow_against(sym, side, K):
    """True si el flujo reciente (CVD 6 velas) va EN CONTRA del lado."""
    d = _cvd_dir(K)
    return d in ("LONG", "SHORT") and d != side


def should_close(sym, side, K):
    """SALIDA (pedido usuario): cerrar si el imán se INVIRTIÓ en contra Y el flujo también va en contra. Ambas condiciones."""
    if not ENABLED:
        return False
    return against(sym, side) and flow_against(sym, side, K)


# --- ESTADO DE MERCADO por SOTT (pedido usuario 2026-08-26: no entrar CONTRA el estado del mercado) ---
_SS_CACHE = {}   # (sym,tf) -> (ts, src)


def _sott_src(sym, tf):
    """SOTT src NUEVO (200/20) de la última barra en tf, vía direction_engine.sott_zone.src_new (Bybit). Cache 90s. None si falla."""
    key = (sym, tf); now = time.time(); hit = _SS_CACHE.get(key)
    if hit and now - hit[0] < 90:
        return hit[1]
    v = None
    try:
        import sys, os
        _d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "direction_engine")
        if _d not in sys.path:
            sys.path.insert(0, _d)
        import sott_zone as _SZ
        v = _SZ.src_new(sym, tf)
    except Exception:
        v = None
    _SS_CACHE[key] = (now, v)
    return v


_STATE_TFS = ("5", "15", "60", "240", "D")   # 5m, 15m, 1h, 4h, 1d (intervalos Bybit) — TODAS las TFs del SOTT


def sott_state_ok(sym, side):
    """ESTADO DE MERCADO: True si NO entrás CONTRA el estado = NO entrar continuación/momentum HACIA el extremo SOTT
    OPUESTO (LONG con SOTT en techo >1080 / SHORT con SOTT en piso <1020 = chase hacia el agotamiento; MEDIDO en TFs
    rápidas net −0.24% vs +0.18% neutro). Chequea TODAS: 5m+15m+1h + superiores 4h+1d. Fail-open sin data. Continuación/momentum (NO fade)."""
    if not ENABLED or side not in ("LONG", "SHORT"):
        return True
    for tf in _STATE_TFS:
        v = _sott_src(sym, tf)
        if v is None:
            continue                                          # fail-open por TF: sin data esa TF no bloquea
        if side == "LONG" and v > 1080:
            return False                                      # LONG hacia techo en cualquier TF = agotamiento
        if side == "SHORT" and v < 1020:
            return False                                      # SHORT hacia piso en cualquier TF = agotamiento
    return True


BLOCKED_SYMS = {"SOXLUSDT", "WLDUSDT", "ORDIUSDT"}   # bloqueados (pedido usuario 2026-08-26 "entradas sucias"): net-negativos
#                                                     # medidos (WLD −0.14 ambas mitades, SOXL −0.39, ORDI −0.30). NEAR NO
#                                                     # (E+0.14 n55 = positivo, solo racha reciente). Reversible: sacar de aquí.


_BLOCKED_BASES = {s.replace("USDT", "") for s in BLOCKED_SYMS}


def blocked(sym):
    """True si el símbolo está bloqueado para ENTRAR (activos net-negativos medidos). Agnóstico al formato (SOXL/SOXLUSDT)."""
    return str(sym).replace("USDT", "").replace("USD", "").replace(".P", "") in _BLOCKED_BASES


# --- SIDE-LOCK por símbolo (pedido usuario 2026-08-31 "TQQQ nunca L/S, dirección DEFINIDA, no dejes que pase") ---
# Fuente de verdad = forward_test/symbol_config.json {SYM:{side:"LONG"/"SHORT",...}}. Antes SOLO lo respetaba el runner
# (_symbol_config_ok); los daemons sueltos (mom/cont15/tqqq/etc) lo saltaban -> mom shorteó TQQQ. Ahora es GLOBAL: todos
# los brazos importan liq_gate, así que cableando el chequeo acá + una línea en cada arm, el lado queda AIRTIGHT.
import os as _os, json as _json
_SIDE_CFG = None
_SIDE_CFG_TS = 0.0


def _side_cfg():
    """{SYM: 'LONG'/'SHORT'} desde symbol_config.json. Cache 5min. Sin archivo = {} (no lockea nada). No hace red."""
    global _SIDE_CFG, _SIDE_CFG_TS
    now = time.time()
    if _SIDE_CFG is not None and now - _SIDE_CFG_TS < 300:
        return _SIDE_CFG
    cfg = {}
    try:
        p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "symbol_config.json")
        with open(p, "r", encoding="utf-8") as f:
            raw = _json.load(f)
        for k, v in raw.items():
            if isinstance(v, dict) and v.get("side") in ("LONG", "SHORT"):
                cfg[str(k)] = v["side"]
    except Exception:
        cfg = _SIDE_CFG if _SIDE_CFG is not None else {}   # si falla la lectura, conservar el último bueno (no desbloquear)
    _SIDE_CFG, _SIDE_CFG_TS = cfg, now
    return _SIDE_CFG


def forced_side(sym):
    """Lado OBLIGADO del símbolo por symbol_config ('LONG'/'SHORT'), o None si no tiene lock. Agnóstico a sufijo USDT."""
    c = _side_cfg()
    s = str(sym)
    return c.get(s) or c.get(s + "USDT") or c.get(s.replace("USDT", "") + "USDT")


def side_locked(sym, side):
    """True = HAY QUE BLOQUEAR la entrada: el símbolo tiene lado fijo y `side` es el OPUESTO.
    Ej TQQQ=LONG -> side_locked('TQQQUSDT','SHORT')=True, ('TQQQUSDT','LONG')=False. Sin lock = False.
    Es una regla de CONFIG dura (no depende de ENABLED ni de red): si el símbolo tiene lado definido, NUNCA el opuesto."""
    f = forced_side(sym)
    return f in ("LONG", "SHORT") and str(side).upper() in ("LONG", "SHORT") and str(side).upper() != f


def _bar_delta(b):
    """Delta de la vela (proxy CVD): ((close-low)-(high-close))/rango * volumen. + = comprador, − = vendedor."""
    try:
        h, l, c, v = float(b[2]), float(b[3]), float(b[4]), float(b[5])
    except Exception:
        return 0.0
    rng = h - l
    return (((c - l) - (h - c)) / rng * v) if rng > 0 else 0.0


def _flow2_against(K, side, n=2):
    """flow2 EN CONTRA: las últimas n velas CERRADAS tienen TODAS el delta en contra del lado (flujo SOSTENIDO contrario)."""
    if not K or len(K) < n:
        return False
    seg = K[-n:]
    cnt = sum(1 for b in seg if (_bar_delta(b) < 0) == (side == "LONG"))
    return cnt >= n


_FK_CACHE = {}   # (sym,tf) -> (ts, klines) para el CVD del TF superior


def _fk_klines(sym, interval, limit=60):
    """Klines Bybit v5 [t,o,h,l,c,v] para el CVD del TF superior. Cache 90s. None si falla."""
    key = (sym, interval); now = time.time(); hit = _FK_CACHE.get(key)
    if hit and now - hit[0] < 90:
        return hit[1]
    v = None
    try:
        import json as _j, urllib.request as _ur
        u = "https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=%s&limit=%d" % (sym, interval, limit)
        d = _j.load(_ur.urlopen(_ur.Request(u, headers={"User-Agent": "lg"}), timeout=10))
        lst = list(reversed(d.get("result", {}).get("list", []) or []))
        v = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in lst]
    except Exception:
        v = None
    _FK_CACHE[key] = (now, v)
    return v


def flow_ok(sym, side, K, htf="240"):
    """NO entrar EN CONTRA del FLUJO NETO (pedido usuario 2026-08-26). Vota 4 indicadores por lado: (1) LIQUIDEZ (imán)
    (2) CVD reciente 3 velas (3) FLOW2 2 velas sostenidas (4) CVD del TF SUPERIOR. Bloquea SOLO si hay MÁS en contra que
    a favor (los indicadores se contradicen seguido; exigirlos TODOS mata el 93% — la mayoría evita las entradas sucias
    sin matar el volumen). Fail-open por indicador (neutro/sin data no vota). K = velas CERRADAS del arm."""
    if not ENABLED or side not in ("LONG", "SHORT"):
        return True
    opp = "SHORT" if side == "LONG" else "LONG"
    forc = agst = 0

    def _tally(d):
        nonlocal forc, agst
        if d == side:
            forc += 1
        elif d == opp:
            agst += 1
    _tally(magnet_dir(sym))                                    # 1. liquidez (imán)
    # 2. CVD reciente (3 velas) = #1 DISCRIMINADOR (minado 1265 trades reales 2026-09-01: cvd_align WR 54% vs 32%) -> pesa x2
    _cvr = _cvd_dir(K, 3)
    if _cvr == side:
        forc += 2
    elif _cvr == opp:
        agst += 2
    _tally(_cvd_dir(_fk_klines(sym, htf, 60) or [], 3))        # 3. CVD del TF superior
    if _flow2_against(K, side, 2):                             # 4. flow2 (2 velas sostenidas)
        agst += 1
    elif _flow2_against(K, opp, 2):
        forc += 1
    return agst <= forc                                        # bloquea solo si el flujo NETO va en contra


def flow_ok_fetch(sym, side, base="5", htf="60"):
    """Como flow_ok pero busca solas las velas base (para el runner, que no tiene K a mano). Fail-open sin data."""
    K = _fk_klines(sym, base, 60)
    if not K:
        return True
    return flow_ok(sym, side, K, htf)


def z_extreme_block(sym, side, thr=2.5):
    """GATE z-extremo (pedido usuario 2026-08-28 "no ejecutar LONG con z≥+2.5 / SHORT con z≤−2.5"). VALIDADO en trades
    REALES (bt_zgate_realtrades): en MOM los bloqueados rinden OOS −0.29% (WR38) vs +0.78% (WR67) los que quedan, ambas
    mitades (IN bloq −1.65/WR29) = comprar la CIMA del pico de agotamiento en 5m/15m. Devuelve True = HAY QUE BLOQUEAR.
    z = SMA75 clásico (= indicador Z-Score Multi). Fail-open (False) sin data. Data Bybit 5m; 15m resampleado cada 3.
    NOTA: medido robusto SOLO en mom; en cont/cont15 no aporta -> cablear con criterio, no universal."""
    if not ENABLED or side not in ("LONG", "SHORT"):
        return False
    try:
        import statistics as _st
        K = _fk_klines(sym, "5", 240)                     # 75×15m = 225 velas 5m -> 240 alcanza
        if not K or len(K) < 226:
            return False
        c = [k[4] for k in K]; last = c[-1]

        def _z(cl):
            m = sum(cl) / len(cl); sd = _st.pstdev(cl)
            return (last - m) / sd if sd > 0 else 0.0
        z5 = _z(c[-75:]); z15 = _z([c[-1 - 3 * m] for m in range(0, 75)])
        if side == "LONG":
            return z5 >= thr or z15 >= thr
        return z5 <= -thr or z15 <= -thr
    except Exception:
        return False


MAXGAIN_SYMS = {"TQQQUSDT", "SNDKUSDT"}   # gestionar para MÁXIMA ganancia (pedido usuario 2026-08-26): dejar correr


def maxgain(sym):
    """True si el símbolo se gestiona en modo MAX-GANANCIA (trailing progresivo ancho, sin scratch en BE)."""
    return sym in MAXGAIN_SYMS


def maxgain_gap(sym, mfe, base):
    """Gap de trailing para MAX-GANANCIA (pedido usuario 2026-08-26): se APRIETA cuanto más alto el MFE -> el stop SUBE lo
    más cerca posible del máximo, para TOMAR el beneficio y NO devolverlo (ej. TQQQ +3.00 → lockea ~+2.73, no +1.89).
    Progresivo continuo: gap = max(0.20, 0.45 − 0.06·mfe). Al arrancar (+1%) ~0.39 (aire para desarrollarse); a +3% ~0.27;
    a +4.2%+ piso 0.20 (lockea a 0.2% del pico). Fuera de la lista devuelve el gap base (sin cambio)."""
    if sym not in MAXGAIN_SYMS:
        return base
    return max(0.20, 0.45 - 0.06 * mfe)


def regime_ok(Kc, n=14, base=50):
    """SYNC / REGIMEN de volatilidad (follow-through): True si el tape esta VIVO = ATR%(n) de la ultima barra CERRADA
    por ENCIMA de su media de las ultimas `base` barras. En CHOP (ATR% < media) las continuaciones mueren (MEDIDO 458
    trades: mom H2 +0.56->+0.73, cont15 ambas mitades +, keep ~47-68%). Adaptativo -> sirve en cualquier TF/activo.
    Kc = klines CERRADAS [t,o,h,l,c,(v)]. Devuelve None si no hay barras suficientes (el caller hace fail-open)."""
    m = n + base
    if not Kc or len(Kc) < m + 2:
        return None

    def _atrp(i):
        atr = sum(max(Kc[j][2] - Kc[j][3], abs(Kc[j][2] - Kc[j - 1][4]), abs(Kc[j][3] - Kc[j - 1][4]))
                  for j in range(i - n + 1, i + 1)) / n
        c = Kc[i][4]
        return (atr / c * 100) if c else 0.0

    last = len(Kc) - 1
    cur = _atrp(last)
    baseline = sum(_atrp(i) for i in range(last - base + 1, last + 1)) / base
    return cur > baseline


def sott_htf_ok(sym, side):
    """ESTADO HTF — validado SOLO para 'cont' (medición 2026-08-26 sobre 456 trades): NO entrar continuación HACIA el
    extremo SOTT OPUESTO en 4h/1d. cont con 4h/1d en contra = E+0.096 / 98% sucio (su PEOR subset). Fail-open.
    OJO: mom/cont15 NO deben usar esto (ahí el extremo = tendencia caliente, su MEJOR E — el bloqueo les baja la E)."""
    if not ENABLED or side not in ("LONG", "SHORT"):
        return True
    for tf in ("240", "D"):                                   # solo superiores: 4h, 1d
        v = _sott_src(sym, tf)
        if v is None:
            continue
        if side == "LONG" and v > 1080:
            return False
        if side == "SHORT" and v < 1020:
            return False
    return True


_YC5_CACHE = {}   # sym -> (ts, bool)


def yellow5m(sym):
    """CIRCULO AMARILLO A+ en 5m (= senal amarilla del indicador Pine "Z-Score Multi"): z(75) CRUZA por debajo de -2.5 en
    la ultima vela CERRADA + ESTRUCTURA (climax vol >=2.5x avg20  O  MFI oversold <25  O  sweep+reclaim del piso). LONG-only.
    Pedido usuario 2026-08-31 "ensenales esa senal a los brazos para que aprendan": se registra como FEATURE por entrada ->
    strategy_analytics/learn_weights miden si predice ganadoras. Cache 90s. Fail-safe False sin data (no rompe la entrada)."""
    now = time.time(); hit = _YC5_CACHE.get(sym)
    if hit and now - hit[0] < 90:
        return hit[1]
    res = False
    try:
        import statistics as _st
        K = _fk_klines(sym, "5", 240)                         # Bybit 5m [t,o,h,l,c,v] (cache propia de _fk_klines)
        if K and len(K) >= 100:
            Kc = K[:-1]                                        # descarta la vela en formacion (= barstate.isconfirmed)
            i = len(Kc) - 1
            c = [k[4] for k in Kc]; o = [k[1] for k in Kc]; h = [k[2] for k in Kc]
            l = [k[3] for k in Kc]; v = [k[5] for k in Kc]

            def _z(idx):
                w = c[idx - 75:idx]; m = sum(w) / 75.0; sd = _st.pstdev(w)
                return (c[idx] - m) / sd if sd > 0 else 0.0
            if _z(i - 1) <= -2.5 and _z(i) > -2.5:             # RECUPERACION (validado bt): z VUELVE cruzando -2.5 hacia ARRIBA (crossover)
                def _mfi_at(idx):
                    p = ng = 0.0
                    for j in range(idx - 13, idx + 1):
                        tp = (h[j] + l[j] + c[j]) / 3.0; tp0 = (h[j - 1] + l[j - 1] + c[j - 1]) / 3.0; mf = tp * v[j]
                        if tp > tp0:
                            p += mf
                        elif tp < tp0:
                            ng += mf
                    return 100.0 if ng <= 0 else 100.0 - 100.0 / (1.0 + p / ng)
                for k in (0, 1):                               # estructura en las ult 2 velas (el piso fue 1 vela ANTES de la recuperacion)
                    j = i - k
                    if j < 20:
                        continue
                    av = sum(v[j - 20:j]) / 20.0
                    vc = av > 0 and v[j] >= 2.5 * av
                    swl = min(l[j - 12:j]) if j >= 12 else min(l[:j] or [l[j]])
                    rng = max(h[j] - l[j], 1e-9); dw = (min(o[j], c[j]) - l[j]) / rng
                    tr = l[j] < swl and c[j] > swl and dw >= 0.45
                    if vc or _mfi_at(j) < 25 or tr:
                        res = True
                        break
    except Exception:
        res = False
    _YC5_CACHE[sym] = (now, res)
    return res
