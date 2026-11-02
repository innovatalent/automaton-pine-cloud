# -*- coding: utf-8 -*-
"""MOTOR DE FLUJO / EVOLUCIÓN DEL SETUP (pedido usuario 2026-09-03: "funciona como un flujo, un proceso de varios días u
horas que va EVOLUCIONANDO"). El setup NO existe en una vela: nace en la TF alta (1d), madura y cascadea hacia abajo
(1h→15m→5m) en el tiempo, y la entrada es la culminación. Este módulo le da MEMORIA: registra CUÁNDO cada TF entra al
extremo (con timestamp) para cada activo, y arma la línea de tiempo del setup. El hilo de fondo del dashboard lo llama
cada ~3min con las zonas z+SOTT por TF de cada fila. Estado persistente en setup_state.json. NO opera, solo trackea."""
import os, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "setup_state.json")
TF_ORDER = ["1d", "1h", "15m", "5m"]     # de ALTA (dirección) a BAJA (gatillo) — el orden en que cascadea
EXPIRE_S = 8 * 3600                       # un setup sin actividad 8h se reinicia (el flujo se enfrió)


def _load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(st):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def _dominant(z_tf, sott_tf):
    """Dirección dominante del setup: suma de zonas z+SOTT en las 4 TFs. Más de un lado = esa dirección."""
    buy = sum(1 for d in (z_tf, sott_tf) for tf in TF_ORDER if d.get(tf) == "compra")
    sell = sum(1 for d in (z_tf, sott_tf) for tf in TF_ORDER if d.get(tf) == "venta")
    if buy > sell and buy >= 1:
        return "LONG"
    if sell > buy and sell >= 1:
        return "SHORT"
    return None


def update_all(rows, now):
    """rows = filas del panel (symbol, z_tf, sott_tf, ok_dir, hist_rising). Actualiza el estado de flujo de cada setup
    y ADJUNTA r['setup'] = descripción de la evolución. Detecta cuándo cada TF entra al extremo (la dimensión temporal)."""
    st = _load()
    seen = set()
    for r in rows:
        sym = r.get("symbol"); z = r.get("z_tf") or {}; so = r.get("sott_tf") or {}
        if not sym:
            continue
        seen.add(sym)
        dirn = _dominant(z, so)
        cur = st.get(sym)
        if dirn is None:
            # sin dirección dominante ahora: si había un setup vivo, lo mantenemos envejeciendo; si no, nada.
            if cur and (now - cur.get("last_active", 0)) < EXPIRE_S:
                r["setup"] = _describe(cur, now)
            continue
        want = "compra" if dirn == "LONG" else "venta"
        # reiniciar si cambió de dirección o expiró (el flujo anterior se enfrió)
        if not cur or cur.get("dir") != dirn or (now - cur.get("last_active", 0)) > EXPIRE_S:
            cur = {"dir": dirn, "born": now, "stages": {}, "last_active": now, "entered": False, "entered_ts": None}
        # REGISTRAR la transición: primera vez que cada TF entra al extremo del lado del setup (z O SOTT) -> timestamp
        for tf in TF_ORDER:
            if (z.get(tf) == want) or (so.get(tf) == want):
                if tf not in cur["stages"]:
                    cur["stages"][tf] = now
        cur["last_active"] = now
        # ENTRADA (culminación): el giro del flujo (OK direccional) a favor + Hist confirmando
        okd = r.get("ok_dir"); hr = r.get("hist_rising")
        if (dirn == "LONG" and okd == "compra" and hr) or (dirn == "SHORT" and okd == "venta" and hr is False):
            if not cur.get("entered"):
                cur["entered"] = True; cur["entered_ts"] = now
        cur["maturity"] = len(cur["stages"])
        st[sym] = cur
        r["setup"] = _describe(cur, now)
    # PRUNE: setups no vistos y expirados
    for sym in list(st.keys()):
        if sym not in seen and (now - st[sym].get("last_active", 0)) > EXPIRE_S:
            del st[sym]
    _save(st)


def _ago(ts, now):
    d = max(0, now - ts)
    if d < 3600:
        return "%dm" % int(d / 60)
    if d < 86400:
        return "%.0fh" % (d / 3600)
    return "%.1fd" % (d / 86400)


def _describe(setup, now):
    """Descripción de la EVOLUCIÓN para el panel: edad, madurez, qué TFs ya entraron (con hace-cuánto) y qué falta."""
    stages = setup.get("stages", {})
    done = [tf for tf in TF_ORDER if tf in stages]
    pend = [tf for tf in TF_ORDER if tf not in stages]
    return {
        "dir": setup["dir"],
        "maturity": len(done),                       # 0..4 (cuántas TFs cascadearon)
        "entered": bool(setup.get("entered")),
        "age": _ago(setup.get("born", now), now),    # edad del setup (días/horas)
        "done": done,                                # TFs que ya entraron (de alta a baja)
        "pend": pend,                                # TFs que faltan
        "timeline": " → ".join("%s %s" % (tf, _ago(stages[tf], now)) for tf in done),
        "entered_age": (_ago(setup["entered_ts"], now) if setup.get("entered_ts") else None),
    }
