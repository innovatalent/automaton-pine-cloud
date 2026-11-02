# -*- coding: utf-8 -*-
"""SELECTOR DE CELDAS POR ESTRATEGIA (2026-08-07, pedido usuario: "las estrategias son muy laxas, combina lo que funciona").

Los brazos operan TODOS los activos/horarios -> muchos cierran en negativo. Este selector mide, POR BRAZO, en qué
ACTIVO × SESIÓN × MERCADO tiene ventaja estadística y en cuáles SANGRA, desde forward_trades (closed, net fees).
Escribe arm_cell_allow.json con listas allow/block. El gate `cell_blocked(arm, symbol, ts)` (que consultan bybit_exec
y forward_runner) BLOQUEA las celdas perdedoras conocidas -> el bot deja de operar donde no tiene edge.

RETROALIMENTACIÓN: se recalcula periódicamente (loop/tarea) -> si una celda deja de tener edge, se bloquea sola; si
una nueva lo gana, se habilita. Fail-open: brazo/celda sin muestra suficiente NO se bloquea (sigue explorando).
REVERSIBLE: borrar arm_cell_allow.json o poner CELL_GATE_ENABLED=False.
"""
import os
import json
import sqlite3
from datetime import datetime, timezone
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "forward_test.db")
OUT = os.path.join(HERE, "arm_cell_allow.json")

# Umbrales (net fees). BLOQUEO conservador (solo lo claramente perdedor, con muestra); ALLOW informativo/strict.
MIN_N_ASSET = 20
ASSET_BLOCK = -0.08     # activo con avg <= -0.08%/t y n>=20 -> SANGRA -> bloquear
ASSET_ALLOW = 0.12      # activo con avg >= +0.12%/t y n>=20 -> gana (para modo strict)
MIN_N_SESS = 40
SESS_BLOCK = -0.05
MIN_N_MKT = 40
MKT_BLOCK = -0.05
# VITALIDAD: activo con MFE hist promedio < VOL_MIN = DORMIDO -> 46% de sus entradas nunca van a favor (mueren en MFE~0),
# E apenas +0.07. Bloquearlo para TODOS los brazos corta las entradas fallidas + el over-trading (~44% de los trades).
VOL_MIN = 0.55
MIN_N_VOL = 25

STOCKS = set("SKHYNIX SKHY SOXL SMCI MSTR COIN META GOOGL MU AAPL SNDK TSLA AMD NVDA SQQQ INTC TQQQ AMZN AVGO HOOD "
             "TSM SPX ASML QQQ SAMSUNG MSFT SPY QCOM ORCL PLTR SNOW BABA NFLX SPCX UBER JPM CRCL DIS RIVN DKNG CVX "
             "WMT SOFI ARM MRVL NBIS EWY SOXS GS PYPL CRM ADBE V BRKB".split())
COMM = set("XAU XAG XPD XPT CL BZ GAS NATGAS COPPER OIL WTI BRENT XAUT PAXG".split())


def _mkt(sym):
    b = sym.replace("USDT", "")
    return "commodity" if b in COMM else ("stock" if b in STOCKS else "cripto")


def _sess(h):
    return "ASIA" if h < 7 else ("LONDON" if h < 12 else ("NY" if h < 20 else "OFF"))


def compute(save=True):
    con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True, timeout=8)
    rows = con.execute(
        "SELECT arm, symbol, ts_entry, pnl_net_pct, mfe_pct FROM forward_trades WHERE status='closed' "
        "AND pnl_net_pct IS NOT NULL AND arm LIKE 'strategy%' AND arm!='strategy_value'").fetchall()
    con.close()
    byA = defaultdict(list)     # (arm,symbol)
    byS = defaultdict(list)     # (arm,sess)
    byM = defaultdict(list)     # (arm,mkt)
    mfe_by = defaultdict(list)  # symbol -> mfe (vitalidad/volatilidad del activo)
    for arm, sym, ts, pnl, mfe in rows:
        try:
            h = datetime.fromisoformat(str(ts)).hour
        except Exception:
            continue
        p = float(pnl)
        byA[(arm, sym)].append(p)
        byS[(arm, _sess(h))].append(p)
        byM[(arm, _mkt(sym))].append(p)
        if mfe is not None:
            mfe_by[sym].append(float(mfe))

    def avg(v):
        return sum(v) / len(v) if v else 0.0

    # __FAILOPEN_SHORT_2026_09_27__ (iter.203): `assets_n` = n por celda (arm, symbol). SIN
    # esto el json NO distingue "celda juzgable del medio" (n>=20 y -0,08 < avg < +0,12, que
    # no aparece en NINGUNA lista) de "celda sin muestra" (n<20, que tampoco aparece). El gate
    # fail-open del SHORT necesita exactamente esa diferencia: bloquea la primera y deja pasar
    # la segunda. Clave ADITIVA: `cell_blocked` usa `.get()`, asi que ningun lector existente
    # cambia de comportamiento.
    arms = defaultdict(lambda: {"assets_block": [], "assets_allow": [], "sessions_block": [],
                                "markets_block": [], "assets_n": {}, "n": 0})
    for (arm, sym), v in byA.items():
        a = arms[arm]
        a["n"] += len(v)
        a["assets_n"][sym] = len(v)
        if len(v) >= MIN_N_ASSET:
            if avg(v) <= ASSET_BLOCK:
                a["assets_block"].append(sym)
            elif avg(v) >= ASSET_ALLOW:
                a["assets_allow"].append(sym)
    for (arm, s), v in byS.items():
        if len(v) >= MIN_N_SESS and avg(v) <= SESS_BLOCK:
            arms[arm]["sessions_block"].append(s)
    for (arm, m), v in byM.items():
        if len(v) >= MIN_N_MKT and avg(v) <= MKT_BLOCK:
            arms[arm]["markets_block"].append(m)

    # DORMANT = activos sin vitalidad (MFE hist < VOL_MIN) -> sus entradas mueren -> bloquear para todos los brazos.
    dormant = sorted(s for s, v in mfe_by.items() if len(v) >= MIN_N_VOL and (sum(v) / len(v)) < VOL_MIN)
    out = {"ts": datetime.now(timezone.utc).isoformat(), "dormant": dormant, "arms": {k: v for k, v in arms.items()}}
    if save:
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        os.replace(tmp, OUT)
    return out


# ---------------- GATE (lo consultan bybit_exec y forward_runner) ----------------
CELL_GATE_ENABLED = True
_cache = {"data": None, "ts": 0.0}
_REFRESH = 600.0


def _load():
    import time as _t
    now = _t.time()
    if _cache["data"] is not None and now - _cache["ts"] < _REFRESH:
        return _cache["data"]
    try:
        with open(OUT, encoding="utf-8") as f:
            _cache["data"] = json.load(f)
    except Exception:
        _cache["data"] = {}
    _cache["ts"] = now
    return _cache["data"]


def cell_blocked(arm, symbol, ts=None):
    """True si (arm, symbol, sesión-de-ts) es una celda PERDEDORA/MUERTA conocida -> NO operar. Fail-open (sin datos)."""
    if not CELL_GATE_ENABLED:
        return False, ""
    d = _load()
    if symbol in (d.get("dormant") or []):
        return True, "activo dormido (sin volatilidad, las entradas mueren MFE~0)"
    a = (d.get("arms") or {}).get(arm)
    if not a:
        return False, ""                                   # brazo sin mapa -> explora libre
    if symbol in (a.get("assets_block") or []):
        return True, "activo sangra (%s)" % symbol
    if _mkt(symbol) in (a.get("markets_block") or []):
        return True, "mercado sin edge (%s)" % _mkt(symbol)
    try:
        h = datetime.fromisoformat(str(ts)).hour if ts else datetime.now(timezone.utc).hour
    except Exception:
        h = datetime.now(timezone.utc).hour
    if _sess(h) in (a.get("sessions_block") or []):
        return True, "sesión sin edge (%s)" % _sess(h)
    return False, ""


# ---------------- __FAILOPEN_SHORT_2026_09_27__ (iter.203, lente EL LADO CORTO) ----------
# `GATE_FAILOPEN_SHORT`: el gate de celda fail-open aplicado **SOLO AL SHORT**. Pre-registrado
# como titular UNICO en `PREREG_failopen_short_20260927.md`, medido en
# `bt_failopen_short_20260927.py`, veredicto **PAPEL** con las 5 condiciones:
#     E1 replica  · E2 Pareto en K=3/4/5  · E3 Pareto en la reserva VIRGEN (>= 2026-09-23)
#     E4 placebo del Pareto p_conj 0,0040/0,0000/0,0000 en 3 semillas x 500 sorteos
#     E5 le gana al azar del MISMO LADO (+0,395051 contra +0,115853 del SHORT `random*`)
# Numero: con el cupo K=4 del motor, E del libro +0,300608 -> +0,323970 y suma +250,4062 ->
# +256,2599 (+2,3 %), n 833 -> 791.
#
# **VA APAGADO.** DSR 0,0 % contra la vara +0,978465, asi que MISION §2.2 lo manda a PAPEL y
# NO a real. Prenderlo es decision HUMANA. Ademas, mientras `arm_cell_allow.json` no tenga
# `assets_n` (se escribe en el proximo `compute()`), la funcion **deja pasar todo** aun
# prendida: fail-open sobre el propio fail-open, para que el orden de los recalculos no pueda
# cambiar el comportamiento por accidente.
CELL_FAILOPEN_SHORT_ENABLED = False


def cell_short_failopen_ok(arm, symbol):
    """(ok, motivo) del titular de iter.203. Solo el LLAMADOR decide el lado; aca no se mira.

    Pasa si: el gate esta apagado, el brazo no es `strategy*` elegible, el brazo no tiene mapa,
    el json todavia no trae `assets_n`, la celda NO es juzgable (n < MIN_N_ASSET) o el activo
    esta en `assets_allow`. Bloquea SOLO la celda juzgable que no llega a ASSET_ALLOW, que es
    exactamente el contraste medido (n_si=126 contra 118 juzgables SHORT no admitidas).
    """
    if not CELL_FAILOPEN_SHORT_ENABLED:
        return True, ""
    if not (str(arm).startswith("strategy") and arm != "strategy_value"):
        return True, ""                                    # donde el selector no corre
    a = (_load().get("arms") or {}).get(arm)
    if not a:
        return True, ""                                    # brazo sin mapa -> explora libre
    an = a.get("assets_n")
    if not an:
        return True, ""                                    # json viejo -> fail-open duro
    if int(an.get(symbol, 0)) < MIN_N_ASSET:
        return True, ""                                    # celda no juzgable -> fail-open
    if symbol in (a.get("assets_allow") or []):
        return True, ""
    return False, "celda juzgable sin edge SHORT (n>=%d, avg < %+.2f)" % (MIN_N_ASSET,
                                                                         ASSET_ALLOW)


if __name__ == "__main__":
    d = compute(save=True)
    arms = d["arms"]
    nb = sum(len(v["assets_block"]) for v in arms.values())
    print("arm_cell_allow.json escrito. brazos=%d  activos bloqueados=%d" % (len(arms), nb))
    for arm, v in sorted(arms.items()):
        if v["assets_block"] or v["sessions_block"] or v["markets_block"]:
            print("  %-16s BLOCK activos=%s ses=%s mkt=%s" % (
                arm, [s.replace("USDT", "") for s in v["assets_block"]][:8],
                v["sessions_block"], v["markets_block"]))
