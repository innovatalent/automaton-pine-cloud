import json
import os
from flask import Flask, request, make_response
DASHBOARD_UI_ONLY = os.environ.get('AUTOMATON_DASHBOARD_UI_ONLY') == '1'
GEX_VETO_ACTIVO = True
app = Flask(__name__)

def _resp_json(obj, code=200):
    r = make_response(json.dumps(obj), code)
    r.headers['Content-Type'] = 'application/json'
    return r
_vp_ext_cache = {'data': None, 'ts': 0.0}
_VP_TTL = 200.0
_zsc_cache = {}

def _zsc(sym, tf, lb=75):
    """Z-Score clásico (close - SMA / stdev) del símbolo en ese TF (= el del indicador Z-Score Multi). None si no hay data.
    Para detectar el CONTRA-SENTIDO: fade LONG con z en extremo SUPERIOR (overbought) = trampa (dio LONG y cayó fuerte).
    Cache 90s (pedido usuario 2026-09-03 'la columna z-score está vacía': con cache se computa para todas las filas sin lag)."""
    import time as _tz
    _k = (sym, tf, lb)
    _h = _zsc_cache.get(_k)
    if _h and _tz.time() - _h[0] < 90:
        return _h[1]
    val = None
    try:
        import imminent_move_alert as _IMM
        import statistics as _st
        K = _IMM._klines(sym, tf, lb + 12)
        if K and len(K) >= lb + 1:
            cl = [float(k[4]) for k in K[-lb:]]
            m = sum(cl) / len(cl)
            sd = _st.pstdev(cl)
            val = round((float(K[-1][4]) - m) / sd, 2) if sd > 0 else 0.0
    except Exception:
        val = None
    _zsc_cache[_k] = (_tz.time(), val)
    return val
_yellow5m_cache = {}

def _yellow5m(sym):
    """True si el simbolo tiene el CIRCULO AMARILLO A+ en 5m AHORA (ultima vela CERRADA): z(75) CRUZA por debajo de -2.5
    + ESTRUCTURA (climax vol >=2.5x avg20  O  MFI oversold <25  O  sweep+reclaim del piso). = la senal amarilla del Pine.
    Cache 60s por simbolo (pedido usuario 2026-08-31: verlo en el panel EXTREMOS para elegir la mejor opcion)."""
    import time as _t
    now = _t.time()
    hit = _yellow5m_cache.get(sym)
    if hit and now - hit[0] < 60:
        return hit[1]
    res = False
    try:
        import imminent_move_alert as _IMM
        import statistics as _st
        K = _IMM._klines(sym, '5m', 130)
        if K and len(K) >= 100:
            Kc = K[:-1]
            i = len(Kc) - 1
            c = [float(b[4]) for b in Kc]
            o = [float(b[1]) for b in Kc]
            h = [float(b[2]) for b in Kc]
            l = [float(b[3]) for b in Kc]
            v = [float(b[5]) for b in Kc]

            def _z(idx):
                w = c[idx - 75:idx]
                m = sum(w) / 75.0
                sd = _st.pstdev(w)
                return (c[idx] - m) / sd if sd > 0 else 0.0
            ext = _z(i - 1) <= -2.5 and _z(i) > -2.5
            if ext:

                def _mfi_at(idx):
                    p = ng = 0.0
                    for j in range(idx - 13, idx + 1):
                        tp = (h[j] + l[j] + c[j]) / 3.0
                        tp0 = (h[j - 1] + l[j - 1] + c[j - 1]) / 3.0
                        mf = tp * v[j]
                        if tp > tp0:
                            p += mf
                        elif tp < tp0:
                            ng += mf
                    return 100.0 if ng <= 0 else 100.0 - 100.0 / (1.0 + p / ng)
                for k in (0, 1):
                    j = i - k
                    if j < 20:
                        continue
                    av = sum(v[j - 20:j]) / 20.0
                    vc = av > 0 and v[j] >= 2.5 * av
                    swl = min(l[j - 12:j]) if j >= 12 else min(l[:j] or [l[j]])
                    rng = max(h[j] - l[j], 1e-09)
                    dw = (min(o[j], c[j]) - l[j]) / rng
                    tr = l[j] < swl and c[j] > swl and (dw >= 0.45)
                    if vc or _mfi_at(j) < 25 or tr:
                        res = True
                        break
    except Exception:
        res = False
    _yellow5m_cache[sym] = (now, res)
    return res
_STOCKS = {'NVDAUSDT', 'TSLAUSDT', 'AAPLUSDT', 'METAUSDT', 'MSTRUSDT', 'COINUSDT', 'TQQQUSDT', 'SOXLUSDT', 'SNDKUSDT', 'PLTRUSDT', 'AMDUSDT', 'GOOGLUSDT', 'AMZNUSDT', 'MSFTUSDT', 'HOODUSDT', 'MUUSDT', 'NFLXUSDT', 'AVGOUSDT', 'SKHYNIXUSDT'}
_PRIORITY_SYMS = {'TQQQUSDT'}

def _ws_open():
    """¿Está abierta la sesión REGULAR de Wall Street (9:30-16:00 America/New_York, L-V)? (pedido usuario 2026-09-03:
    opera TQQQ en horario WS). Usa zoneinfo (maneja DST); fallback a ventana UTC aprox (EDT) si no hay tzdata."""
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        ny = _dt.datetime.now(ZoneInfo('America/New_York'))
        if ny.weekday() >= 5:
            return False
        hm = ny.hour * 60 + ny.minute
        return 9 * 60 + 30 <= hm < 16 * 60
    except Exception:
        u = _dt.datetime.utcnow()
        if u.weekday() >= 5:
            return False
        hm = u.hour * 60 + u.minute
        return 13 * 60 + 30 <= hm < 20 * 60
_htf_trend_cache = {}

def _htf_trend(sym):
    """Tendencia de PRECIO en 4h (EMA50 + posición/pendiente): 'up'/'down'/'flat'. Para filtrar confluencias SOTT que
    PELEAN la tendencia de fondo (lección UNI 2026-09-03: SOTT venta/SHORT pero precio +17% sobre EMA50 en tendencia
    alcista fuerte = trampa, 'lo caro se pone más caro', siguió +8.5%; vs SUI/HBAR/BCH oversold/LONG a favor = rindieron)."""
    import time as _t2
    hit = _htf_trend_cache.get(sym)
    if hit and _t2.time() - hit[0] < 300:
        return hit[1]
    res = None
    try:
        import json as _j, urllib.request as _u
        d = _j.load(_u.urlopen(_u.Request('https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=240&limit=200' % sym, headers={'User-Agent': 'x'}), timeout=10))
        c = [float(r[4]) for r in reversed(d.get('result', {}).get('list', []) or [])]
        if len(c) >= 60:
            k = 2.0 / 51.0
            e = c[0]
            es = []
            for x in c:
                e = x * k + e * (1 - k)
                es.append(e)
            slope = (es[-1] - es[-7]) / es[-7] * 100.0
            pxv = (c[-1] - es[-1]) / es[-1] * 100.0
            res = 'up' if slope > 0.05 and pxv > -1.0 else 'down' if slope < -0.05 and pxv < 1.0 else 'flat'
    except Exception:
        res = None
    _htf_trend_cache[sym] = (_t2.time(), res)
    return res
_bot_real_cache = {'ts': 0.0, 'pos': {}, 'summary': None}

def _bot_real_data():
    """Registro REAL del BOT (cuenta mixta): lee forward_trades venue='bybit_real' (lo que operó el bot, NO tus manuales ni
    el demo). Devuelve {pos: {symbol:{side,arm}} abiertas, summary:{n,net,wr,open}}. Cache 20s. Fail-soft."""
    import time as _t, os as _os3, sqlite3 as _sq
    if _bot_real_cache['summary'] is not None and _t.time() - _bot_real_cache['ts'] < 20:
        return _bot_real_cache
    try:
        _db = _os3.path.join(_os3.path.dirname(__file__), '..', 'forward_test', 'forward_test.db')
        c = _sq.connect(_db, timeout=5)
        c.row_factory = _sq.Row
        pos = {}
        for r in c.execute("SELECT symbol,side,arm FROM forward_trades WHERE venue='bybit_real' AND status='open'"):
            pos[r['symbol']] = {'side': r['side'], 'arm': r['arm']}
        cl = c.execute("SELECT COUNT(*), AVG(pnl_net_pct), SUM(CASE WHEN pnl_net_pct>0 THEN 1.0 ELSE 0 END)*100.0/COUNT(*) FROM forward_trades WHERE venue='bybit_real' AND status='closed' AND pnl_net_pct IS NOT NULL").fetchone()
        c.close()
        _bot_real_cache['pos'] = pos
        _bot_real_cache['summary'] = {'open': len(pos), 'n': cl[0] or 0, 'avg': round(cl[1] or 0, 3), 'wr': round(cl[2] or 0)}
        _bot_real_cache['ts'] = _t.time()
    except Exception:
        pass
    return _bot_real_cache
FADE_ADX_MAX = 30

def _auto_verdict(r):
    """MOTOR DE DECISIÓN UNIFICADO (usuario 2026-09-03): articula la SECUENCIA de los indicadores en UNA decisión operable,
    tal como funciona el flujo: ①z marca el extremo → ②SOTT gira en TFs altas → ③flip de flujo 5m → +liquidez a favor →
    filtro de tendencia 4h (no pelear la tendencia = lección UNI). action:
      · OPERAR    = secuencia completa / reversión confirmada + NO pelea la tendencia (entrar)
      · ACOMPAÑAR = tendencia a favor (SOTT alineado, comprar el pullback / vender el rebote)
      · ESPERAR   = formándose (z-extremo o SOTT girando, falta gatillo)
      · EVITAR    = pelea la tendencia 4h = trampa (tipo UNI)
    conf 0-100 = op_score (validado) + bonos por liquidez / tendencia a favor / SOTT≥3TF / gatillo, − si pelea tendencia."""
    dirr = r.get('dir_reco')
    seq = r.get('secuencia') or ''
    modo = r.get('modo_reco')
    why = []
    action = 'ESPERAR'
    _pt = {'M%d' % i: 0 for i in range(15)}
    if r.get('modo_reco') == 'FADE' and (r.get('adx') or 0) >= FADE_ADX_MAX:
        return {'action': 'EVITAR', 'side': dirr, 'conf': 10, 'fade_strong_trend': True, 'partes': _pt, 'conf_bruto': 10, 'gate': 'G1', 'why': ['🚫 FADE contra tendencia FUERTE (ADX %.0f≥%d) — el peor leak (WR19%% E−2.0); esperá que la tendencia afloje' % (r.get('adx') or 0, FADE_ADX_MAX)]}
    if r.get('htf_contra'):
        if (r.get('sott_agree') or 0) >= 3:
            action = 'ESPERAR'
            why.append('confluencia SOTT %dTF fuerte, pero pelea tendencia 4h — esperar el giro/flip' % (r.get('sott_agree') or 0))
        else:
            action = 'EVITAR'
            why.append('pelea tendencia 4h (confluencia débil = trampa)')
    elif r.get('signal') and dirr:
        action = 'OPERAR'
        why.append('reversión confirmada (flip+Hist)')
    elif seq.startswith('3'):
        action = 'OPERAR'
        why.append('secuencia ③ENTRAR')
    elif modo == 'TENDENCIA' and r.get('htf_aligned') and ((r.get('sott_agree') or 0) >= 2):
        action = 'ACOMPAÑAR'
        why.append('tendencia a favor + SOTT')
    elif seq.startswith('2'):
        action = 'ESPERAR'
        why.append('SOTT formándose ②')
    elif seq.startswith('1'):
        action = 'ESPERAR'
        why.append('z marcó extremo ①')
    if action in ('ACOMPAÑAR', 'OPERAR') and dirr in ('LONG', 'SHORT'):
        _stf = r.get('sott_tf') or {}
        _fv = int(_stf.get('5m') == 'venta') + int(_stf.get('15m') == 'venta')
        _fc = int(_stf.get('5m') == 'compra') + int(_stf.get('15m') == 'compra')
        if dirr == 'LONG' and _fv >= 2:
            action = 'ESPERAR'
            why = ['SOTT SOBRECOMPRADO 5m+15m — chase del techo, esperar el pullback']
        elif dirr == 'SHORT' and _fc >= 2:
            action = 'ESPERAR'
            why = ['SOTT SOBREVENDIDO 5m+15m — esperar el rebote']
    conf = (r.get('op_score') or 0) * 7.0
    _pt['M0'] = conf
    if r.get('liq_align') and r.get('modo_reco') == 'FADE':
        conf += 6
        why.append('fade hacia la liquidez %s' % ('abajo' if r.get('liq_bias') == 'SHORT' else 'arriba'))
        _pt['M1'] = 6
    if r.get('liq_ok'):
        conf += 8
        _pt['M2'] = 8
    if r.get('ob_bias') and dirr and (r.get('ob_bias') == dirr):
        conf += 5
        why.append('order book a favor (%s)' % ('soporte bids' if dirr == 'LONG' else 'resistencia asks'))
        _pt['M3'] = 5
    _vpu = r.get('vol_push')
    if _vpu and _vpu.get('strong') and (_vpu.get('delta_dir') == dirr):
        conf += 8
        why.append('empuje volumen FUERTE a favor (%.1f× media 7d%s)' % (_vpu.get('ratio', 0), ' sesión' if _vpu.get('session_only') else ''))
        _pt['M4'] = 8
    elif _vpu and (not _vpu.get('strong')):
        why.append('volumen flojo (%.1f× media)' % _vpu.get('ratio', 0))
    _pos = r.get('pos')
    if _pos and _pos.get('fade_side') and (dirr in ('LONG', 'SHORT')):
        if _pos['fade_side'] == dirr:
            conf += 4 + 3 * (_pos.get('strength', 0) >= 2)
            why.append('fade la multitud %s (%s)' % (_pos.get('crowd'), (_pos.get('note') or '').split('→')[0].strip()))
            _pt['M5'] = 4 + 3 * (_pos.get('strength', 0) >= 2)
        else:
            conf -= 8
            why.append('⚠ con la multitud %s (offside = combustible en contra)' % _pos.get('crowd'))
            _pt['M5'] = -8
    SESION_AL_SCORE = False
    import datetime as _dtv
    _uu = _dtv.datetime.utcnow()
    _hh = _uu.hour
    _wdv = _uu.weekday()
    if _wdv >= 5:
        _sdelta, _snota = (-12, 'fin de semana (poco volumen)')
    elif 8 <= _hh < 20:
        _sdelta, _snota = (6, 'Londres/NY abiertas')
    elif _hh < 8:
        _sdelta, _snota = (-6, 'sesión Asia (menos limpias)')
    else:
        _sdelta, _snota = (0, None)
    if _snota:
        if SESION_AL_SCORE:
            conf += _sdelta
            why.append(_snota)
            _pt['M6'] = _sdelta
        else:
            why.append('%s — informativo, NO puntúa (__SESION_OFF_2026_09_10__: el %+d no pasó la vara; medido 1.17× y no 3×)' % (_snota, _sdelta))
    if r.get('htf_aligned'):
        conf += 8
        _pt['M7'] = 8
    if (r.get('sott_agree') or 0) >= 3:
        conf += 8
        _pt['M8'] = 8
    if r.get('trigger') or r.get('fade_trig5m'):
        conf += 6
        _pt['M9'] = 6
    if r.get('htf_contra'):
        conf -= 40
        _pt['M10'] = -40
    try:
        import os as _osmk, sys as _sysmk
        _fwdmk = _osmk.path.join(_osmk.path.dirname(_osmk.path.dirname(_osmk.path.abspath(__file__))), 'forward_test')
        if _fwdmk not in _sysmk.path:
            _sysmk.path.insert(0, _fwdmk)
        import market_regime as _mkr
        _mb, _mnote = _mkr.bias_for(r.get('symbol'))
    except Exception:
        _mb, _mnote = (None, None)
    _corr_ok = True
    _corr_info = None
    try:
        import corr_btc as _cb
        _corr_info = _cb.corr(r.get('symbol'))
        if _corr_info and (not _corr_info['aplica_contexto']):
            _corr_ok = False
    except Exception:
        pass
    if _mb and dirr in ('LONG', 'SHORT') and _corr_ok:
        if _mb == dirr:
            conf += 10
            why.append('contexto a favor (%s)' % _mnote)
            _pt['M11'] = 10
        else:
            conf -= 14
            why.append('contexto EN CONTRA: %s' % _mnote)
            _pt['M11'] = -14
            if action == 'ACOMPAÑAR':
                action = 'ESPERAR'
                why.append('no perseguir contra la rotación macro — esperar alineación con BTC/dominancia')
    elif _mb and (not _corr_ok) and _corr_info:
        why.append('contexto de BTC NO aplicado: %s está desacoplada (corr 48h %.2f < %.2f) — medido: con corr<0.3 el contexto pierde poder predictivo' % (str(r.get('symbol') or '').replace('USDT', ''), _corr_info['corr'], _corr_info['umbral']))
    r['mkt_ctx'] = {'bias': _mb, 'note': _mnote}
    if _corr_info:
        r['corr_btc'] = _corr_info
    try:
        import dow_bias as _dw
        _dbs, _dstr, _dnote = _dw.bias_for(r.get('symbol'))
    except Exception:
        _dbs, _dstr, _dnote = (None, 0, None)
    r['dow_bias'] = {'side': _dbs, 'strength': _dstr, 'note': _dnote} if _dnote else None
    try:
        import dow_bias as _dw2
        _dadj, _dadjn = _dw2.adjust_for(r.get('symbol'), dirr)
    except Exception:
        _dadj, _dadjn = (0, None)
    if _dadj and dirr in ('LONG', 'SHORT'):
        conf += _dadj
        why.append('%s (%+d)' % (_dadjn, _dadj))
        r['dow_adj'] = {'side': dirr, 'delta': _dadj, 'note': _dadjn}
        _pt['M12'] = _dadj
    try:
        import rates_regime as _rr
        _rctx = _rr.context()
        r['rates'] = '%s:%+d' % (_rctx.get('regime'), int(_rctx.get('score') or 0))
        _radj, _radjn = _rr.adjust_for(r.get('symbol'), dirr)
    except Exception:
        _radj, _radjn = (0, None)
    if _radj and dirr in ('LONG', 'SHORT'):
        conf += _radj
        why.append('%s (%+d)' % (_radjn, _radj))
        r['rates_adj'] = {'side': dirr, 'delta': _radj, 'note': _radjn}
        _pt['M13'] = _radj
    try:
        import gex_live as _glz
        import gex_zonas as _gzz
        _sy = str(r.get('symbol') or '').upper()
        _cn = _glz.coin_for(_sy)
        _stz = None
        if _cn:
            _stz = _glz.state(_cn, 'scalp')
        elif _sy in _STOCKS and _sy not in ('TQQQUSDT', 'SOXLUSDT', 'SPXLUSDT'):
            _stz = _glz.equity_state(_sy[:-4] if _sy.endswith('USDT') else _sy)
        if _stz is not None:
            _zz = _glz._zonas_de(_stz, r.get('atr_pct'))
            if _zz:
                _cc = _gzz.confluencias(_zz, poc=r.get('poc'), val=r.get('val'), vah=r.get('vah'))
                _ap = _gzz.aporte_score(_zz, _cc, lado=dirr, modo=r.get('modo_reco'), spot=_stz.spot)
                r['gex_zonas'] = _zz
                r['gex_confluencia'] = _cc
                r['gex_aporte'] = _ap
                if _ap.get('total'):
                    conf += _ap['total']
                    _pt['M14'] = _ap['total']
                    why.append('gamma %+d (%s)' % (_ap['total'], '; '.join(_ap.get('detalle') or [])[:90]))
    except Exception:
        pass
    _bruto = conf
    conf = max(0, min(100, round(conf)))
    if action == 'EVITAR':
        conf = min(conf, 15)
    return {'action': action, 'side': dirr, 'conf': conf, 'mkt_bias': _mb, 'mkt_note': _mnote, 'why': why, 'partes': _pt, 'conf_bruto': round(_bruto, 2)}
_VERDICT_LATCH = {}

def _verdict_with_latch(r, now):
    """ANTI-PARPADEO + MANTENER HACIA EL TARGET (usuario 2026-09-03: "que el cartel OPERAR no se prenda/apague a cada
    segundo; la operativa se mantiene abierta hacia un target/liquidez con su probabilidad histórica"). Cuando el veredicto
    es OPERAR/ACOMPAÑAR se LATCHEA (con su target y su prob); en refrescos siguientes se MANTIENE estable aunque las
    condiciones instantáneas bajen a ESPERAR, hasta que se INVALIDE: tocó el target (cumplida) · SL ~1.5% en contra ·
    la tendencia 4h se dio vuelta · expiró (6h). Así la operativa queda abierta hacia el objetivo, no parpadea."""
    sym = r.get('symbol')
    px = r.get('px')
    tgt = r.get('target')
    v = _auto_verdict(r)
    dirr = v.get('side')
    lt = _VERDICT_LATCH.get(sym)
    if v['action'] in ('OPERAR', 'ACOMPAÑAR') and dirr in ('LONG', 'SHORT'):
        if not lt or lt.get('side') != dirr:
            _VERDICT_LATCH[sym] = {'side': dirr, 'action': v['action'], 'entry_px': px, 'target': tgt, 'ts': now, 'conf': v['conf'], 'target_reach': r.get('target_reach')}
        else:
            lt['conf'] = v['conf']
            lt['action'] = v['action']
            lt['target'] = tgt
        v['latched'] = True
        v['target'] = tgt
        v['target_reach'] = r.get('target_reach')
        v['hold_age'] = round((now - _VERDICT_LATCH[sym]['ts']) / 60)
        return v
    if lt:
        side = lt['side']
        ep = lt.get('entry_px') or px
        inval = None
        if r.get('htf_contra') and r.get('dir_reco') == side:
            inval = 'tendencia se dio vuelta'
        elif px and ep:
            adv = (ep - px) / ep * 100 if side == 'LONG' else (px - ep) / ep * 100
            if adv >= 1.5:
                inval = 'SL (−1.5%)'
            elif tgt and (side == 'LONG' and px >= tgt or (side == 'SHORT' and px <= tgt)):
                inval = '🎯 target alcanzado'
        if now - lt['ts'] > 6 * 3600:
            inval = inval or 'expiró (6h)'
        if inval:
            del _VERDICT_LATCH[sym]
            v['latch_closed'] = inval
            return v
        return {'action': lt['action'], 'side': side, 'conf': lt.get('conf', 0), 'why': ['mantener hacia el target'], 'latched': True, 'target': lt.get('target'), 'target_reach': lt.get('target_reach'), 'hold_age': round((now - lt['ts']) / 60), 'partes': None, 'conf_bruto': None, 'latch_stale': True}
    return v

def _bybit_prof(sym):
    """Perfil de volumen 24h desde BYBIT (fallback para acciones tokenizadas como HOOD que Binance no tiene). Devuelve
    {px,vwap,poc,vah,val,sig_pos,dn_band,up_band,vol_below} o None."""
    try:
        import confluence_watch as _cw
        K = _cw._kl(sym, '5', 300)
    except Exception:
        K = None
    if not K or len(K) < 60:
        return None
    import math as _m
    px = K[-1][4]
    h = [b[2] for b in K]
    l = [b[3] for b in K]
    c = [b[4] for b in K]
    v = [b[5] for b in K]
    hlc = [(h[i] + l[i] + c[i]) / 3 for i in range(len(K))]
    sv = sum(v) or 1
    vwap = sum((hlc[i] * v[i] for i in range(len(K)))) / sv
    var = sum((v[i] * (hlc[i] - vwap) ** 2 for i in range(len(K)))) / sv
    sd = _m.sqrt(var) if var > 0 else 0
    lo = min(l)
    hi = max(h)
    nb = 60
    bs = (hi - lo) / nb if hi > lo else 1
    vol = [0.0] * nb
    for i in range(len(K)):
        b0 = max(0, min(nb - 1, int((l[i] - lo) / bs)))
        b1 = max(0, min(nb - 1, int((h[i] - lo) / bs)))
        span = b1 - b0 + 1
        for b in range(b0, b1 + 1):
            vol[b] += v[i] / span
    poc_i = max(range(nb), key=lambda b: vol[b])
    poc = lo + (poc_i + 0.5) * bs
    tot = sum(vol) or 1
    lo_i = hi_i = poc_i
    acc = vol[poc_i]
    while acc < 0.7 * tot and (lo_i > 0 or hi_i < nb - 1):
        up = vol[hi_i + 1] if hi_i < nb - 1 else -1
        dn = vol[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1
            acc += max(up, 0)
        else:
            lo_i -= 1
            acc += max(dn, 0)
    vah = lo + (hi_i + 1) * bs
    val = lo + lo_i * bs
    cur_i = max(0, min(nb - 1, int((px - lo) / bs)))
    vol_below = 100.0 * sum(vol[:cur_i]) / tot
    _h1 = []
    for _k in range(max(0, len(K) - 15 * 12), len(K), 12):
        _t = K[_k:_k + 12]
        if len(_t) < 12:
            break
        _h1.append((max((b[2] for b in _t)), min((b[3] for b in _t)), _t[-1][4]))
    _atr1h = None
    if len(_h1) >= 2:
        _tr = [max(_h1[j][0] - _h1[j][1], abs(_h1[j][0] - _h1[j - 1][2]), abs(_h1[j][1] - _h1[j - 1][2])) for j in range(1, len(_h1))]
        _atr1h = sum(_tr) / len(_tr) / px * 100.0 if px else None
    return {'px': px, 'vwap': vwap, 'poc': poc, 'vah': vah, 'val': val, 'sig_pos': round((px - vwap) / sd, 2) if sd > 0 else 0.0, 'dn_band': vwap - 2.5 * sd, 'up_band': vwap + 2.5 * sd, 'vol_below': round(vol_below), 'atr1h_pct': _atr1h}

def _inject_confluence_rows(rows):
    """Incluye en el panel los activos con CONFLUENCIA SOTT FUERTE (≥3 TF) que NO entran por extremo VWAP (ej HOOD: SOTT
    venta 3TF pero sin extremo VWAP) → que se CONSIDEREN operables por la confluencia SOTT multi-TF (pedido usuario
    2026-09-03: es un indicador CLAVE). Los toma del vigilante (confluence_watch). Fail-soft."""
    try:
        from direction_engine import sott_zone as _sz2
    except Exception:
        return
    watch = _cw_cache.get('data') or []
    present = {r.get('symbol') for r in rows}
    _tfs2 = [('5m', '5'), ('15m', '15'), ('1h', '60'), ('4h', '240'), ('1d', 'D')]
    for u in watch:
        sym = u.get('sym')
        side = u.get('side')
        px = u.get('px')
        if not sym or sym in present or side not in ('LONG', 'SHORT') or (not px):
            continue
        if (u.get('sott_agree') or 0) < 3 and (u.get('z_agree') or 0) < 3:
            continue
        tfz = {}
        for lbl, code in _tfs2:
            try:
                v = _sz2.src_new(sym, code)
            except Exception:
                v = None
            tfz[lbl] = 'compra' if v is not None and v < 1020 else 'venta' if v is not None and v > 1080 else 'neutro' if v is not None else None
        allz = [tfz.get(t) for t in ('5m', '15m', '1h', '4h', '1d')]
        htfz = [tfz.get(t) for t in ('1h', '4h', '1d')]
        hc = htfz.count('compra')
        hv = htfz.count('venta')
        r = {'symbol': sym, 'px': px, 'sig_pos': 0.0, 'sig_abs': 0.0, 'tier': 'conf', 'prime': False, 'lado_ext': 'SUP' if side == 'SHORT' else 'INF', 'beyond_band': False, 'is_ext': False, 'poc': px, 'poc_dist': 0.0, 'vwap': px, 'vah': px, 'val': px, 'lvl_delta': 0, 'net_delta': 0, 'delta_surge': u.get('surge', 0), 'cluster_reach': 0, 'cluster_n': 1, 'reach_poc': 0, 'reach_vwap': 0, 'delta_side': '—', 'signal': None, 'sig_mode': None, 'trend': side, 'took_liq': False, 'cont_score': 0, 'cont_ok': False, 'cont_weak': False, 'best_cont': False, 'ok_dir': None, 'hp_mode': None, 'adx': 0, 'regimen': 'mixto', 'target': px, 'target_kind': 'VWAP', 'target_full': px, 'dist_target': 0.0, 'target_reach': 0, 'flow2': 0, 'cvd': 0, 'flow2_sup': 0, 'cvd_sup': 0, 'flow_want': 0, 'sync': None, 'conv': 0, 'conf_n': 0, 'conf_srcs': [], 'conf_liq': False, 'liq_ok': bool(u.get('liq_ok')), 'liq_dist': u.get('liq_dist'), 'liq_contra': False, 'yellow5m': False, 'trigger': False, 'hora_edge': 'media', 'sf_mid_dist': None, 'sweep_fade': None, 'z_contra': False, 'z_bias': None, 'hist': 0, 'sott_tf': tfz, 'sott_agree': max(allz.count('compra'), allz.count('venta')), 'sott_dir': 'compra' if tfz.get('1h') == 'compra' or tfz.get('1d') == 'compra' else 'venta' if tfz.get('1h') == 'venta' or tfz.get('1d') == 'venta' else None, 'dir_reforzada': False, 'htf_side': 'LONG' if hc > hv else 'SHORT' if hv > hc else None, 'htf_agree': max(hc, hv), 'htf_trend': u.get('trend4h'), 'htf_contra': bool(u.get('contra')), 'htf_aligned': not u.get('contra'), 'z_tf': {}, 'z_agree': u.get('z_agree', 0), 'z_against': u.get('z_agree', 0), 'z_dir': None, 'conf_track': None, 'sess_trace': None, 'setup': None, 'secuencia': '2-SOTT-max' if side == 'SHORT' else '2-SOTT-min', 'seq_side': side, 'trend_conf': u.get('sott_agree', 0) if u.get('sott_agree', 0) >= 3 else 0, 'fade_conf': 0, 'modo_reco': 'FADE', 'dir_reco': side, 'modo_src': 'SOTT%dTF' % max(allz.count('compra'), allz.count('venta')), 'watch_row': True}
        prof = _bybit_prof(sym)
        if prof:
            _pxp = prof['px']
            r['px'] = _pxp
            r['vwap'] = prof['vwap']
            r['poc'] = prof['poc']
            r['vah'] = prof['vah']
            r['val'] = prof['val']
            r['sig_pos'] = prof['sig_pos']
            r['sig_abs'] = abs(prof['sig_pos'])
            r['dn_band'] = prof['dn_band']
            r['up_band'] = prof['up_band']
            r['prime'] = abs(prof['sig_pos']) >= 2.0
            r['tier'] = 'EXTREMO' if abs(prof['sig_pos']) >= 2.5 else 'FUERTE' if abs(prof['sig_pos']) >= 2.0 else 'conf'
            r['vol_below'] = prof['vol_below']
            _center = prof['vwap'] if abs(prof['vwap'] - _pxp) < abs(prof['poc'] - _pxp) else prof['poc']
            _mid = _pxp + (_center - _pxp) * 0.5
            _minmv = _pxp * 0.01
            _dir = 1 if _center > _pxp else -1
            tgt = _mid if abs(_mid - _pxp) >= _minmv else _pxp + _minmv * _dir
            r['target'] = round(tgt, 6)
            r['target_full'] = round(_center, 6)
            r['target_kind'] = 'VWAP' if _center == prof['vwap'] else 'POC'
            r['dist_target'] = round((tgt - _pxp) / _pxp * 100, 2)
            _atr1h = prof.get('atr1h_pct')
            if _atr1h and _atr1h > 0:
                _dc = abs(_center - _pxp) / _pxp * 100.0
                _d2 = min(_atr1h, _dc)
                if _d2 > 1e-09:
                    r['obj2'] = round(_pxp * (1 + _dir * _d2 / 100.0), 6)
                    r['obj2_dist'] = round(_dir * _d2, 3)
                    r['obj2_kind'] = r['target_kind'] if _d2 >= _dc - 1e-09 else 'ATR'
                    r['atr1h_pct'] = round(_atr1h, 3)
            if abs(prof['sig_pos']) >= 2.5:
                r['is_ext'] = True
                r['beyond_band'] = _pxp >= prof['up_band'] or _pxp <= prof['dn_band']
                r['lado_ext'] = 'SUP' if prof['sig_pos'] > 0 else 'INF'
        r['op_score'] = round(2.0 + r['sott_agree'] * 0.8 + (u.get('z_agree') or 0) * 0.4 - (3.0 if r['htf_contra'] else 0), 2)
        r['op_grade'] = 'OK' if r['op_score'] >= 3 else ''
        rows.append(r)
_DEC_MIN_N = 20
_DEC_MIN_P2 = 20.0

def _flow_align(r, side):
    """Lecturas de FLUJO a favor / en contra del lado: flow2 + CVD 5m, flow2 + CVD 1h, empuje de volumen con delta a favor."""
    want = 1 if side == 'LONG' else -1
    vals = [r.get(k) for k in ('flow2', 'cvd', 'flow2_sup', 'cvd_sup')]
    a = sum((1 for x in vals if x == want))
    c = sum((1 for x in vals if x == -want))
    vp = r.get('vol_push')
    if isinstance(vp, dict) and vp.get('strong') and (vp.get('delta_dir') == side):
        a += 1
    return (a, c)
_ENG_RULES = {'ts': 0.0, 'mtime': 0.0, 'allowed': set(), 'decided': set()}

def _engine_rules_cells():
    """Celdas del motor VP (vp_engine_rules.json): allowed (operan) y decided (n>=30 con muestra). Cache por mtime.
    Fuente ÚNICA de "evidencia OK" para la decisión del panel → el panel dice LONG/SHORT con ✅ SÓLO donde el motor opera."""
    import os as _o, json as _j, time as _t
    p = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), 'forward_test', 'vp_engine_rules.json')
    try:
        mt = _o.path.getmtime(p)
        if mt != _ENG_RULES['mtime'] or _t.time() - _ENG_RULES['ts'] > 600:
            d = _j.load(open(p, encoding='utf-8'))
            _ENG_RULES.update(ts=_t.time(), mtime=mt, allowed=set(d.get('allowed') or []), decided=set(d.get('decided_cells') or []))
    except Exception:
        pass
    return (_ENG_RULES['allowed'], _ENG_RULES['decided'])
_REGIME_C = {'ts': 0.0, 'data': None}
_ER24_C = {}

def _regime_ctx():
    """__REGIME_PANEL_2026_09_05__: régimen validado (VIX>20 reversión ETF/acciones; DVOL BTC pct>66 reversión cripto; FOMC; OPEX)
    leído de forward_test/vp_engine_state.json (cache 60 s). Nunca revienta: dict con None si falta."""
    import os as _o, time as _tm
    if _tm.time() - _REGIME_C['ts'] < 60 and _REGIME_C['data'] is not None:
        return _REGIME_C['data']
    d = {'vix': None, 'vix_on': None, 'dvol_pct': None, 'dvol_on': None, 'fomc_today': None, 'opex_next': None, 'ibs_today': {}, 'fresh': False}
    try:
        stp = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), 'forward_test', 'vp_engine_state.json')
        st = json.load(open(stp, encoding='utf-8'))
        d['fresh'] = _tm.time() - float(st.get('ts') or 0) < 900
        rb = st.get('rebote') or {}
        cd = st.get('cdaily') or {}
        ib = st.get('ibs') or {}
        op = st.get('opex') or {}
        if rb.get('vix') is not None:
            d['vix'] = round(float(rb['vix']), 2)
            d['vix_on'] = d['vix'] > float(ib.get('vix_min') or rb.get('vix_min') or 20.0)
        dp = (cd.get('dvol_pct') or {}).get('BTC')
        if dp is not None:
            d['dvol_pct'] = round(float(dp), 1)
            d['dvol_on'] = d['dvol_pct'] > 66.0
        d['fomc_today'] = bool(cd.get('fomc_today'))
        d['opex_next'] = op.get('next')
        d['ibs_today'] = dict(ib.get('today') or {})
        d['ibs_real_syms'] = list(ib.get('real_syms') or [])
        _spy = (ib.get('today') or {}).get('SPYUSDT')
        _qqq = (ib.get('today') or {}).get('QQQUSDT')
        d['breadth'] = {'spy': _spy, 'qqq': _qqq, 'on': min([x for x in (_spy, _qqq) if x is not None]) < 0.2 if _spy is not None or _qqq is not None else None}
    except Exception:
        pass
    _REGIME_C['ts'] = _tm.time()
    _REGIME_C['data'] = d
    return d

def _er24_panel(sym):
    """__REGIME_PANEL_2026_09_05__: {er, dir(+1/-1), ret24} con las últimas 24 velas 1 h CERRADAS (misma fórmula que el motor). Cache 10 min."""
    import time as _tm
    h = _ER24_C.get(sym)
    if h and _tm.time() - h[0] < 600:
        return h[1]
    out = None
    try:
        import os as _o, sys as _sy
        _ft = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), 'forward_test')
        if _ft not in _sy.path:
            _sy.path.insert(0, _ft)
        import imminent_move_alert as _IMM
        K = _IMM._klines(sym, '1h', 200)
        if K and len(K) >= 30:
            c = [float(k[4]) for k in K[:-1][-25:]]
            er = abs(c[-1] - c[0]) / max(sum((abs(c[i] - c[i - 1]) for i in range(1, len(c)))), 1e-12)
            out = {'er': round(er, 3), 'dir': 1 if c[-1] >= c[0] else -1, 'ret24': round((c[-1] / c[0] - 1) * 100, 2)}
            try:
                v = [float(k[5]) if len(k) > 5 else 0.0 for k in K[:-1]]
                base = sum(v[-169:-1]) / max(1, len(v[-169:-1]))
                out['vol7d'] = round(v[-1] / base, 2) if base > 0 else None
            except Exception:
                out['vol7d'] = None
    except Exception:
        out = None
    _ER24_C[sym] = (_tm.time(), out)
    return out
_RV3X_MAP = {'TQQQUSDT': 'QQQUSDT', 'SPXLUSDT': 'SPYUSDT', 'SOXLUSDT': 'SMHUSDT'}
_RV3X_C = {}

def _rv3x_panel(sym, hours=8):
    """__RV3X_PANEL_2026_09_05__: desvío 8 h del perp 3× vs 3× el perp 1× (Bybit, velas cerradas). + = caro (corrige −), − = barato (corrige +). Cache 5 min."""
    ref = _RV3X_MAP.get(sym)
    if not ref:
        return None
    import time as _tm
    h = _RV3X_C.get(sym)
    if h and _tm.time() - h[0] < 300:
        return h[1]
    v = None
    try:
        import urllib.request as _u

        def _k(s_):
            u = 'https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=60&limit=%d' % (s_, hours + 3)
            r = json.loads(_u.urlopen(_u.Request(u, headers={'User-Agent': 'dash-rv'}), timeout=15).read())['result']['list']
            r = sorted(r, key=lambda x: int(x[0]))[:-1]
            return {int(x[0]): float(x[4]) for x in r}
        A = _k(sym)
        B = _k(ref)
        ts = sorted(set(A) & set(B))
        if len(ts) >= hours + 1:
            v = round((A[ts[-1]] / A[ts[-hours - 1]] - 1) * 100 - 3 * (B[ts[-1]] / B[ts[-hours - 1]] - 1) * 100, 2)
    except Exception:
        v = None
    _RV3X_C[sym] = (_tm.time(), v)
    return v
_MTF_C = {}

def _mtf_score(sym):
    """__MTF_PANEL_2026_09_06__: alineación multi-temporalidad (5m/15m/30m desde velas 5 m; 1h/4h/1d desde velas 1 h; velas CERRADAS): UP = cierre > EMA20
    del marco y EMA20 subiendo (1d: EMA10). Devuelve {"n": 0-6, "up": [marcos]} o None. Cache 5 min. SÓLO LECTURA: como gatillo mide −0.26/op."""
    import time as _tm, collections as _co
    h = _MTF_C.get(sym)
    if h and _tm.time() - h[0] < 60:
        return h[1]
    out = None
    try:
        import os as _o, sys as _sy
        _ft = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), 'forward_test')
        if _ft not in _sy.path:
            _sy.path.insert(0, _ft)
        import imminent_move_alert as _IMM

        def _ema(a, n):
            e = [a[0]]
            k = 2 / (n + 1)
            for x in a[1:]:
                e.append(e[-1] + k * (x - e[-1]))
            return e

        def _state(K, minutes, n_ema=20):
            step = minutes * 60000
            b = _co.OrderedDict()
            for k in K:
                b[int(k[0]) // step] = float(k[4])
            vals = list(b.values())
            if len(vals) < n_ema + 6:
                return None
            vals = vals[:-1]
            e = _ema(vals, n_ema)
            return bool(vals[-1] > e[-1] and e[-1] >= e[-4])
        K5 = _IMM._klines(sym, '5m', 600) or []
        K1 = _IMM._klines(sym, '1h', 600) or []
        st_ = {'5m': _state(K5, 5), '15m': _state(K5, 15), '30m': _state(K5, 30), '1h': _state(K1, 60), '4h': _state(K1, 240), '1d': _state(K1, 1440, 10)}
        ok = [k for k, v in st_.items() if v]
        out = {'n': len(ok), 'up': ok, 'known': sum((1 for v in st_.values() if v is not None))}
    except Exception:
        out = None
    _MTF_C[sym] = (_tm.time(), out)
    return out

def _row_regime_tag(r, ctx):
    """__REGIME_PANEL_2026_09_05__: etiqueta por fila {text,color,title,kind,fire} con el discriminador validado para su clase.
    ETF/acción: IBS de hoy (dispara <0.2 con VIX>20 y sin FOMC). Cripto: ER24 + dirección (≥0.20 continuación eficiente)."""
    sym = r.get('symbol') or ''
    ibs = (ctx.get('ibs_today') or {}).get(sym)
    if ibs is not None:
        vix = ctx.get('vix')
        von = bool(ctx.get('vix_on'))
        fomc = bool(ctx.get('fomc_today'))
        real = sym in (ctx.get('ibs_real_syms') or [])
        fire = ibs < 0.2 and von and (not fomc)
        if fire:
            txt = '📐 IBS %.2f → LONG al cierre NY (VIX %s ✓%s)' % (ibs, '%.0f' % vix if vix is not None else '?', ' · REAL' if real else ' · paper')
            col = '#3fb950'
        elif ibs < 0.2:
            txt = '📐 IBS %.2f dispara pero %s → motor NO opera' % (ibs, 'HOY FOMC' if fomc else 'VIX %.0f ≤ 20' % vix if vix is not None else 'VIX ?')
            col = '#ffd000'
        elif ibs > 0.9:
            txt = '📐 IBS %.2f (cierre en el techo: salida IBS)' % ibs
            col = '#8a94a6'
        else:
            txt = '📐 IBS %.2f (sin señal diaria)' % ibs
            col = '#6e7681'
        title = 'IBS = (cierre − mínimo)/(máximo − mínimo) de la sesión NY de HOY. Fuente IBS del motor: IBS < 0.2 al cierre → LONG; sale el primer día con IBS > 0.9, máx 5 sesiones (7 si volumen de capitulación). Sólo con VIX > 20 y sin FOMC. Medido 2010-26: TQQQ +1.24/op, SOXL +1.61, SPXL +1.98, acciones +1.2…+1.7 (14-18 de 18 años positivos).'
        _rv = _rv3x_panel(sym)
        if _rv is not None:
            _rvt = ' · perp %s %+.2f%% vs 3×%s (8h)' % ('CARO 🔴' if _rv >= 0.5 else 'BARATO 🟢' if _rv <= -0.5 else '≈', _rv, _RV3X_MAP[sym][:-4])
            txt += _rvt
            title += ' VALOR RELATIVO (medido Bybit jun-sep 2026): cuando el perp 3× se desvía ≥ 0.5 %% de 3× el perp 1× en 8 h, lo corrige en las 4-8 h siguientes (+0.2 %% relativo, WR 64-76 %%, t 3.5-4.8). Muy chico para operar el par con dos comisiones; sirve como TIMING: comprar el 3× cuando está BARATO, evitar cuando está CARO. Hoy: %+.2f%%.' % _rv
        return {'text': txt, 'color': col, 'title': title, 'kind': 'IBS', 'fire': fire, 'ibs': ibs, 'rv3x': _rv}
    er = _er24_panel(sym)
    if er:
        eff = er['er'] >= 0.2
        arrow = '▲ LONG' if er['dir'] > 0 else '▼ SHORT'
        _cr = sym not in _STOCKS and (not sym.startswith(('CL', 'XAU', 'XAG', 'NG', 'GC', 'SI')))
        _v7 = er.get('vol7d')
        _vtxt = ' · vol 1h %.1f× media 7d%s' % (_v7, ' 🔊' if _v7 >= 2.0 else '') if _v7 else ''
        _mtf = _mtf_score(sym)
        if _mtf and _mtf.get('known'):
            _vtxt += ' · MTF %d/%d%s' % (_mtf['n'], _mtf['known'], ' ▲' + ','.join(_mtf['up']) if _mtf['n'] else '')
        if eff and _cr:
            txt = '⚡ ER24 %.2f %s (%+.1f%% 24h) → continuación eficiente: MOM opera a favor%s' % (er['er'], arrow, er['ret24'], _vtxt)
            col = '#3fb950' if er['dir'] > 0 else '#f85149'
        elif eff:
            txt = '⚡ ER24 %.2f %s (%+.1f%% 24h) tendencia eficiente · no fadear (MOM sólo cripto)' % (er['er'], arrow, er['ret24'])
            col = '#8a94a6'
        else:
            txt = '⚡ ER24 %.2f → rango%s%s' % (er['er'], ' (MOM no opera)' if _cr else '', _vtxt)
            col = '#6e7681'
        title = 'ER24 = eficiencia de Kaufman: |cierre − cierre hace 24 h| / suma de |cambios| en 24 velas 1 h CERRADAS. Alto = tendencia limpia. Medido (loop 001, MOM cripto): ER < 0.20 +0.19/op · ER ≥ 0.20 y tendencia a favor +1.00/op (n129). El motor exige ER ≥ 0.20 y entrar A FAVOR de la dirección 24 h. Con DVOL BTC ≤ p66 la continuación rinde más; con DVOL > p66 manda la reversión diaria (CDAILY). VOLUMEN (loop 023): vela 1 h con volumen ≥ 2× la media de 7 días → MOM +1.51/op (WR 82 %, ≥+1 % 69 %) vs +0.53 sin ese volumen; el motor lo usa como PRIORIDAD (+0.5). Sesión 21-24 UTC negativa (−0.3). Londres 08-13 UTC la mejor (+2.05 con ER). MTF = marcos alineados al alza (5m/15m/30m/1h/4h/1d, cierre > EMA20 y EMA subiendo, velas cerradas). SÓLO LECTURA: medido 18 monedas × 120 d, entrar cuando se alinean ≥ 5 marcos da −0.26/op (6/6: −1.18); P(vela ≥ +4 % en 3 h | 6/6) = 7 % vs 1.7 % base. UNI 05-09: 7/7 a las 10 UTC (luego −2 %), 7/7 de nuevo a las 15 UTC.'
        return {'text': txt, 'color': col, 'title': title, 'kind': 'ER', 'fire': eff, 'er': er['er'], 'dir': er['dir']}
    return None
_ENGPOS_C = {'ts': 0.0, 'data': {}}

def _engine_positions():
    """__DECISION_SYNC_2026_09_06__: posiciones ABIERTAS del motor VP (journal forward_test/vp_engine.db): sym -> (venue, side, cell, HH:MM). Real pisa a paper. Cache 15 s."""
    import os as _o, time as _tm, sqlite3 as _sq
    if _tm.time() - _ENGPOS_C['ts'] < 15:
        return _ENGPOS_C['data']
    out = {}
    try:
        dbp = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), 'forward_test', 'vp_engine.db')
        c = _sq.connect('file:%s?mode=ro' % dbp.replace('\\', '/'), uri=True, timeout=3)
        for sym, venue, side, cell, ts in c.execute("SELECT symbol, venue, side, cell, ts_open FROM trades WHERE status='OPEN' ORDER BY CASE venue WHEN 'real' THEN 0 ELSE 1 END, id DESC"):
            if sym not in out:
                out[sym] = (venue, side, cell, (ts or '')[11:16])
        c.close()
    except Exception:
        pass
    _ENGPOS_C['ts'] = _tm.time()
    _ENGPOS_C['data'] = out
    return out

def _direct_decision(r):
    """{dec: LONG|SHORT|ESPERAR, side, score 0-100, why, tag, flow:[a,c], p2, n, struct, conf}. Ver docstring del parche."""
    v = r.get('verdict') or {}
    side = v.get('side') or r.get('dir_reco')
    act = v.get('action')
    conf = float(v.get('conf') or 0)
    e = r.get('edge') or {}
    contra_ext = bool(r.get('dir_reco') in ('LONG', 'SHORT') and side in ('LONG', 'SHORT') and (side != r.get('dir_reco')))
    if contra_ext:
        e = {}
    p2 = e.get('p2')
    n = int(e.get('n') or 0)
    net = e.get('net')
    ev = min(100.0, 2.0 * float(p2)) if p2 is not None else 50.0
    score = int(round(0.5 * conf + 0.5 * ev))
    a, c = _flow_align(r, side) if side in ('LONG', 'SHORT') else (0, 0)
    flow_ok = a >= 1 and a > c
    why0 = str((v.get('why') or [''])[0] or '').replace('"', '').strip()
    dec, why, tag = ('ESPERAR', None, None)
    if side not in ('LONG', 'SHORT'):
        why = 'sin dirección'
    elif act == 'EVITAR':
        why = 'trampa: ' + why0[:70]
    elif act not in ('OPERAR', 'ACOMPAÑAR'):
        why = 'formándose' + (': ' + why0[:70] if why0 else '')
    elif not flow_ok:
        why = 'flujo en contra (%d a favor / %d en contra)' % (a, c)
    elif p2 is not None and n >= _DEC_MIN_N and (net is not None) and (net <= 0):
        why = 'evidencia negativa (%s: net %+.2f%%, n=%d)' % (e.get('key'), float(net), n)
    elif p2 is not None and n >= _DEC_MIN_N and (float(p2) < _DEC_MIN_P2):
        why = 'evidencia floja (%s: +2%% limpio %.0f%%, n=%d)' % (e.get('key'), float(p2), n)
    elif e.get('key') and (lambda _al, _dc: e.get('key') in _dc and e.get('key') not in _al)(*_engine_rules_cells()):
        why = 'evidencia insuficiente para el motor (%s: +2%% limpio %.0f%%, n=%d; celdas que operan: %s)' % (e.get('key'), float(p2 or 0), n, ', '.join(sorted(_engine_rules_cells()[0])) or 'ninguna')
    else:
        dec = side
        if contra_ext:
            tag = '🧪 sin muestra · contra el extremo (%s %s): el motor NO lo opera' % (r.get('modo_reco') or '?', r.get('dir_reco'))
        else:
            tag = '✅ evidencia %.0f%% (n=%d)' % (float(p2), n) if p2 is not None and n >= _DEC_MIN_N else '🧪 sin muestra'
    return {'dec': dec, 'side': side, 'score': score, 'why': why, 'tag': tag, 'flow': [a, c], 'p2': p2, 'n': n, 'struct': act, 'conf': int(conf), 'contra_ext': contra_ext}

def _vp_extremes_data():
    import time as _t
    if _vp_ext_cache['data'] is not None and _t.time() - _vp_ext_cache['ts'] < _VP_TTL:
        return _vp_ext_cache['data']
    try:
        import sys as _sys, os as _os2
        _ft = _os2.path.join(_os2.path.dirname(__file__), '..', 'forward_test')
        if _ft not in _sys.path:
            _sys.path.insert(0, _ft)
        import vwap_profile as _vp
        from direction_engine import sott_zone as _sz
        rows = _vp.vp_extremes(_sz.roster())
        try:
            import liq_magnets as _lm
        except Exception:
            _lm = None
        _tfs = [('5m', '5'), ('15m', '15'), ('1h', '60'), ('4h', '240'), ('1d', 'D')]
        try:
            from concurrent.futures import ThreadPoolExecutor as _TPE
            _jobs = []
            for _rw in rows:
                _sy = _rw.get('symbol')
                if not _sy:
                    continue
                for _c in ('5', '15', '60', '240', 'D'):
                    _jobs.append(('s', _sy, _c))
                for _tt in ('5m', '15m', '1h', '1d'):
                    _jobs.append(('z', _sy, _tt))
                if _sy not in _STOCKS:
                    _jobs.append(('p', _sy, None))

            def _prewarm(j):
                try:
                    if j[0] == 's':
                        _sz.src_new(j[1], j[2])
                        _sz.src_series(j[1], j[2])
                    elif j[0] == 'z':
                        _zsc(j[1], j[2])
                    elif j[0] == 'p':
                        import positioning as _posw
                        _posw.positioning(j[1])
                except Exception:
                    pass
            with _TPE(max_workers=12) as _pex:
                list(_pex.map(_prewarm, _jobs))
        except Exception:
            pass
        for _r in rows:
            _r['sott_side'] = None
            _r['sott_n'] = 0
            _r['sott_tfs'] = ''
            _r['liq_ok'] = False
            _r['liq_dist'] = None
            _r['conf_n'] = 0
            _r['conf_srcs'] = []
            _r['conf_liq'] = False
            _r['z5'] = None
            _r['z15'] = None
            _r['z_contra'] = False
            _r['z_bias'] = None
            if not _r.get('is_ext'):
                _r['op_score'] = round((_r.get('cont_score') or 0) * 1.2, 2)
                _r['op_grade'] = 'OK' if (_r.get('cont_score') or 0) >= 4 else ''
            _s = _r['symbol']
            _ex = []
            _tfz = {}
            for _lbl, _code in _tfs:
                try:
                    _src = _sz.src_new(_s, _code)
                except Exception:
                    _src = None
                _sd = ('LONG' if _src < 1020 else 'SHORT' if _src > 1080 else None) if _src is not None else None
                _tfz[_lbl] = 'compra' if _src is not None and _src < 1020 else 'venta' if _src is not None and _src > 1080 else 'neutro' if _src is not None else None
                if _sd:
                    _ex.append((_lbl, _sd))
            _r['sott_tf'] = _tfz
            try:
                _r['sott_proj'] = _sz.sott_project(_s)
            except Exception:
                _r['sott_proj'] = None
            _dz = [_tfz.get('1h'), _tfz.get('1d')]
            _allz = [_tfz.get(t) for t in ('5m', '15m', '1h', '1d')]
            _all5 = [_tfz.get(t) for t in ('5m', '15m', '1h', '4h', '1d')]
            _nvend = _all5.count('venta')
            _ncomp = _all5.count('compra')
            _r['sott_dir'] = 'compra' if _ncomp > _nvend else 'venta' if _nvend > _ncomp else None
            _r['sott_side'] = 'LONG' if _ncomp > _nvend else 'SHORT' if _nvend > _ncomp else None
            _r['sott_dom'] = max(_ncomp, _nvend)
            _r['sott_agree'] = max(_allz.count('compra'), _allz.count('venta'))
            _htfz = [_tfz.get('1h'), _tfz.get('4h'), _tfz.get('1d')]
            _hc = _htfz.count('compra')
            _hv = _htfz.count('venta')
            _r['htf_side'] = 'LONG' if _hc > _hv else 'SHORT' if _hv > _hc else None
            _r['htf_agree'] = max(_hc, _hv)
            _r['htf_trend'] = _htf_trend(_s)
            _nL = sum((1 for _, sd in _ex if sd == 'LONG'))
            _nS = sum((1 for _, sd in _ex if sd == 'SHORT'))
            _side = _r.get('sott_side')
            _r['sott_n'] = max(_nL, _nS) if _side else 0
            _r['sott_tfs'] = '·'.join((l for l, sd in _ex if sd == _side)) if _side else ''
            _z5 = _zsc(_s, '5m')
            _z15 = _zsc(_s, '15m')
            _z1h = _zsc(_s, '1h')
            _z1d = _zsc(_s, '1d')
            _r['z5'] = _z5
            _r['z15'] = _z15
            _r['z1h'] = _z1h
            _r['z1d'] = _z1d

            def _zz(_zv):
                return None if _zv is None else 'compra' if _zv <= -2.5 else 'venta' if _zv >= 2.5 else 'neutro'
            _r['z_tf'] = {'5m': _zz(_z5), '15m': _zz(_z15), '1h': _zz(_z1h), '1d': _zz(_z1d)}
            _zdz = [_zz(_z1h), _zz(_z1d)]
            _r['z_dir'] = 'compra' if _zdz.count('compra') > _zdz.count('venta') else 'venta' if _zdz.count('venta') > _zdz.count('compra') else None
            _zall = [_zz(_z5), _zz(_z15), _zz(_z1h), _zz(_z1d)]
            _r['z_agree'] = max(_zall.count('compra'), _zall.count('venta'))
            _r['yellow5m'] = _yellow5m(_s) if _z5 is not None and _z5 < -1.5 else False
            _sg = _r.get('signal')
            _zhi = _z5 is not None and _z5 >= 2.5 or (_z15 is not None and _z15 >= 2.5)
            _zlo = _z5 is not None and _z5 <= -2.5 or (_z15 is not None and _z15 <= -2.5)
            _r['z_bias'] = 'SHORT' if _zhi else 'LONG' if _zlo else None
            _r['z_contra'] = bool(_sg == 'LONG' and _zhi or (_sg == 'SHORT' and _zlo))
            _stf = _r.get('sott_tf') or {}
            _ztf = _r.get('z_tf') or {}
            _zlow = bool('compra' in (_ztf.get('5m'), _ztf.get('15m'), _ztf.get('1h'), _ztf.get('1d')) or _r.get('yellow5m'))
            _zhigh = bool('venta' in (_ztf.get('5m'), _ztf.get('15m'), _ztf.get('1h'), _ztf.get('1d')))
            _s1h = _stf.get('1h')
            _s1d = _stf.get('1d')
            _sfast = (_stf.get('5m'), _stf.get('15m'))
            _dir_buy = _s1h == 'compra' or _s1d == 'compra'
            _dir_sell = _s1h == 'venta' or _s1d == 'venta'
            _r['dir_reforzada'] = _s1h == 'compra' and _s1d == 'compra' or (_s1h == 'venta' and _s1d == 'venta')
            _seq = None
            _seqside = None
            if _zlow or _dir_buy or 'compra' in _sfast:
                _seqside = 'LONG'
                if _r.get('ok_dir') == 'compra' and _r.get('hist_rising'):
                    _seq = '3-ENTRADA'
                elif 'compra' in _sfast:
                    _seq = '2-SOTT-min'
                elif _zlow:
                    _seq = '1-z-extremo'
            elif _zhigh or _dir_sell or 'venta' in _sfast:
                _seqside = 'SHORT'
                if _r.get('ok_dir') == 'venta' and _r.get('hist_rising') is False:
                    _seq = '3-ENTRADA'
                elif 'venta' in _sfast:
                    _seq = '2-SOTT-max'
                elif _zhigh:
                    _seq = '1-z-extremo'
            _r['secuencia'] = _seq
            _r['seq_side'] = _seqside
            _over = 'venta' if _r.get('lado_ext') == 'SUP' else 'compra'
            _zt2 = _r.get('z_tf') or {}
            _z_against = sum((1 for _tf in ('5m', '15m', '1h', '1d') if _zt2.get(_tf) == _over))
            _sott_ag = _r.get('sott_agree') or 0
            _sott_side = 'LONG' if _r.get('sott_dir') == 'compra' else 'SHORT' if _r.get('sott_dir') == 'venta' else None
            _r['z_against'] = _z_against
            _r['trend_conf'] = _sott_ag if _sott_side and _sott_ag >= 3 else 0
            _r['fade_conf'] = _z_against if _r.get('is_ext') and _z_against >= 2 else 0
            if _r['trend_conf'] and _sott_side:
                _r['modo_reco'] = 'TENDENCIA'
                _r['dir_reco'] = _sott_side
                _r['modo_src'] = 'SOTT%dTF' % _sott_ag
            elif _r['fade_conf']:
                _r['modo_reco'] = 'FADE'
                _r['dir_reco'] = 'SHORT' if _r.get('lado_ext') == 'SUP' else 'LONG'
                _r['modo_src'] = 'z%dTF' % _z_against
            else:
                _r['modo_src'] = 'ADX'
            if (_r.get('sott_dom') or 0) >= 2 and _r.get('sott_side') and _r.get('dir_reco') and (_r.get('dir_reco') != _r.get('sott_side')):
                _r['dir_reco'] = _r['sott_side']
                _r['dir_src'] = 'SOTT'
                if _r.get('modo_src') in (None, 'ADX'):
                    _r['modo_src'] = 'SOTT-dir'
            if _lm is not None:
                try:
                    _mg = _lm.magnets(_s) or {}
                    _ld = _mg.get('magnet_dist')
                    _mdir = _mg.get('dir')
                    _dirside = _side or _r.get('trend')
                    if _sg and _mdir and (_mdir != _sg):
                        _r['liq_contra'] = True
                        _r['signal_blocked'] = _sg
                        _r['signal'] = None
                        _r['liq_ok'] = False
                        _r['liq_dist'] = round(_ld, 1) if _ld is not None else None
                    else:
                        _r['liq_ok'] = _mdir == _dirside and (_ld is not None and abs(_ld) >= 1.0)
                        _r['liq_dist'] = round(_ld, 1) if _ld is not None else None
                        _r['liq_px'] = _mg.get('magnet_px')
                        _r['liq_w'] = _mg.get('magnet_wrel')
                        _tp = _r.get('target')
                        if _tp:
                            _srcs = []
                            _cands = [('POC', _r.get('poc')), ('VWAP', _r.get('vwap')), ('VAH', _r.get('vah')), ('VAL', _r.get('val'))]
                            for _pl in (_mg.get('up') or []) + (_mg.get('down') or []):
                                _cands.append(('liq', _pl.get('px')))
                            for _nm, _lv in _cands:
                                if _lv and abs(_lv - _tp) / _tp * 100 <= 0.4 and (_nm not in _srcs):
                                    _srcs.append(_nm)
                            _r['conf_n'] = len(_srcs)
                            _r['conf_srcs'] = _srcs
                            _r['conf_liq'] = 'liq' in _srcs
                except Exception:
                    pass
        import datetime as _dt
        _un = _dt.datetime.utcnow()
        _hr = _un.hour
        _dw = _un.weekday()
        _goodHr = 6 <= _hr <= 10 or 13 <= _hr <= 15 or 20 <= _hr <= 22
        _badHr = _hr <= 5 or _hr in (11, 12, 16, 17, 18, 23)
        _wknd = _dw >= 5
        _hora_edge = 'ALTA' if _goodHr and (not _wknd) else 'BAJA' if _badHr or _wknd else 'media'
        for _r in rows:
            _r['hora_edge'] = _hora_edge
            _r['htf_extreme'] = False
            _r['trigger'] = False
            _sc = 0.0
            _op = {}
            _isext = bool(_r.get('is_ext'))
            _modo = _r.get('modo_reco')
            _dirr = _r.get('dir_reco')
            _play = (_isext or _r.get('symbol') in _vp.ALWAYS_INCLUDE) and _modo in ('TENDENCIA', 'FADE') and (_dirr in ('LONG', 'SHORT'))
            if _r.get('signal'):
                _sc += 1.0
                _op['A1'] = 1.0
                if _r.get('prime'):
                    _sc += 3.0
                    _op['A2'] = 3.0
                if _r.get('yellow5m'):
                    _sc += 4.0
                    _op['A3'] = 4.0
                _sc += (_r.get('conv') or 0) * 0.6
                _op['A4'] = (_r.get('conv') or 0) * 0.6
                if (_r.get('conv') or 0) >= 2:
                    _sc += 1.0
                    _op['A5'] = 1.0
                _sc += (_r.get('sott_n') or 0) * 0.5
                _op['A6'] = (_r.get('sott_n') or 0) * 0.5
                if _r.get('sott_side') == _r.get('signal') and '1h' in (_r.get('sott_tfs') or ''):
                    _sc += 2.5
                    _op['A7'] = 2.5
                    _r['htf_extreme'] = True
                _stfs = _r.get('sott_tfs') or ''
                _sn = _r.get('sott_n') or 0
                if _r.get('sott_side') and '5m' in _stfs and (_sn >= 2):
                    _r['sott_conf'] = 'MAXIMA' if _sn >= 3 else 'ALTA'
                    _r['sott_conf_nota'] = 'etiqueta informativa: la confluencia SOTT se midio sobre 750 dias y NO anticipa (3 TF = -0.24%/24h)'
                if _r.get('sf_mid_dist') is not None:
                    _sc += 2.0
                    _op['A8'] = 2.0
                _ds = _r.get('delta_surge') or 0
                _rc = _r.get('cvd') or 0
                _dtrig = (_r.get('signal') == 'SHORT' and _rc < 0 or (_r.get('signal') == 'LONG' and _rc > 0)) and _ds >= 1.5
                if _dtrig:
                    _sc += 2.0
                    _op['A9'] = 2.0
                    _r['trigger'] = True
                elif _ds >= 1.5:
                    _sc += 0.5
                    _op['A9'] = 0.5
                if (_r.get('sig_abs') or 0) >= 3.0:
                    _sc -= 1.5
                    _op['A10'] = -1.5
                if _r.get('z_contra'):
                    _sc -= 5.0
                    _op['A11'] = -5.0
            if _play:
                if not _r.get('signal'):
                    _sc += 1.5
                    _op['B1'] = 1.5
                if _modo == 'TENDENCIA':
                    _tc = _r.get('trend_conf') or 0
                    _op['B2'] = 3.0 if _tc >= 3 else 1.0 if (_r.get('sott_agree') or 0) >= 2 else 0.0
                    _sc += _op['B2']
                    if _r.get('dir_reforzada'):
                        _sc += 1.0
                        _op['B3'] = 1.0
                    _op['B4'] = min(max((_r.get('adx') or 0) - 25.0, 0.0) / 15.0, 1.0)
                    _sc += _op['B4']
                    if _r.get('cont_ok'):
                        _sc += (_r.get('cont_score') or 0) * 0.3
                        _op['B5'] = (_r.get('cont_score') or 0) * 0.3
                else:
                    _fc = _r.get('fade_conf') or 0
                    _op['B6'] = 2.5 if _fc >= 3 else 1.5 if _fc >= 2 else 0.0
                    _sc += _op['B6']
                    _op['B7'] = min(_r.get('sig_abs') or 0, 3.5) * 0.5
                    _sc += _op['B7']
                    if _r.get('yellow5m') and _dirr == 'LONG':
                        _sc += 1.5
                        _op['B8'] = 1.5
                    _fcvd = _r.get('cvd') or 0
                    if _dirr == 'SHORT' and _fcvd < 0 or (_dirr == 'LONG' and _fcvd > 0):
                        _sc += 1.0
                        _op['B9'] = 1.0
                        _r['fade_trig5m'] = True
                        if (_r.get('delta_surge') or 0) >= 1.5:
                            _sc += 0.5
                            _op['B9'] = 1.5
                _seq = _r.get('secuencia') or ''
                if _r.get('seq_side') == _dirr:
                    _op['B10'] = 2.0 if _seq.startswith('3') else 1.0 if _seq.startswith('2') else 0.0
                    _sc += _op['B10']
            if _r.get('signal') or _play:
                if _r.get('liq_ok'):
                    _sc += 2.0
                    _op['C1'] = 2.0
                if (_r.get('conf_n') or 0) >= 2:
                    _sc += 1.0
                    _op['C2'] = 1.0
                _cr = _r.get('cluster_reach') or 0
                if _cr >= 1.6:
                    _sc += 1.5
                    _op['C3'] = 1.5
                elif _cr >= 1.0:
                    _sc += 0.5
                    _op['C3'] = 0.5
                if (_r.get('cluster_n') or 1) >= 2:
                    _sc += 0.5
                    _op['C4'] = 0.5
                _trh = _r.get('target_reach') or 0
                if _trh >= 60:
                    _sc += 1.0
                    _op['C5'] = 1.0
                elif _trh >= 45:
                    _sc += 0.5
                    _op['C5'] = 0.5
                if _goodHr and (not _wknd):
                    _sc += 1.0
                    _op['C6'] = 1.0
                if _badHr or _wknd:
                    _sc -= 1.0
                    _op['C6'] = -1.0
                _ht = _r.get('htf_trend')
                if _ht in ('up', 'down') and _dirr in ('LONG', 'SHORT'):
                    if _dirr == 'SHORT' and _ht == 'up' or (_dirr == 'LONG' and _ht == 'down'):
                        _sc -= 3.0
                        _r['htf_contra'] = True
                        _op['C7'] = -3.0
                    else:
                        _sc += 1.0
                        _r['htf_aligned'] = True
                        _op['C7'] = 1.0
            if _isext or _r.get('signal') or _r.get('symbol') in _vp.ALWAYS_INCLUDE:
                _r['op_score'] = round(_sc, 2)
                _r['op_partes'] = {k: round(v, 4) for k, v in _op.items() if v}
                _r['op_src'] = 'engine'
                _r['op_grade'] = 'TOP' if _sc >= 9 else 'BUENA' if _sc >= 5 else 'OK' if _sc >= 3 else ''
        try:
            import setup_flow as _sf
            _sf.update_all(rows, _t.time())
        except Exception:
            pass
        try:
            import confluence_history as _ch
            _nowh = _t.time()
            _ch.log_snapshot(rows, _nowh)
            for _r in rows:
                _r['conf_track'] = _ch.anticipation(_r.get('symbol'), _r.get('htf_side'), _nowh, _r.get('px'))
        except Exception:
            pass
        try:
            _inject_confluence_rows(rows)
        except Exception:
            pass
        for _r in rows:
            _sym = _r.get('symbol')
            try:
                import session_volume as _sv
                _r['vol_push'] = _sv.push(_sym, _sym in _STOCKS)
            except Exception:
                pass
            try:
                import smc_zones as _smcz
                _r['smc'] = _smcz.smc_live(_sym)
            except Exception:
                _r['smc'] = None
            if _sym in ('TQQQUSDT', 'SOXLUSDT'):
                try:
                    import tqqq_wave as _tw
                    _r['wave'] = _tw.signal(_sym)
                except Exception:
                    _r['wave'] = None
            try:
                _r['regime_tag'] = _row_regime_tag(_r, _regime_ctx())
            except Exception:
                _r['regime_tag'] = None
            _sl = _stock_liq_cache.get(_sym)
            if _sl:
                _r['oi_levels'] = _sl
                _r['max_pain'] = _sl.get('max_pain')
                _r['liq_below'] = _sl.get('load_below')
                _r['liq_above'] = _sl.get('load_above')
                _r['liq_bias'] = _sl.get('liq_bias')
                _r['liq_align'] = _r.get('dir_reco') == _sl.get('liq_bias') if _sl.get('liq_bias') else None
                _dr = _r.get('dir_reco')
                _mg = _sl.get('magnet_down') if _dr == 'SHORT' else _sl.get('magnet_up') if _dr == 'LONG' else None
                if _mg and _mg.get('px'):
                    _r['target'] = _mg['px']
                    _r['target_full'] = _mg['px']
                    _r['target_kind'] = 'OI'
                    _r['dist_target'] = _mg.get('dist_pct')
                    _r['liq_ok'] = True
                    _r['liq_dist'] = _mg.get('dist_pct')
                    _r['conf_liq'] = True
                continue
            _vb = _r.get('vol_below')
            if _vb is not None:
                _r['liq_below'] = _vb
                _r['liq_above'] = 100 - _vb
                _r['liq_bias'] = None
                _r['liq_align'] = None
            try:
                import orderbook_liq as _ob
                _obd = _ob.depth(_sym)
                if _obd:
                    _r['ob'] = _obd
                    _r['ob_bias'] = _obd.get('ob_bias')
                    _r['ob_imbalance'] = _obd.get('imbalance')
            except Exception:
                pass
            try:
                import liq_weight as _lw
                _r['liq_w'] = _lw.weigh(_sym)
            except Exception:
                _r['liq_w'] = None
            try:
                import positioning as _pos
                _r['pos'] = _pos.positioning(_sym)
            except Exception:
                _r['pos'] = None
        try:
            import session_projection as _sp
            for _r in rows:
                _pside = _r.get('htf_side') or _r.get('dir_reco')
                if _pside in ('LONG', 'SHORT'):
                    _r['session_proj'] = _sp.project(_r.get('symbol'), _pside)
                _r['sess_trace'] = _sp.session_trace(_r.get('symbol'))
        except Exception:
            pass
        _now_v = _t.time()
        for _r in rows:
            try:
                _r['verdict'] = _verdict_with_latch(_r, _now_v)
            except Exception:
                _r['verdict'] = None
        try:
            import market_regime as _mkr3
        except Exception:
            _mkr3 = None
        for _r in rows:
            _pdir = _r.get('dir_reco') or (_r.get('verdict') or {}).get('side')
            _L = {}
            try:
                _mb = _mkr3.bias_for(_r.get('symbol'))[0] if _mkr3 else None
            except Exception:
                _mb = None
            _corr_ok_cf = True
            try:
                _cbi = _r.get('corr_btc')
                if _cbi and (not _cbi.get('aplica_contexto', True)):
                    _corr_ok_cf = False
            except Exception:
                pass
            _L['contexto'] = (_mb == _pdir if _mb and _pdir else None) if _corr_ok_cf else None
            _lwv = _r.get('liq_w')
            _ls = _lwv.get('side') if isinstance(_lwv, dict) else None
            _L['lugar'] = _ls == _pdir if _ls and _pdir else None
            _ps = (_r.get('pos') or {}).get('fade_side')
            _L['posicionamiento'] = _ps == _pdir if _ps and _pdir else None
            _gat = bool(_r.get('signal') or str(_r.get('secuencia') or '').startswith('3') or _r.get('trigger') or _r.get('fade_trig5m'))
            _L['gatillo'] = _gat
            _dd = (_r.get('dow_bias') or {}).get('side')
            _L['dia'] = _dd == _pdir if _dd and _pdir else None
            _dia_aplica = (_r.get('dow_bias') or {}).get('side') is not None or (_r.get('dow_bias') or {}).get('note') is not None
            _sc = sum((1 for k in ('contexto', 'lugar', 'dia') if _L[k])) + (1 if _gat else 0)
            _r['confluence'] = {'dir': _pdir, 'score': _sc, 'max': 4 if _dia_aplica else 3, 'layers': _L, 'pos_refutado': True}
        _brsum = None
        try:
            _br = _bot_real_data()
            _bp = _br.get('pos') or {}
            for _r in rows:
                _r['bot_real'] = _bp.get(_r.get('symbol'))
            _brsum = _br.get('summary')
        except Exception:
            pass
        _carry = []
        _sl_lbl = {'ny': 'WS', 'asia': 'Asia', 'london': 'Londres'}
        for _r in rows:
            _cf = [t for t in _r.get('sess_trace') or [] if t.get('side') and (t.get('agree') or 0) >= 2]
            if not _cf:
                continue
            _mc = _cf[-1]
            _chain = [_sl_lbl.get(t['sess'], t['sess']) for t in _cf if t.get('side') == _mc.get('side')]
            _carry.append({'sym': _r.get('symbol'), 'side': _mc.get('side'), 'agree': _mc.get('agree'), 'chain': _chain, 'stock': _r.get('symbol') in _STOCKS})
        _carry.sort(key=lambda c: (-(c.get('agree') or 0), -len(c.get('chain') or [])))
        _stocks_hidden = 0
        rows.sort(key=lambda r: -(r.get('op_score') or 0))
        for _r in rows:
            _r['priority'] = _r.get('symbol') in _PRIORITY_SYMS
        rows.sort(key=lambda r: 0 if r.get('priority') else 1)
        _mctx = None
        try:
            import market_regime as _mkr2
            _mctx = _mkr2.context()
        except Exception:
            pass
        _dow = None
        try:
            import dow_bias as _dwb
            _dow = _dwb.today_summary()
        except Exception:
            pass
        try:
            import panel_edge as _pe
            _stp = _os2.path.join(_os2.path.dirname(__file__), '..', 'forward_test', 'vp_engine_state.json')
            _dec = {}
            try:
                _stj = json.load(open(_stp, encoding='utf-8'))
                if _t.time() - float(_stj.get('ts') or 0) < 600:
                    _dec = _stj.get('decisions') or {}
            except Exception:
                _dec = {}
            for _r in rows:
                try:
                    _r['edge'] = _pe.for_row(_r)
                except Exception:
                    _r['edge'] = None
                _r['engine'] = _dec.get(_r.get('symbol'))
        except Exception:
            pass
        try:
            import rates_regime as _rr3
            _rc3 = _rr3.context()
            _rtag = '%s:%+d' % (_rc3.get('regime'), int(_rc3.get('score') or 0))
            for _r in rows:
                _r.setdefault('rates', _rtag)
        except Exception:
            pass
        for _r in rows:
            try:
                _r['decision'] = _direct_decision(_r)
                try:
                    _eng = _engine_positions()
                    _ep = _eng.get(_r.get('symbol'))
                    if _ep and isinstance(_r['decision'], dict):
                        _venue, _side_e, _cell_e, _t_e = _ep
                        if _venue == 'real':
                            _r['decision'] = dict(_r['decision'], dec=_side_e, side=_side_e, score=max(int(_r['decision'].get('score') or 0), 85), tag='🤖 REAL abierta por el motor: %s desde %s UTC' % (_cell_e, _t_e))
                        else:
                            _r['decision'] = dict(_r['decision'], dec=_side_e, side=_side_e, score=max(int(_r['decision'].get('score') or 0), 55), tag='🧪 paper abierta por el motor: %s desde %s UTC' % (_cell_e, _t_e))
                except Exception:
                    pass
                _rg = _r.get('regime_tag') or {}
                if _rg.get('kind') == 'IBS' and _rg.get('fire') and isinstance(_r['decision'], dict):
                    _r['decision'] = dict(_r['decision'], dec='LONG', side='LONG', score=max(int(_r['decision'].get('score') or 0), 70), tag='📐 IBS %.2f + VIX>20: fuente diaria del motor (LONG al cierre, salida IBS>0.9)' % _rg.get('ibs', 0))
                elif _rg.get('kind') == 'ER' and isinstance(_r['decision'], dict) and (_r['decision'].get('dec') in ('LONG', 'SHORT')) and (float(_rg.get('er') or 0) >= 0.3) and (((_rg.get('dir') or 0) > 0) != (_r['decision']['dec'] == 'LONG')):
                    _r['decision'] = dict(_r['decision'], tag=((_r['decision'].get('tag') or '') + ' · ⚠ contra tendencia eficiente (ER24 %.2f): fade medido negativo' % float(_rg.get('er') or 0)).strip(' ·'))
            except Exception:
                _r['decision'] = None
        try:
            import gex_live as _gl
            _gl.start()
            for _r in rows:
                try:
                    _sy = _r.get('symbol')
                    _r['gex'] = _gl.for_row(_r) if _sy not in _STOCKS or _sy in getattr(_gl, 'EQUITY_MAP', {}) else None
                except Exception:
                    _r['gex'] = None
                _gx = _r.get('gex') or {}
                if _gx.get('aplica') and isinstance(_r.get('decision'), dict) and (_r['decision'].get('dec') in ('LONG', 'SHORT')):
                    _vd_g = _gx.get('veredicto')
                    if _vd_g in ('VETAR', 'REDUCIR'):
                        _pen = (_gx.get('penalizaciones') or ['estructura de gamma en contra'])[0]
                        _txt = 'GEX %s (score %s): %s' % (_vd_g, _gx.get('score'), _pen)
                        _r['decision'] = dict(_r['decision'], tag=((_r['decision'].get('tag') or '') + ' | ' + _txt).strip(' |'))
                        _fade_en_gamma_negativa = _gx.get('tipo_senal') == 'REVERSION' and _gx.get('regimen') == 'NEGATIVO'
                        if GEX_VETO_ACTIVO and _fade_en_gamma_negativa:
                            _r['decision'] = dict(_r['decision'], dec='ESPERAR', side=_r['decision'].get('dec'), tag=((_r['decision'].get('tag') or '') + ' | VETO GEX: fade en gamma negativa (medido -0.362%/op vs -0.169% en gamma positiva, 5 de 6 anios)').strip(' |'))
        except Exception:
            pass
        try:
            _gex_ctx = {_c: _gl.levels(_c, 'scalp') for _c in _gl.COINS}
        except Exception:
            _gex_ctx = None
        try:
            import operables as _opm
            for _r in rows:
                try:
                    _r['op'] = _opm.evaluar(_r.get('symbol'))
                except Exception:
                    _r['op'] = None
        except Exception:
            pass
        try:
            import ctx_fila as _cxf
            _gex_btc_ctx = None
            try:
                _gex_btc_ctx = (_gex_ctx or {}).get('BTC')
            except Exception:
                pass
            _reg_ctx = None
            try:
                _reg_ctx = _regime_ctx()
            except Exception:
                _reg_ctx = None
            _rates_for_ctx = None
            try:
                import rates_regime as _rr4
                _rates_for_ctx = _rr4.context()
            except Exception:
                _rates_for_ctx = None
            for _r in rows:
                try:
                    _r['ctx'] = _cxf.resolver(_r, mctx=_mctx, regime=_reg_ctx, rates_ctx=_rates_for_ctx, gex_btc=_gex_btc_ctx, stocks=_STOCKS)
                except Exception:
                    _r['ctx'] = None
        except Exception:
            pass
        _gex_avisos = []
        try:
            _gex_avisos = _gl.aviso_proyeccion(rows, min_pct=1.0)
            for _av in [] if DASHBOARD_UI_ONLY else _gex_avisos:
                try:
                    import alert_hub as _ah
                    _txt = 'proyeccion de %.1f por ciento en %s hacia %s, la estructura de gamma lo permite' % (_av['pct'], _av['symbol'].replace('USDT', ''), 'arriba' if _av['lado'] == 'LONG' else 'abajo')
                    _ah.gate('gexproj-%s-%s-%d' % (_av['symbol'], _av['lado'], int(_t.time() // 900)), _av['symbol'], _av['lado'], _txt)
                except Exception:
                    pass
        except Exception:
            _gex_avisos = []
        try:
            import rates_regime as _rr2
            _rates_ctx = _rr2.context()
        except Exception:
            _rates_ctx = None
        out = {'rows': rows, 'n': len(rows), 'ts': _t.time(), 'ws_open': _ws_open(), 'gex': _gex_ctx, 'gex_avisos': _gex_avisos, 'watch': _cw_cache.get('data') or [], 'bot_real': _brsum, 'mkt_ctx': _mctx, 'stocks_hidden': _stocks_hidden, 'carry_watch': _carry[:12], 'dow': _dow, 'rates_ctx': _rates_ctx, 'regime': _regime_ctx()}
    except Exception as _e:
        import traceback as _tb, sys as _sys
        _trace = _tb.format_exc()
        try:
            print('[vp_extremes] ERROR ' + str(_e) + chr(10) + _trace, file=_sys.stderr, flush=True)
        except Exception:
            pass
        out = {'error': str(_e), 'rows': [], 'ws_open': _ws_open(), 'trace': _trace[-2500:]}
    _vp_ext_cache['data'] = out
    _vp_ext_cache['ts'] = _t.time()
    return out
_CF_BANDA = 0.3
_DERIV_CACHE = {}
_DERIV_TTL = 1800.0
_DERIV_PRESUPUESTO = 6

def _deriv_cached(_gl, sym, _budget=None):
    """
    Niveles del lider proyectados al alt, cacheados y con PRESUPUESTO por pedido.

    La primera vez que se calcula un alt hay que bajar sus velas de 4h (las de BTC,
    ETH y la cesta ya quedan en el cache de gex_live), asi que cuesta ~1 s. Con 25
    alts en frio eso serian 25 s dentro de un request. Se calculan como mucho
    `_DERIV_PRESUPUESTO` nuevos por pedido: el resto sale del cache y se va
    llenando entre llamadas sucesivas y con el refresher de 30 min.
    """
    import time as _t
    key = str(sym).upper()
    hit = _DERIV_CACHE.get(key)
    if hit and _t.time() - hit[0] < _DERIV_TTL:
        return hit[1]
    if _budget is not None and _budget[0] <= 0:
        return hit[1] if hit else None
    try:
        d = _gl.derivado(key)
    except Exception:
        d = None
    if _budget is not None:
        _budget[0] -= 1
    _DERIV_CACHE[key] = (_t.time(), d)
    return d

def api_tv_niveles():
    """
    __TV_NIVELES_2026_09_09__ — TODOS los niveles del panel, en una sola cadena
    para pegar en TradingView antes de operar.

    Decision del usuario: «le pego antes de operar todos los datos del dashboard
    manualmente y listo». Entonces que esa unica pegada traiga TODO, no solo la
    gamma.

    Formato plano, un bloque por activo separado por ';', campos por '~':

        CLAVE~PX:precio~POC:x~VAL:x~VAH:x~VW:x~TG:x~IM:x:peso~MP:x
              ~FL:flip~P1:centro:lo:hi:fuerza~...~VD:LADO:MODO:score

    Todos los campos son OPCIONALES: si el panel no lo tiene, no va. El indicador
    dibuja lo que encuentra.

    A diferencia de /api/gex_pine, aca van TODOS los activos del panel, no solo los
    que tienen cadena de opciones: el perfil de volumen, el iman de liquidacion y
    el veredicto existen para todos. La gamma se agrega solo donde es legitima.
    """
    try:
        import sys as _sys, os as _os2, time as _tt
        _ft = _os2.path.join(_os2.path.dirname(__file__), '..', 'forward_test')
        if _ft not in _sys.path:
            _sys.path.insert(0, _ft)
        import gex_live as _gl
        import gex_zonas as _gz
        from direction_engine.pine_export_quality import source_fields, verified_confluences
        try:
            import ob_flow_niveles as _obf
        except Exception:
            _obf = None
        try:
            import zonas_perfil as _zp
        except Exception:
            _zp = None
        try:
            import gex_griegas as _ggr
        except Exception:
            _ggr = None
        d = _vp_ext_cache['data'] or {}
        filas = list(d.get('rows') or [])
        _base = ['BTCUSDT', 'ETHUSDT', 'QQQUSDT', 'SPYUSDT', 'SMHUSDT', 'IWMUSDT', 'TQQQUSDT', 'SOXLUSDT', 'SPXLUSDT', 'XAUUSDT', 'XAGUSDT', 'CLUSDT', 'XAUTUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT', 'TAOUSDT', 'SUIUSDT', 'TONUSDT']
        _vistos = set()
        for _r in filas:
            _s = str(_r.get('symbol') or '').upper()
            if _s:
                _vistos.add(_s)
        for x in _base + sorted(_STOCKS):
            if x not in _vistos:
                _vistos.add(x)
                filas.append({'symbol': x})

        def num(v, dec=6):
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            if f != f or f in (float('inf'), float('-inf')) or f == 0:
                return None
            return '%%.%dg' % dec % f
        _APAL = {'TQQQUSDT', 'SOXLUSDT', 'SPXLUSDT'}
        _pres = [_DERIV_PRESUPUESTO]
        partes, con_gamma, con_drv = ([], 0, 0)
        for r in filas:
            sym = str(r.get('symbol') or '').upper()
            if not sym:
                continue
            clave = sym[:-4] if sym.endswith('USDT') else sym
            campos = [clave]
            _propios = []
            for etiq, k, _nm, _gp, _pz in (('POC', 'poc', 'POC', 'POC', 1.6), ('VAL', 'val', 'VAL', 'VA', 1.0), ('VAH', 'vah', 'VAH', 'VA', 1.0), ('VW', 'vwap', 'VWAP', 'VWAP', 1.2)):
                try:
                    _v = float(r.get(k) or 0)
                except (TypeError, ValueError):
                    _v = 0.0
                if _v > 0:
                    _propios.append((_v, _nm, _gp, _pz))
            for etiq, k in (('TG', 'obj2'), ('MP', 'max_pain')):
                v = num(r.get(k)) or (num(r.get('target')) if k == 'obj2' else None)
                if v:
                    campos.append('%s:%s' % (etiq, v))
            zops = None
            if _zp is not None:
                try:
                    zops = _zp.zonas_operativas(r)
                except Exception:
                    zops = None
            if zops and zops.get('regimen'):
                campos.append('RG:%s:%s:%s:%s' % (str(zops.get('regimen') or '-')[0], num(zops.get('adx'), 3) or '0', str(zops.get('modo') or '-')[0], str(zops.get('lado') or '-')[0]))
                for et, k in (('ZC', 'compra'), ('ZV', 'venta')):
                    zz_ = zops.get(k)
                    if zz_ and num(zz_['lo']) and num(zz_['hi']):
                        campos.append('%s:%s:%s' % (et, num(zz_['lo']), num(zz_['hi'])))
                if zops.get('veto'):
                    campos.append('VETO:1')
            lp, lw = (num(r.get('liq_px')), r.get('liq_w'))
            if lp:
                campos.append('IM:%s:%s' % (lp, num(lw, 3) or '0'))
            try:
                from direction_engine import retail_whale as _rw
                _rwd = _rw.snapshot(sym)
                if _rwd and (_rwd.get('retail_long') is not None or _rwd.get('whale_long') is not None):
                    campos.append('RW:%s:%s:%s:%d:%s:%s:%s:%s:%s:%s' % (_rwd.get('retail_long') if _rwd.get('retail_long') is not None else '-', _rwd.get('whale_long') if _rwd.get('whale_long') is not None else '-', (_rwd.get('net_side') or '-')[0], int(_rwd.get('strength') or 0), (_rwd.get('base_side') or '-')[0], _rwd.get('whale_med') if _rwd.get('whale_med') is not None else '-', _rwd.get('z_spread') if _rwd.get('z_spread') is not None else '-', _rwd.get('retail_med') if _rwd.get('retail_med') is not None else '-', _rwd.get('zw') if _rwd.get('zw') is not None else '-', _rwd.get('zr') if _rwd.get('zr') is not None else '-'))
            except Exception:
                pass
            try:
                from direction_engine import estrategia_signals as _est
                _sig = _est.todas(['BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE', 'ADA', 'AVAX', 'LINK', 'LTC', 'BCH', 'DOT', 'ATOM', 'UNI', 'ETC', 'FIL']).get(clave)
                if _sig is not None:
                    _sd, _z = _sig
                    campos.append('ST:%s:%s' % ('L' if _sd > 0 else 'S' if _sd < 0 else 'N', num(_z, 3) or '0'))
            except Exception:
                pass
            obs = []
            if _obf is not None and sym.endswith('USDT'):
                try:
                    obs = _obf.seleccionar_ob_exportacion(sym)
                except Exception:
                    obs = []
                for ob in obs[:3]:
                    campos.append('OB:%s:%s:%s:%d' % (num(ob['top']), num(ob['bottom']), 'L' if ob['lado'] == 'LONG' else 'S', int(ob['score'])))
                _ob_times = [ob.get('source_closed_ts') for ob in obs[:3]]
                if _ob_times and all((isinstance(t, (int, float)) and t > 0 for t in _ob_times)):
                    campos.extend(['BTS:%.3f' % min(_ob_times), 'BTTL:900'])
            st, escala = (None, None)
            coin = _gl.coin_for(sym)
            if coin:
                st = _gl.state(coin, 'scalp')
            elif clave in _gz.APALANCADOS:
                _idx, _lev = _gz.APALANCADOS[clave]
                st = _gl.equity_state(_idx)
                _pxa = r.get('px')
                if not _pxa:
                    _sa = _gl.equity_state(clave)
                    _pxa = getattr(_sa, 'spot', None) if _sa is not None else None
                if st is not None and _pxa:
                    escala = (_lev, float(_pxa), _idx)
                else:
                    st = None
            elif sym in _STOCKS or clave in ('QQQ', 'SPY', 'SMH', 'IWM'):
                st = _gl.equity_state(clave)
            if st is not None:
                _sp = getattr(st, 'spot', None)
                _gref = escala[2] if escala else getattr(st, 'coin', None) or coin or clave
                campos.extend(source_fields(st, _gref, scaled=bool(escala)))

                def _esc(v, _sp=_sp, escala=escala):
                    if v is None or not escala:
                        return v
                    return _gz.escalar_precio(v, _sp, escala[1], escala[0])
                fl = num(_esc(getattr(st, 'gamma_flip', None)))
                if fl:
                    campos.append('FL:%s' % fl)
                cw = num(_esc(getattr(st, 'call_wall', None)))
                pw = num(_esc(getattr(st, 'put_wall', None)))
                if escala and escala[0] < 0:
                    cw, pw = (pw, cw)
                if cw:
                    campos.append('CW:%s' % cw)
                if pw:
                    campos.append('PW:%s' % pw)
                if _ggr is not None:
                    try:
                        _gr = _ggr.niveles_de_estado(st)
                    except Exception:
                        _gr = None
                    if _gr:
                        _inv = bool(escala and escala[0] < 0)
                        for _et, _k, _ks in (('GVW', 'vanna_wall', 'vanna_wall_signo'), ('GVF', 'vanna_flip', None), ('GCW', 'charm_wall', 'charm_wall_signo'), ('GDW', 'delta_wall', 'delta_wall_signo'), ('GDZ', 'delta_zero', None), ('GVT', 'vol_trigger', None), ('GMX', 'gamma_max', None)):
                            _v = num(_esc(_gr.get(_k)))
                            if not _v:
                                continue
                            if _ks:
                                _s = int(_gr.get(_ks) or 0)
                                if _inv:
                                    _s = -_s
                                campos.append('%s:%s:%d' % (_et, _v, _s))
                            else:
                                campos.append('%s:%s' % (_et, _v))
                if not escala:
                    try:
                        import options_context as _oc
                        _rows = getattr(st, '_rows', None)
                        if _rows:
                            _exps = sorted({rr.expiry_ts for rr in _rows if rr.expiry_ts > getattr(st, 'ts', 0)})
                            if _exps:
                                _near = [rr for rr in _rows if rr.expiry_ts == _exps[0]]
                                _mp = _oc.max_pain(_near)
                                if _mp and _mp.get('max_pain'):
                                    _v = num(_mp['max_pain'])
                                    if _v:
                                        campos.append('OMAXPAIN:%s' % _v)
                                    if _mp.get('put_call_ratio'):
                                        campos.append('OPUTCALL:%.2f' % _mp['put_call_ratio'])
                            _reg = _oc._regime(getattr(st, 'net_gex_musd', None), st.spot, getattr(st, 'zero_gamma_zone', None))
                            if _reg and _reg != 'UNKNOWN':
                                campos.append('OGAMMAREG:%s' % _reg)
                            _lad = getattr(st, 'ladder', None)
                            if _lad:
                                _cw2 = _oc._second_wall(_lad, st.spot, True, getattr(st, 'call_wall', None))
                                _pw2 = _oc._second_wall(_lad, st.spot, False, getattr(st, 'put_wall', None))
                                if _cw2 and num(_cw2):
                                    campos.append('OCALLW2:%s' % num(_cw2))
                                if _pw2 and num(_pw2):
                                    campos.append('OPUTW2:%s' % num(_pw2))
                    except Exception:
                        pass
                try:
                    z = _gl._zonas_de(st, r.get('atr_pct'))
                    if z and escala:
                        z = _gz.escalar_zonas(z, _sp, escala[1], escala[0])
                except Exception:
                    z = None
                if z:
                    try:
                        marcas = _gz.marcas_confluencia(z, poc=r.get('poc'), val=r.get('val'), vah=r.get('vah'), call_wall=getattr(st, 'call_wall', None), put_wall=getattr(st, 'put_wall', None), flip=getattr(st, 'gamma_flip', None), obs=obs if not escala else None)
                    except Exception:
                        marcas = {}
                    hay = False
                    for k in ('P1', 'N1', 'A1'):
                        zz = z.get(k)
                        if not zz:
                            continue
                        mk = marcas.get(k) or ''
                        if _zp is not None and zops and (not escala):
                            _mz = _zp.marca_para(zops, zz['center'], st.spot)
                            if _mz:
                                mk = mk + '+' + _mz if mk else _mz
                        try:
                            rd = _gz.lectura_zona(k, mk, zz.get('dist_pct'))
                        except Exception:
                            rd = ''
                        for mal, bien in ((':', ' -'), ('~', '-'), (';', ',')):
                            rd = rd.replace(mal, bien)
                        campos.append('%s:%.6g:%d:%s' % (k, zz['center'], zz['strength'], mk))
                        hay = True
                    if hay:
                        con_gamma += 1
            if st is None and sym.endswith('USDT') and (sym not in _STOCKS) and (clave not in _gz.APALANCADOS):
                dv = _deriv_cached(_gl, sym, _pres)
                if dv and dv.get('aplica'):
                    campos.extend(['GSRC:D', 'GREF:%s' % (dv.get('ref') or 'UNKNOWN')])
                    campos.append('DRV:%s:%s:%s:%s' % (dv.get('ref') or '?', num(dv.get('corr'), 3) or '0', num(dv.get('beta_ref'), 3) or '0', num(dv.get('banda_pct'), 3) or '0'))
                    _MAPA_DRV = {'flip': 'FL', 'techo': 'CW', 'piso': 'PW', 'gamma_max': 'GMX', 'vanna_wall': 'GVW', 'vanna_flip': 'GVF', 'charm_wall': 'GCW', 'delta_wall': 'GDW', 'delta_zero': 'GDZ', 'vol_trigger': 'GVT'}
                    con_drv += 1
                    for lv_ in dv.get('niveles') or []:
                        if lv_.get('tapado'):
                            continue
                        et = _MAPA_DRV.get(lv_.get('nombre'))
                        v_ = num(lv_.get('px'))
                        if et and v_:
                            campos.append('%s:%s' % (et, v_))
            try:
                _cfz = verified_confluences(campos, r, _tt.time(), band=_CF_BANDA)
            except Exception:
                _cfz = []
            campos.append('CFV:2')
            for _j, _z in enumerate(_cfz[:3]):
                _cv = num(_z['centro'], 12)
                if _cv:
                    campos.append('CF:%s:%d:%s:%.12g:%.12g:%d:%d' % (_cv, _z['n'], '-'.join(_z['nombres']), _z['lo'], _z['hi'], _z['members'], int(_z['valid_until'])))
            try:
                from direction_engine.prima_coinbase import campo_cadena as _pr_campo
                _pr = _pr_campo(clave)
                if _pr:
                    campos.append(_pr)
            except Exception:
                pass
            try:
                from direction_engine.vol_objetivo import campo_cadena as _vo_campo
                _vo = _vo_campo()
                if _vo:
                    campos.append(_vo)
            except Exception:
                pass
            lado = str(r.get('dir_reco') or '').upper()
            modo = str(r.get('modo_reco') or '').upper()
            sc = r.get('op_score')
            if lado in ('LONG', 'SHORT'):
                campos.append('VD:%s:%s:%s' % (lado[0], (modo or '-')[0], num(sc, 3) or '0'))
            if len(campos) > 1:
                partes.append('~'.join(campos))
        _TOPE = int(request.args.get('max') or 24)
        if len(partes) > _TOPE:
            _pri = []
            for _i, _pt in enumerate(partes):
                _k = _pt.split('~')[0]
                _tiene_gamma = ':FL:' in '~' + _pt or _pt.find('~FL:') >= 0
                _es_major = _k in ('BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE', 'AVAX', 'LINK', 'TAO', 'SUI')
                _es_commodity = _k in ('CL', 'XAU', 'XAG', 'XAUT')
                _rango = 0 if _es_commodity else 1 if _es_major else 2 if _tiene_gamma else 3
                _pri.append(((_rango, _i), _pt))
            _pri.sort(key=lambda x: x[0])
            partes = [x[1] for x in _pri[:_TOPE]]
        txt = ';'.join(partes)
        con_gamma = sum((any((p.startswith(('P1:', 'N1:', 'A1:')) for p in block.split('~'))) for block in partes))
        con_drv = sum(('~DRV:' in block for block in partes))
        n_activos = len(partes)
        _g = globals()
        from_cache = False
        if n_activos >= 3 and len(txt) > 200:
            _g['_TV_NIVELES_LAST'] = {'txt': txt, 'n': n_activos, 'cg': con_gamma, 'cd': con_drv, 'ts': _tt.time()}
        elif _g.get('_TV_NIVELES_LAST'):
            _lg = _g['_TV_NIVELES_LAST']
            txt, n_activos, con_gamma, con_drv = (_lg['txt'], _lg['n'], _lg['cg'], _lg['cd'])
            from_cache = True
        _edad = round(_tt.time() - _g['_TV_NIVELES_LAST']['ts'], 1) if _g.get('_TV_NIVELES_LAST') else 0.0
        if request.args.get('json'):
            return _resp_json({'cadena': txt, 'n': n_activos, 'con_gamma': con_gamma, 'con_derivado': con_drv, 'de_cache': from_cache, 'edad_s': _edad, 'ts': _tt.time()})
        from flask import Response as _R
        return _R(txt, mimetype='text/plain; charset=utf-8', headers={'X-Tv-Activos': str(n_activos), 'X-Tv-Con-Gamma': str(con_gamma), 'X-Tv-Derivados': str(con_drv), 'X-Tv-De-Cache': '1' if from_cache else '0', 'X-Tv-Edad-S': str(_edad), 'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        import traceback
        return _resp_json({'error': str(e), 'trace': traceback.format_exc()[-500:]}, 500)
_cw_cache = {'ts': 0.0, 'data': []}
_stock_liq_cache = {}
