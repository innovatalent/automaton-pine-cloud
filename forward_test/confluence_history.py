# -*- coding: utf-8 -*-
"""REGISTRO HISTÓRICO de la CONFLUENCIA SOTT/z multi-TF por activo (pedido usuario 2026-09-03: "guardar el estado de las
horas previas de confluencias SOTT y z-score, porque los que ayer tenían confluencia hoy dieron el movimiento = anticipa").
Lo alimenta el refresher del dashboard c/refresco; deduplica a ~1/hora (o cuando CAMBIA la dirección de TFs altas) para no
inflar. `anticipation(asset, htf_side)` devuelve hace cuánto empezó esa confluencia y cuánto se movió el precio desde entonces
(a favor) = la evidencia de anticipación EN VIVO por activo. sqlite propio (confluence_history.db)."""
import os, sqlite3, time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "confluence_history.db")
_MIN_GAP = 3300.0     # ~55 min: cadencia horaria (además siempre se registra si CAMBIA htf_side)


def _db():
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS conf(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, asset TEXT, price REAL,
        sott_dir TEXT, sott_agree INTEGER, htf_side TEXT, htf_agree INTEGER, z_dir TEXT, z_agree INTEGER, z_against INTEGER,
        modo_reco TEXT, dir_reco TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_conf_at ON conf(asset, ts)")
    return c


def log_snapshot(rows, now):
    """Graba un snapshot por activo si pasó >=~1h del último O cambió la dirección de TFs altas (htf_side)."""
    c = _db(); n = 0
    for r in rows:
        a = r.get("symbol")
        if not a:
            continue
        htf_side = r.get("htf_side")
        last = c.execute("SELECT ts,htf_side FROM conf WHERE asset=? ORDER BY ts DESC LIMIT 1", (a,)).fetchone()
        changed = (last is None) or (last[1] != htf_side) or ((now - last[0]) >= _MIN_GAP)
        if not changed:
            continue
        c.execute("INSERT INTO conf(ts,asset,price,sott_dir,sott_agree,htf_side,htf_agree,z_dir,z_agree,z_against,modo_reco,dir_reco) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (int(now), a, r.get("px"), r.get("sott_dir"), r.get("sott_agree"), htf_side, r.get("htf_agree"),
                   r.get("z_dir"), r.get("z_agree"), r.get("z_against"), r.get("modo_reco"), r.get("dir_reco")))
        n += 1
    c.commit(); c.close()
    return n


def anticipation(asset, htf_side, now, price_now):
    """¿Hace cuánto viene esta confluencia de TFs altas (htf_side) y cuánto se movió el precio desde que EMPEZÓ, a favor?
    Devuelve {since_h, move_dir} o None. Busca hacia atrás el registro consecutivo más viejo con el MISMO htf_side."""
    if not htf_side or price_now is None:
        return None
    c = _db()
    hist = c.execute("SELECT ts,price,htf_side FROM conf WHERE asset=? ORDER BY ts DESC LIMIT 400", (asset,)).fetchall()
    c.close()
    if not hist:
        return None
    start_ts = None; start_px = None
    for ts, px, side in hist:                 # de más nuevo a más viejo: caminar mientras siga el mismo lado
        if side == htf_side:
            start_ts = ts; start_px = px
        else:
            break
    if start_ts is None or not start_px:
        return None
    since_h = (now - start_ts) / 3600.0
    mv = (price_now - start_px) / start_px * 100.0
    move_dir = mv if htf_side == "LONG" else -mv
    return {"since_h": round(since_h, 1), "move_dir": round(move_dir, 2), "start_px": start_px}


def primed(asset, now, hours=24, min_agree=3):
    """¿El activo estuvo 'cargado' de confluencia SOTT fuerte (≥min_agree TFs) en las últimas `hours`? = candidato a
    VIGILANCIA ESPECIAL (usuario 2026-09-03: los que tuvieron 3+ confluencias en 24h esperar su gatillo+liquidez).
    Devuelve {count: cuántos snapshots con agree≥min, max: máxima confluencia vista, side: lado dominante}."""
    c = _db()
    cut = int(now - hours * 3600)
    rows = c.execute("SELECT htf_agree,sott_agree,htf_side FROM conf WHERE asset=? AND ts>=?", (asset, cut)).fetchall()
    c.close()
    if not rows:
        return {"count": 0, "max": 0, "side": None}
    cnt = sum(1 for h, s, _ in rows if (h or 0) >= min_agree or (s or 0) >= min_agree)
    mx = max(max((h or 0), (s or 0)) for h, s, _ in rows)
    sides = [sd for h, s, sd in rows if sd and ((h or 0) >= min_agree or (s or 0) >= min_agree)]
    side = max(set(sides), key=sides.count) if sides else None
    return {"count": cnt, "max": mx, "side": side}


def purge(days=14):
    """Limpia registros más viejos que `days` (mantiene el histórico acotado)."""
    c = _db()
    try:
        cut = int(time.time() - days * 86400)
        c.execute("DELETE FROM conf WHERE ts < ?", (cut,))
        c.commit()
    except Exception:
        pass
    c.close()
