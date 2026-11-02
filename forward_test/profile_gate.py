# -*- coding: utf-8 -*-
"""GATE DE PERFIL (2026-08-10): convierte lo APRENDIDO por asset_profiler (asset_profiles.json) en
OPERATIVA. Por celda activo|sesion: NO operar las que SANGRAN (E<0 con n suficiente) y usar el TP
aprendido (tp_sug) de las que ganan. Cache TTL, fail-open (sin perfil -> no bloquea, TP default)."""
import os, json, time
HERE=os.path.dirname(os.path.abspath(__file__))
PROF=os.path.join(HERE,"asset_profiles.json")
MIN_N=15
_c=[0.0, {}]

def _load():
    t=time.time()
    if t-_c[0]<120 and _c[1]: return _c[1]
    try:
        with open(PROF) as f: _c[1]=json.load(f)
    except Exception: _c[1]={}
    _c[0]=t; return _c[1]

def session_now():
    h=time.gmtime().tm_hour
    return "ASIA" if h<7 else ("LONDON" if h<12 else ("NY" if h<20 else "OFF"))

def cell_block(symbol, session=None):
    """(bloquea?, tp_sug). Bloquea si la celda tiene E<0 con n>=MIN_N (sangra). tp_sug = TP aprendido."""
    if session is None: session=session_now()
    p=_load().get("%s|%s"%(symbol, session))
    if not p: return False, None
    if p.get("n",0)>=MIN_N and (p.get("E") or 0)<0: return True, None
    return False, p.get("tp_sug")

def is_target1(symbol, session=None):
    """True si la celda tiene +1% SISTEMATIZADO (>=1% en train Y test = confiable, no in-sample)."""
    if session is None: session=session_now()
    p=_load().get("%s|%s"%(symbol, session))
    return bool(p and p.get("robust_1pct"))

def target1_cells():
    """Lista de celdas +1% confirmadas (para whitelist / sizing agresivo / modo conservador)."""
    return sorted(k for k,v in _load().items() if v.get("robust_1pct"))

def real_eligible(symbol, session=None):
    """True si la celda GRADUO a REAL: robusta OOS +1% Y confirmada EN VIVO (post-gates, n>=MIN_LIVE, E>0).
    El ejecutor real solo deberia operar estas -> 'operar real con lo que YA funciona en vivo'."""
    if session is None: session=session_now()
    p=_load().get("%s|%s"%(symbol, session))
    return bool(p and p.get("real_eligible"))

def real_cells():
    return sorted(k for k,v in _load().items() if v.get("real_eligible"))
