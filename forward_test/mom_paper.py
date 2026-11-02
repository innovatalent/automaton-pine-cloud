# -*- coding: utf-8 -*-
"""BRAZO MOMENTUM 1h unificado (2026-08-21, usuario "armalo, aplicalo a todas"). La logica que SI da MFE>=1% limpio:
MOMENTUM/CONTINUACION en TIMEFRAME ALTO (1h), NO reversion ni 5m/15m (medido: 5m/15m mas chico y NO robusto; 1h robusto).
Universo = cripto (Binance 1h) + TQQQ/ETFs/acciones (bitunix 1h). Entrada LONG = ignicion O breakout O reclaim de
continuacion (las 3 OOS-robustas en cripto 1h y TQQQ 1h). Salida = trailing de continuacion + SL estructural + timestop.
Espejo ALEATORIO. DB mom.db. Daemon 3min. Sincroniza via sync_dedicated_arms (combo lo corre c/ciclo) -> /forward.
Medido: cripto 1h ignicion+breakout E+0.97% MFEprom1.43% reach1 59% OOS-OK; TQQQ 1h reclaim E+0.85% reach1 75% OOS-OK."""
import os, sqlite3, json, time, random
import urllib.request as _ur
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "mom.db")
ZLB, MAXB, FEE = 50, 12, 0.08
TRAIL_ACT, TRAIL_GB, SLCAP, SWING = 1.0, 0.3, 3.0, 10
PROT_ACT = 0.7    # A/B sombra 'mom_prot': activa el trailing en +0.7% (protege el hueco 0.7-1% que mom deja ir al SL)
PROT_ACT2 = 0.5   # A/B sombra 'mom_prot2': activa en +0.5% (más agresivo; el backtest lo daba mejor, medir si scratchea)
MAX_STALE_MIN = 75.0   # 1h fresco
_KC = {}

# --- Telegram + voz para la entrada momentum (mismo canal que zscore_alert) ---
import sys as _sys

__ALERT_HUB_PATCHED__ = True   # parche alert_hub 2026-09-04 (no borrar: idempotencia)
def _HUB_OK(prefix, side, sym):
    """Puerta única de alertas: sólo hablan/mandan las celdas (prefijo, lado) validadas por alert_hub. Fail-open."""
    try:
        import alert_hub as _AH
        import time as _t
        return _AH.gate("%s-%s-%s-%d" % (prefix, sym, side, int(_t.time())), sym, side)
    except Exception:
        return True
ROOT = os.path.dirname(HERE)
for _p in (ROOT, os.path.join(ROOT, "direction_engine")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
os.environ.setdefault("ENGINE_TG", "1")
try:
    import telegram_notify as _TG
except Exception:
    _TG = None
VOICE = os.path.join(ROOT, "direction_engine", "zscore_alerts.json")


MOM_ALERT_ON = False   # 2026-08-28 (pedido usuario "alertas unificadas"): SILENCIADO. El brazo sigue operando/midiendo;
                       # la ÚNICA alerta voz+telegram es la UNIFICADA (sott_combo_alert.scan_unified).


def _notify(sym, side, which):
    """Avisa la ENTRADA momentum 1h por Telegram + cola de voz. SILENCIADO (MOM_ALERT_ON=False); el brazo sigue operando."""
    if not _HUB_OK("mom", side, sym): return
    if not MOM_ALERT_ON:
        return
    if _AQ is not None and not _AQ.alert_ok(sym):
        return
    sp = sym.replace("USDT", "")
    tipo = {"ign": "ignicion", "brk": "breakout", "rec": "reclaim"}.get(which, "momentum")
    word = "long" if side == "LONG" else "short"
    if _TG is not None:
        try:
            _TG._send("🚀 <b>ENTRADA MOMENTUM 1h</b>\n%s <b>%s</b> — %s (1h)\ntendencia + volumen a favor" % (sp, side, tipo),
                      force=True, zalert=True)
        except Exception:
            pass
    try:
        alerts = json.load(open(VOICE)) if os.path.exists(VOICE) else []
        alerts.append({"id": "mom-%s-%d" % (sym, int(time.time())), "asset": sym, "side": side,
                       "text": "%s en %s, entrada momentum en una hora, %s" % (word, sp, tipo), "ts": int(time.time())})
        json.dump(alerts[-30:], open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass

# 2026-08-21 (usuario "keep WR>=60"): universo = SOLO los de WR>=60% + E>0 + MFE>=1% (medido bt_asset_mfe).
CRYPTO = ["ENAUSDT", "LDOUSDT", "ORDIUSDT", "NEARUSDT", "INJUSDT", "WLDUSDT", "STXUSDT", "APTUSDT",
          "GALAUSDT", "OPUSDT", "ARBUSDT", "HBARUSDT", "XLMUSDT", "UNIUSDT",
          # __MOM_UNIVERSE_WIDE_2026_09_05__ (usuario: "más frecuencia"): +18 monedas líquidas de Bybit (turnover ≥ 20 M, listadas en Binance).
          # Prueban en PAPER; el motor sólo las lleva a real cuando el gate por activo (n>=8 y E>0 en mom.db) lo permite.
          "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ADAUSDT", "LINKUSDT", "TAOUSDT",
          "LTCUSDT", "ZECUSDT", "DASHUSDT", "XMRUSDT", "ONDOUSDT", "AVAXUSDT", "DOTUSDT", "AAVEUSDT"]  # __MOM_UNIVERSE_2026_09_05__: +8 monedas del replay (prueban en paper; real sólo con n>=8 y E>0)   # WR 60-69%, E+0.24 a +1.32, MFE>=1.06
ETFS = ["TQQQUSDT", "SOXLUSDT"]   # ETF 3x indice/sector (WR~64, alto MFE); los 1x (QQQ/SPY/...) bajo WR -> fuera
STOCKS = []      # acciones individuales: WR<60 o muestra fina -> fuera
COMMOD = []      # commodities: WR<60 / MFE<1 -> fuera
NONCRYPTO = ETFS + STOCKS + COMMOD   # bitunix 1h (solo horario de mercado)


def _binance1h(sym, limit=400):
    now = time.time(); hit = _KC.get(sym)
    if hit and now - hit[0] < 90:
        return hit[1]
    try:
        u = "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=1h&limit=%d" % (sym, limit)
        r = json.load(_ur.urlopen(_ur.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=12))
        K = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in r]
        _KC[sym] = (now, K); return K
    except Exception:
        return hit[1] if hit else None


def _bx1h(sym, limit=300):
    now = time.time(); hit = _KC.get("bx:" + sym)
    if hit and now - hit[0] < 90:
        return hit[1]
    try:
        import bitunix as BX
        K = BX.klines(sym, "1h", limit)
        K = [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in K] if K else None
        _KC["bx:" + sym] = (now, K); return K
    except Exception:
        return hit[1] if hit else None


def _k1h(sym, is_stock):
    return _bx1h(sym) if is_stock else _binance1h(sym)


def _fresh(K):
    if not K or len(K) < 2:
        return False
    ts = K[-1][0]; ts = ts / 1000.0 if ts > 1e12 else ts
    return (time.time() - ts) / 60.0 <= MAX_STALE_MIN + 60


def _ema(a, n):
    k = 2.0 / (n + 1); e = a[0]
    for x in a[1:]:
        e = x * k + e * (1 - k)
    return e


def _feat(Kc):
    o = [x[1] for x in Kc]; h = [x[2] for x in Kc]; l = [x[3] for x in Kc]; c = [x[4] for x in Kc]; v = [x[5] for x in Kc]
    n = len(c)
    ema = _ema(c[-(ZLB + 1):], ZLB) if n >= ZLB else c[-1]
    ema3 = _ema(c[-(ZLB + 4):-3], ZLB) if n >= ZLB + 3 else ema
    avgv = sum(v[-21:-1]) / 20.0 if n >= 21 else 0.0
    volr = (v[-1] / avgv) if avgv > 0 else 0.0
    rng = max(h[-1] - l[-1], 1e-9)
    body_up = (c[-1] - l[-1]) / rng
    dl = v[-1] * (c[-1] - o[-1]) / rng
    hh = max(h[-21:-1]) if n >= 21 else 1e18
    ll = min(l[-21:-1]) if n >= 21 else -1e18
    body_dn = (h[-1] - c[-1]) / rng
    trendup = c[-1] > ema and ema >= ema3
    trenddn = c[-1] < ema and ema <= ema3
    # LONG
    ignL = trendup and body_up >= 0.7 and volr >= 1.5 and dl > 0
    brkL = c[-1] > hh and volr >= 1.3 and c[-1] > ema
    recL = trendup and c[-1] > o[-1] and c[-2] < o[-2] and volr >= 1.2
    # SHORT (espejo breakdown; validado en TQQQ/QQQ, medido forward en el resto)
    ignS = trenddn and body_dn >= 0.7 and volr >= 1.5 and dl < 0
    brkS = c[-1] < ll and volr >= 1.3 and c[-1] < ema
    recS = trenddn and c[-1] < o[-1] and c[-2] > o[-2] and volr >= 1.2
    if ignL or brkL or recL:
        return dict(sig=True, side="LONG", which=("ign" if ignL else "brk" if brkL else "rec"))
    if ignS or brkS or recS:
        return dict(sig=True, side="SHORT", which=("ign" if ignS else "brk" if brkS else "rec"))
    return dict(sig=False, side=None, which="")


try:
    import asset_quality as _AQ
except Exception:
    _AQ = None


def _syms():
    ok = (lambda s: _AQ.mfe_ok(s)) if _AQ else (lambda s: True)
    return [(s, False) for s in CRYPTO if ok(s)] + [(s, True) for s in NONCRYPTO if ok(s)]


def _db():
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT, ts_open TEXT, arm TEXT,
        symbol TEXT, side TEXT, entry REAL, entry_bar_ts TEXT, sl REAL, status TEXT, ts_close TEXT, exit_px REAL,
        pnl_pct REAL, mfe REAL, mae REAL, clean INTEGER, reason TEXT)""")
    return c


def _iso():
    return datetime.now(timezone.utc).isoformat()


def _open(con, arm, sym, side, entry, bar_ts, Kc):
    swlo = min(x[3] for x in Kc[-SWING:]); swhi = max(x[2] for x in Kc[-SWING:])
    sl = max(swlo, entry * (1 - SLCAP / 100)) if side == "LONG" else min(swhi, entry * (1 + SLCAP / 100))
    con.execute("INSERT INTO trades(ts_open,arm,symbol,side,entry,entry_bar_ts,sl,status,mfe,mae) "
                "VALUES(?,?,?,?,?,?,?, 'OPEN',0,0)", (_iso(), arm, sym, side, entry, bar_ts, sl))


LIQ_MAGNET_GATE = True   # 2026-08-25 (pedido usuario): mom solo abre HACIA el imán de liquidación dominante.
# Medido/OOS: WR a-favor 67% vs 49% en contra (robusto en ambas mitades); corta las perdedoras que pelean el imán
# (GALA/TQQQ/ARB actuales entraron TODAS en contra). E ~estable +1.28 (saca la lotería en-contra). LIQ_MAGNET_GATE=False revierte.


# __LIQFLIP_GEMELOS_2026_09_23__ (iter.160) — INTERRUPTOR DE `liq_flip`, MEDIDO Y REFUTADO.
# True = comportamiento de hoy (NO cambia nada). False = apaga la salida por imán invertido.
# El propio motor corrió el experimento pareado: existen 195 GEMELOS mom<->mom_rand con el
# MISMO symbol+entry_bar_ts+side y `entry`/`sl` IDÉNTICOS, gestionados por el MISMO bucle.
# Difieren en UNA compuerta: la de abajo. Cuando `liq_flip` NO dispara, D = pnl(mom)-pnl(rand)
# es 0.0 EXACTO en los 164 pares (47 SHORT + 117 LONG) -> el pareo es limpio bit a bit.
# Cuando dispara (16 de 63 SHORT, 15 de 132 LONG), el efecto causal es NEGATIVO EN LOS DOS LADOS:
#     SHORT  D = -0,0988 pp/op sobre el brazo   (los 16 cortados: -0,3889 pp cada uno)
#     LONG   D = -0,0393 pp/op sobre el brazo   (los 15 cortados: -0,3459 pp cada uno)
# El gemelo, dejado correr, salió por `trail` en 10 de 16 (SHORT): corta 2,5 ganadoras por
# perdedora. Y el daño está TODO en el corte temprano: liq_flip de <1 h D=-0,5778 (SHORT) /
# -1,1676 (LONG); de >=1 h D=+0,0268 / +0,3731. Signos OPUESTOS en los dos lados.
# Causa estructural: `after` está vacío la 1a hora (velas de 1 h) -> 11 de 16 disparan con
# `mfe=mae=0`, o sea CERO barras examinadas: no es una salida, es una RETRACCIÓN DE LA ENTRADA.
# Placebo balanceado (2.000 sorteos de 15 de 53 con las mismas edades): el efecto real cae en
# el percentil 61,0 % -> la SELECCIÓN del imán no se distingue de elegir al azar.
# Medición: bt_liqflip_gemelos_20260923.py · verificador 51 OK/0 MAL · biblioteca/160.
# NO se apagó acá: apagar una salida viva es decisión HUMANA (MISION §3). Poner False y listo.
#
# __LIQFLIP_EDAD_2026_09_23__ (iter.161) — SOLO COMENTARIO, NO CAMBIA NINGÚN COMPORTAMIENTO.
# NO HAY INTERRUPTOR DE GUARDA DE EDAD, Y ESTÁ MEDIDO POR QUÉ NO LO HAY. Se midió el
# contrafáctico EXACTO de una guarda `edad >= K` para K en {1 h, 2 h, 3 h} sobre los MISMOS
# 195 gemelos (suprimir el disparo convierte a `mom` en su gemelo, así que el contrafáctico
# sale del pareo sin simulador). NINGUNA K sobrevive la regla pre-registrada, 0 de 3:
#     SHORT  K=1/2/3 h -> D = +0,0021 pp  IC95 [-0,0978 ; +0,1012]  n_eventos = 5
#     LONG   K=1/2 h   -> D = +0,0226 pp  IC95 [-0,0164 ; +0,0699]  n_eventos = 8
#     LONG   K=3 h     -> D = +0,0138 pp  IC95 [-0,0176 ; +0,0568]  n_eventos = 6
# Los 6 IC contienen el 0. El TECHO de la guarda POR ENCIMA DE APAGAR es +0,0021 (SHORT) /
# +0,0226 (LONG) pp: el 97,9 % (SHORT) y el 63,5 % (LONG) de lo que la guarda "mejora" es
# dejar de perder, que es exactamente lo que da apagarla. LA DECISIÓN ES BINARIA, y HUMANA.
# Y el corte por edad de iter.160 resultó ser, en su mayor parte, un enunciado sobre el
# GEMELO y no sobre `liq_flip`: del salto D_joven - D_viejo, el 95,9 % (LONG) y el 63,4 %
# (SHORT) viene de lo que hizo la operación NO TRATADA, no del brazo tratado — en LONG
# `pnl(mom)` es prácticamente igual en los dos baldes (-0,7324 contra -0,6687).
# Medición: bt_liqflip_edad_20260923.py · verificador 69 OK/0 MAL/8 tripwires · biblioteca/161.
LIQ_FLIP_EXIT = True


def _magnet_ok(sym, side):
    """True si el imán de liquidación dominante (liq_magnets) va A FAVOR del lado. Fail-open si no hay data (no bloquea)."""
    if not LIQ_MAGNET_GATE:
        return True
    try:
        import liq_magnets as _LM
        m = _LM.magnets(sym) or {}; d = m.get("dir")
        # __MAGNET_FAILOPEN_2026_09_06__: tras una subida fuerte NO quedan pools arriba (up=[]) y todos los pools son las liquidaciones de los longs abajo →
        # dir=SHORT bloqueaba TODA ignición LONG en tendencia (05-09: UNI/OP/ARB/XMR/ENA/HBAR, 7 de 7). Sin pools del lado favorable no hay
        # información de imán → fail-open. Sólo bloquea cuando hay pools de ambos lados y el imán dominante va en contra.
        fav = m.get("up") if side == "LONG" else m.get("down")
        if not fav:
            return True
        if d in ("LONG", "SHORT"):
            return d == side
    except Exception:
        pass
    return True


_GLOG = {}


def _glog(sym, side, f, gate):
    """__MOM_GATE_LOG_2026_09_06__: registra (1 vez por símbolo/gate/hora) qué gate bloqueó una señal MOM → mom_gates.log (diagnóstico 'por qué no operó')."""
    import time as _t, os as _o
    k = (sym, gate, int(_t.time() // 3600))
    if k in _GLOG:
        return
    _GLOG[k] = 1
    try:
        with open(_o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "mom_gates.log"), "a", encoding="utf-8") as fh:
            fh.write("%s %s %s %s bloqueado por %s\n" % (_t.strftime("%Y-%m-%d %H:%M:%S"), sym, side, f.get("which", ""), gate))
    except Exception:
        pass


def _scan(con):
    op = 0
    try:                                           # __MOM_TIPO_2026_09_06__: columna tipo (idempotente)
        con.execute("ALTER TABLE trades ADD COLUMN tipo TEXT")
    except Exception:
        pass
    for sym, is_stock in _syms():
        try:
            import liq_gate as _lgb
            if _lgb.blocked(sym):
                continue                      # activo bloqueado (entradas sucias net-negativas: SOXL/WLD/ORDI). GATE 2026-08-26.
            K = _k1h(sym, is_stock)
            if not K or len(K) < ZLB + 25 or not _fresh(K):
                continue
            Kc = K[:-1]; bar_ts = str(Kc[-1][0]); entry = K[-1][4]
            f = _feat(Kc)
            if not f["sig"]:
                continue
            if con.execute("SELECT 1 FROM trades WHERE symbol=? AND arm='mom' AND status='OPEN'", (sym,)).fetchone():
                continue
            if con.execute("SELECT 1 FROM trades WHERE symbol=? AND arm='mom' AND entry_bar_ts=?", (sym, bar_ts)).fetchone():
                continue
            mside = f.get("side") or "LONG"
            if _lgb.side_locked(sym, mside):
                _glog(sym, mside, f, "side_lock"); continue                      # SIDE-LOCK symbol_config (TQQQ=LONG, COTI=SHORT...): NUNCA el lado opuesto. GATE 2026-08-31.
            if not _magnet_ok(sym, mside):
                _glog(sym, mside, f, "magnet"); continue                      # imán de liquidación EN CONTRA -> no abrir (ni mom ni mom_rand). GATE 2026-08-25.
            if not _lgb.flow_ok(sym, mside, Kc, "240"):
                _glog(sym, mside, f, "flow"); continue                      # NO entrar EN CONTRA de liquidez/CVD/flow2/CVD-TF (evita entradas sucias). GATE 2026-08-26.
            if _lgb.z_extreme_block(sym, mside):
                _glog(sym, mside, f, "z_extreme"); continue                      # z (SMA75 5m/15m) YA ≥+2.5 (LONG) / ≤−2.5 (SHORT) = comprar la cima del pico de
                #  agotamiento. VALIDADO en trades reales de mom (bloq OOS −0.29/WR38 vs +0.78/WR67, ambas mitades). GATE 2026-08-28.
            # (2026-08-26) SIN gate de estado SOTT en mom: MEDIDO que mom HACIA el extremo (FAST_OPP) es su MEJOR bucket
            # (E+1.46) — el extremo = tendencia caliente para surfear, no agotamiento. El imán de liquidez SÍ queda.
            _open(con, "mom", sym, mside, entry, bar_ts, Kc); op += 1
            try:                                   # __MOM_TIPO_2026_09_06__: tipo de gatillo (ign +2.09 / brk +1.33 / rec +0.58 en el espejo) para la jerarquía del motor
                con.execute("UPDATE trades SET tipo=? WHERE arm='mom' AND symbol=? AND entry_bar_ts=?", (f.get("which", ""), sym, bar_ts))
            except Exception:
                pass
            _open(con, "mom_prot", sym, mside, entry, bar_ts, Kc)   # A/B SOMBRA (2026-08-27, "modificar pto 1"): MISMA
            _open(con, "mom_prot2", sym, mside, entry, bar_ts, Kc)  #  entrada que mom pero el trailing activa antes (0.7 /
            #  0.5) para proteger el hueco 0.7-1%. Mismas entradas -> comparación limpia sin costo de muestra. Paper puro
            #  (no en SELECTED_ARMS -> el ejecutor los saltea). Compará strategy_mom vs _prot(0.7) vs _prot2(0.5) en /forward.
            _notify(sym, mside, f.get("which", ""))   # Telegram + voz de la entrada momentum (LONG o SHORT)
            rside = "LONG" if random.Random(sym + bar_ts + "mom").random() < 0.5 else "SHORT"
            if not _lgb.side_locked(sym, rside):          # el control aleatorio TAMBIÉN respeta el side-lock (TQQQ nunca SHORT). GATE 2026-08-31.
                _open(con, "mom_rand", sym, rside, entry, bar_ts, Kc)
        except Exception:
            continue
    con.commit(); return op


def _manage(con):
    cl = 0
    import liq_gate as _lg
    for tid, arm, sym, side, entry, ebar, sl in con.execute(
            "SELECT id,arm,symbol,side,entry,entry_bar_ts,sl FROM trades WHERE status='OPEN'").fetchall():
        try:
            is_stock = sym in NONCRYPTO
            K = _k1h(sym, is_stock)
            if not K:
                continue
            after = [x for x in K[:-1] if str(x[0]) > str(ebar)]
            cmfe = cmae = 0.0; peak = 0.0; active = False; exitpx = None; reason = None; bars = 0
            for b in after:
                bars += 1; hi, lo, c = b[2], b[3], b[4]
                fav = ((hi - entry) / entry * 100) if side == "LONG" else ((entry - lo) / entry * 100)
                adv = ((lo - entry) / entry * 100) if side == "LONG" else ((entry - hi) / entry * 100)
                cmfe = max(cmfe, fav); cmae = min(cmae, adv)
                # FIX 2026-08-24 (giveback): antes el SL estructural se evaluaba ANTES del trailing en la MISMA barra
                # -> en reversiones volatiles (high a favor, low al SL) ganaba el SL y DEVOLVIA la ganancia ya lograda
                # (mfe ~2% cerraba en -SL). Ahora, una vez +TRAIL_ACT, se RATCHETEA el stop al piso (peak - TRAIL_GB)
                # ANTES del check de stop -> el trade que ya fue a +1%+ sale en GANANCIA, no en perdida. El SL
                # estructural solo rige ANTES de +TRAIL_ACT (riesgo temprano intacto). Solo ratchea a favor.
                _act = PROT_ACT2 if arm == "mom_prot2" else (PROT_ACT if arm == "mom_prot" else TRAIL_ACT)   # A/B: 0.5 / 0.7 / 1.0
                if not active and cmfe >= _act:
                    active = True
                if active:
                    peak = max(peak, cmfe)
                    _gap = _lg.maxgain_gap(sym, peak, TRAIL_GB)   # MAX-GANANCIA (TQQQ/SNDK): trail progresivo ancho, deja correr
                    _lockpx = entry * (1 + (peak - _gap) / 100) if side == "LONG" else entry * (1 - (peak - _gap) / 100)
                    sl = max(sl, _lockpx) if side == "LONG" else min(sl, _lockpx)
                # (2026-08-26) SIN BE-lock en mom (pedido usuario tras ver la medición): el BE-temprano scratchea los runners
                # que hunden tras picar y después resumen -> baja la E del campeón. mom mantiene su stop ancho intrínseco.
                if side == "LONG" and lo <= sl:
                    exitpx = sl; reason = ("trail" if sl > entry else "sl"); break
                if side == "SHORT" and hi >= sl:
                    exitpx = sl; reason = ("trail" if sl < entry else "sl"); break
                if bars >= MAXB:
                    exitpx = c; reason = "timestop"; break
            if exitpx is None and LIQ_FLIP_EXIT and not arm.endswith("_rand"):
                # SALIDA por LIQUIDACIÓN INVERTIDA + FLUJO EN CONTRA (pedido usuario 2026-08-26): si el imán se dio vuelta
                # en contra Y el flujo reciente también, cerrar YA al precio actual (no esperar el stop). Excl. random.
                import liq_gate as _lg
                if _lg.should_close(sym, side, K):
                    exitpx = K[-1][4]; reason = "liq_flip"
            if exitpx is None:
                con.execute("UPDATE trades SET mfe=?,mae=? WHERE id=?", (round(cmfe, 3), round(cmae, 3), tid)); continue
            pnl = ((exitpx - entry) / entry * 100 if side == "LONG" else (entry - exitpx) / entry * 100) - FEE
            clean = 1 if (cmfe >= 1.0 and cmae >= -0.3) else 0
            con.execute("UPDATE trades SET status='CLOSED',ts_close=?,exit_px=?,pnl_pct=?,mfe=?,mae=?,clean=?,reason=? WHERE id=?",
                        (_iso(), exitpx, round(pnl, 3), round(cmfe, 3), round(cmae, 3), clean, reason, tid)); cl += 1
        except Exception:
            continue
    con.commit(); return cl


def step():
    con = _db()
    op = _scan(con)
    cl = _manage(con)
    nopen = con.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]
    con.close()
    return op, cl, nopen


def report():
    if not os.path.exists(DB):
        return ["\n**Brazo MOMENTUM 1h:** sin DB aun."]
    con = _db(); L = ["\n**Brazo MOMENTUM 1h (cripto+TQQQ, ign/brk/rec, backtest OOS-robusto E+0.7-0.97%):**"]
    for arm in ("mom", "mom_prot", "mom_prot2", "mom_rand"):
        row = con.execute("SELECT COUNT(*),AVG(pnl_pct),AVG(CASE WHEN pnl_pct>0 THEN 1.0 ELSE 0 END)*100,"
                          "AVG(CASE WHEN clean=1 THEN 1.0 ELSE 0 END)*100 FROM trades WHERE status='CLOSED' AND arm=?", (arm,)).fetchone()
        nop = con.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN' AND arm=?", (arm,)).fetchone()[0]
        n, e, wr, cl = row
        L.append("- %-9s n=%d E=%+.3f%% WR=%.0f%% limpio=%.0f%% abiertas=%d" % (arm, n or 0, e or 0, wr or 0, cl or 0, nop))
    con.close(); return L


if __name__ == "__main__":
    import sys
    if "once" in sys.argv:
        o, c, n = step()
        print("mom step: abrio=%d cerro=%d abiertas=%d" % (o, c, n))
        print("\n".join(report()))
    else:
        while True:
            try:
                step()
            except Exception:
                pass
            time.sleep(180)
