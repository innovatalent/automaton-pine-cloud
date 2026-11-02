# -*- coding: utf-8 -*-
"""FILTRO DE CONFLUENCIA GLOBAL (2026-08-10, pedido usuario "todas las mejoras globales a todas las
estrategias, entra muy mal en varias"). Un solo lugar con toda la calidad de entrada: BTC lidera +
estructura MSS + flujo CVD + volumen a favor + OI (no squeeze) + liquidaciones + room a resistencia.
`confluence_block(sym, side)` -> (bloquea?, motivo). Todo con cache TTL (baja latencia) y FAIL-OPEN
(si falta un dato, NO bloquea). Lo usan forward_runner (todos los brazos) y swing_wall_paper."""
import time, os, sqlite3
import bitunix

HERE=os.path.dirname(os.path.abspath(__file__))
AUTODB=os.path.join(HERE,"..","automaton.db")
TTL=45

_C={}   # key -> (ts, valor)
def _cached(key, fn, ttl=TTL):
    t=time.time()
    if key in _C and t-_C[key][0]<ttl: return _C[key][1]
    try: v=fn()
    except Exception: v=None
    _C[key]=(t,v); return v

def klines(sym): return _cached(("k",sym), lambda: bitunix.klines_smart(sym,"5m",300)) or []
def klines_long(sym): return _cached(("kl",sym), lambda: bitunix.klines_smart(sym,"5m",1000), 120) or []

# ---------------- SOTT (valor barato/caro, config usuario 2000/10 bandas 1020/1080) ----------------
def _var_func(src, length):
    n=len(src); valpha=2.0/(length+1); VAR=[0.0]*n; vud=[0.0]*n; vdd=[0.0]*n
    for i in range(1,n):
        dd=src[i]-src[i-1]
        if dd>0: vud[i]=dd
        elif dd<0: vdd[i]=-dd
    su=sd=0.0
    for i in range(n):
        su+=vud[i]; sd+=vdd[i]
        if i-9>=0: su-=vud[i-9]; sd-=vdd[i-9]
        den=su+sd; vcmo=(su-sd)/den if den!=0 else 0.0; a=valpha*abs(vcmo)
        VAR[i]=a*src[i]+(1-a)*(VAR[i-1] if i>0 else 0.0)
    return VAR
def _stoch_pct(c,h,l,n):
    out=[50.0]*len(c)
    for i in range(n-1,len(c)):
        hh=max(h[i-n+1:i+1]); ll=min(l[i-n+1:i+1]); out[i]=100*(c[i]-ll)/(hh-ll) if hh>ll else 50.0
    return out
def sott_value(sym):
    """SOTT %K 0-100 (=1000-1100 en el grafico). El usuario usa 2000 en 5m = ~7 dias de lookback, que NO entra en las
    klines vivas de 5m (~1000). Se computa el MISMO lookback de 7 dias en 1h (pk=167h ~ 2000x5m) -> mismo rango
    barato/caro con datos disponibles. Fiel al nivel que ve el usuario aunque el chart sea 5m."""
    def q():
        try: K=bitunix.klines_smart(sym,"1h",300)
        except Exception: K=[]
        if not K or len(K)<80: return 50.0
        c=[x[4] for x in K]; h=[x[2] for x in K]; l=[x[3] for x in K]
        pk=min(167, max(60, len(c)-20))
        k=_var_func(_stoch_pct(c,h,l,pk), 10)
        return k[-1] if k and k[-1] is not None else 50.0
    return _cached(("sott",sym), q, 90)

# umbrales (usuario: long solo <=1020, short solo >=1080; 1050 = punto medio). Configurables via swing_config.json.
import os as _os, json as _json
def _cfgval(key, default):
    try:
        with open(_os.path.join(HERE,"swing_config.json")) as f: return float(_json.load(f).get(key, default))
    except Exception: return default
def sott_zone_block(sym, side):
    """(bloquea?, motivo). OPERA TODAS LAS ZONAS; solo bloquea el EXTREMO equivocado: NO longear ARRIBA del techo
    (SOTT>1080) ni shortear ABAJO del piso (SOTT<1020). En el medio (1020-1080) opera AMBOS lados. La probabilidad
    se inclina con el tamaño (sott_bias). Default long_max=80(1080)/short_min=20(1020); configurables."""
    v=sott_value(sym)
    lmax=_cfgval("sott_long_max",80.0); smin=_cfgval("sott_short_min",20.0)
    d="LONG" if str(side).upper()=="LONG" else "SHORT"
    if d=="LONG"  and v>lmax: return True, "SOTT 10%02.0f arriba del techo -> long solo <=10%02.0f"%(v,lmax)
    if d=="SHORT" and v<smin: return True, "SOTT 10%02.0f abajo del piso -> short solo >=10%02.0f"%(v,smin)
    return False, ""

def sott_bias(sym, side):
    """Factor 0.3-1.5 de FAVORABILIDAD por posicion SOTT (para inclinar el TAMAÑO/probabilidad, NO bloquea):
    cerca del piso(1020) favorece LONG, cerca del techo(1080) favorece SHORT, 1050 neutro."""
    v=sott_value(sym); pos=max(0.0,min(1.0,(v-20.0)/60.0))   # 0=piso .. 1=techo
    f=(1.5-1.2*pos) if str(side).upper()=="LONG" else (0.3+1.2*pos)
    return round(max(0.3,min(1.5,f)),2)

def _mss_bias(K, lb=2):
    if not K or len(K)<20: return "NEUTRO"
    h=[x[2] for x in K]; l=[x[3] for x in K]; c=[x[4] for x in K]
    sh=set(); sl=set()
    for i in range(lb,len(h)-lb):
        if h[i]==max(h[i-lb:i+lb+1]): sh.add(i)
        if l[i]==min(l[i-lb:i+lb+1]): sl.add(i)
    last_sh=last_sl=None; b="NEUTRO"
    for i in range(lb,len(c)):
        j=i-lb
        if j in sh: last_sh=h[j]
        if j in sl: last_sl=l[j]
        if last_sl is not None and c[i]<last_sl: b="SHORT"
        elif last_sh is not None and c[i]>last_sh: b="LONG"
    return b

def btc_bias(): return _cached(("btc",), lambda: _mss_bias(bitunix.klines_smart("BTCUSDT","5m",300))) or "NEUTRO"

def _cvd_dir(K, n=60):
    s=0.0
    for x in K[-n:]:
        h=x[2];l=x[3];c=x[4];v=x[5];rng=(h-l) or 1e-9
        s+=v*(((c-l)-(h-c))/rng)
    return "LONG" if s>0 else "SHORT"

def _vol_ok(K, d, n=12):
    if not K or len(K)<40: return True
    vols=[x[5] for x in K[-30:]]; avg=sum(vols)/max(1,len(vols))
    vbuy=vsell=vsum=0.0
    for x in K[-n:]:
        h=x[2];l=x[3];c=x[4];v=x[5];rng=(h-l) or 1e-9
        vbuy+=v*((c-l)/rng); vsell+=v*((h-c)/rng); vsum+=v
    if vsum<=0: return False
    return ((vsum/n)>=avg*0.7) and ((vbuy>vsell) if d=="LONG" else (vsell>vbuy))

def _liq_bias(sym):
    def q():
        import derivs_coinalyze as D
        s=D.signal(sym) or {}
        ll=s.get("liq_long_1h") or 0; ls=s.get("liq_short_1h") or 0
        if ll>1.5*ls and ll>0: return "SHORT"
        if ls>1.5*ll and ls>0: return "LONG"
        return "NEUTRO"
    return _cached(("liq",sym), q, 120) or "NEUTRO"

def mh_latest(sym):
    def q():
        con=sqlite3.connect("file:%s?mode=ro"%AUTODB.replace("\\","/"),uri=True,timeout=3); con.row_factory=sqlite3.Row
        r=con.execute("SELECT oi_change_pct,liq_above_dist,liq_below_dist,taker_ratio,momentum FROM market_history WHERE symbol=? ORDER BY ts DESC LIMIT 1",(sym,)).fetchone()
        con.close(); return dict(r) if r else None
    return _cached(("mh",sym), q, 120)

def _oi_ok(sym, d, K):
    r=mh_latest(sym)
    if not r or r.get("oi_change_pct") is None or len(K)<10: return True
    oichg=r["oi_change_pct"]; pup=K[-1][4]>K[-8][4]
    if d=="LONG"  and pup and oichg<-0.5: return False
    if d=="SHORT" and (not pup) and oichg<-0.5: return False
    return True

def _headroom_ok(K, d, need=1.2):
    if not K or len(K)<20: return True
    h=[x[2] for x in K]; l=[x[3] for x in K]; px=K[-1][4]; lb=2
    if d=="LONG":
        res=[h[i] for i in range(lb,len(h)-lb) if h[i]==max(h[i-lb:i+lb+1]) and h[i]>px]
        return ((min(res)-px)/px*100>=need) if res else True
    sup=[l[i] for i in range(lb,len(l)-lb) if l[i]==min(l[i-lb:i+lb+1]) and l[i]<px]
    return ((px-max(sup))/px*100>=need) if sup else True

def liq_tp(sym, d):
    r=mh_latest(sym)
    if not r: return None
    dist=r.get("liq_above_dist") if d=="LONG" else r.get("liq_below_dist")
    try: dist=float(dist)
    except Exception: return None
    return round(min(2.5,max(1.0,dist)),2) if dist>0.3 else None

def confluence_block(sym, side):
    """(bloquea?, motivo). PUNTAJE: volumen a favor OBLIGATORIO; del resto (BTC+MSS+CVD+liq+OI+room) se exige
    una MAYORIA (>=confluence_min, default 3/6). Antes era AND-de-todo -> bloqueaba TODO. FAIL-OPEN."""
    d="LONG" if str(side).upper()=="LONG" else "SHORT"
    K=klines(sym)
    if not K or len(K)<50: return False, ""          # sin datos -> no bloquea
    if not _vol_ok(K,d): return True,"volumen en contra (obligatorio)"
    b=btc_bias()
    cond=[(sym=="BTCUSDT" or b=="NEUTRO" or b==d,"BTC"),
          (_mss_bias(K) in ("NEUTRO",d),"MSS"),
          (_cvd_dir(K)==d,"CVD"),
          (_liq_bias(sym) in ("NEUTRO",d),"liq"),
          (_oi_ok(sym,d,K),"OI"),
          (_headroom_ok(K,d),"room")]
    score=sum(1 for ok,_ in cond if ok)
    mn=int(_cfgval("confluence_min",3))
    if score<mn:
        return True,"confluencia %d/6 <%d (falta %s)"%(score,mn,",".join(n for ok,n in cond if not ok))
    return False,""
