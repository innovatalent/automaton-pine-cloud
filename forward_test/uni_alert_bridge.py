# -*- coding: utf-8 -*-
"""PUENTE ALERTA→TRADE (usuario 2026-09-03). scan_unified SÓLO notificaba ("no gatea estrategias"); esto convierte cada
alerta de MÁXIMA confianza (target_reach≥80, ADX≥25, no fade-tendencia-fuerte, contexto macro a favor) en un TRADE.

A/B de ENTRADA (usuario 2026-09-03: "el activo se mueve EN CONTRA, luego rebota en un iFVG y AHÍ sigue a la alerta"):
  • variante 'imm'  = entra YA al sonar la alerta (baseline).
  • variante 'ifvg' = ESPERA el gatillo iFVG (barrido en contra + inversión de FVG + reclaim, ver ifvg.py). Si el rechazo
    no aparece (precio sigue de largo) → NO entra = filtra los perdedores (que se van 2.9% en contra sin volver).
Validado con label_outcomes: ganadores mae 0.59% vs perdedores 2.92% (5×), 56% de ganadores van primero en contra.

PAPER: ambas variantes (medir cuál gana OOS). REAL: sólo la variante 'ifvg' la ESPEJA el real loop (bybit_exec._mirror_uni_alerts)
si `alert_real_on.flag` está armado. Cache/gestión: trailing+0.6/TP1 mitad/SL/timestop 5h. Se llama desde scan_unified + host."""
import os, sqlite3, time, json
import urllib.request as _ur
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "uni_alert.db")
SL_PCT = 1.5            # SL 'imm' ~1.5% más allá del extremo (la 'ifvg' usa el SL del gatillo, más ajustado)
TP_MIN = 0.5
TRAIL_ACT = 0.6
TRAIL_GB = 0.5
BE_AT = 0.5           # ★ BE-lock (usuario "si vuelve a la entrada, cortar en BE por seguridad"): tras +0.5% MFE, SL a BE+fees
BE_LOCK = 0.12        # el stop salta a +0.12% (cubre comisiones RT ~0.11%) → un ganador NO se devuelve a pérdida
FEE = 0.11
MAXB = 60              # timestop 5h (velas 5m)


def _iso():
    return datetime.now(timezone.utc).isoformat()


def _k5(sym, limit=80):
    try:
        d = json.load(_ur.urlopen(_ur.Request(
            "https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=5&limit=%d" % (sym, limit),
            headers={"User-Agent": "x"}), timeout=10))
        return [[int(x[0]), float(x[2]), float(x[3]), float(x[4])] for x in reversed(d["result"]["list"])]  # ts,h,l,c
    except Exception:
        return None


def _db():
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT, ts_open TEXT, symbol TEXT,
        side TEXT, entry REAL, sl REAL, tp REAL, conv REAL, variant TEXT DEFAULT 'imm', venue_real INTEGER DEFAULT 0,
        status TEXT, ts_close TEXT, exit_px REAL, pnl_pct REAL, mfe REAL, mae REAL, clean INTEGER, reason TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS pending(id INTEGER PRIMARY KEY AUTOINCREMENT, ts_alert TEXT, symbol TEXT,
        side TEXT, alert_px REAL, dist_target REAL, conv REAL, status TEXT DEFAULT 'WAIT')""")
    # migración: agregar 'variant' si la DB es vieja
    try:
        cols = [d[1] for d in c.execute("PRAGMA table_info(trades)")]
        if "variant" not in cols:
            c.execute("ALTER TABLE trades ADD COLUMN variant TEXT DEFAULT 'imm'")
    except Exception:
        pass
    return c


def _open_trade(con, sym, side, entry, sl, tp, conv, variant):
    if con.execute("SELECT 1 FROM trades WHERE symbol=? AND side=? AND variant=? AND status='OPEN'", (sym, side, variant)).fetchone():
        return
    con.execute("INSERT INTO trades(ts_open,symbol,side,entry,sl,tp,conv,variant,venue_real,status,mfe,mae) "
                "VALUES(?,?,?,?,?,?,?,?,0,'OPEN',0,0)", (_iso(), sym, side, entry, sl, tp, round(conv, 1), variant))


def on_alert(sym, side, r, conv):
    """Al emitirse una alerta: abre 'imm' YA (baseline) + registra un PENDING para el gatillo iFVG. NUNCA revienta."""
    try:
        entry = r.get("px") or (_k5(sym) or [[0, 0, 0, 0]])[-1][3]
        if not entry:
            return
        dist = abs(r.get("dist_target") or 0.0)
        tp_pct = max(TP_MIN, dist / 2.0)
        con = _db()
        # variante INMEDIATA (baseline, sólo paper)
        if side == "LONG":
            sl = entry * (1 - SL_PCT / 100.0); tp = entry * (1 + tp_pct / 100.0)
        else:
            sl = entry * (1 + SL_PCT / 100.0); tp = entry * (1 - tp_pct / 100.0)
        _open_trade(con, sym, side, entry, sl, tp, conv, "imm")
        # PENDING para la variante iFVG (espera barrido + rechazo). No duplicar si ya hay un WAIT del mismo símbolo+lado.
        if not con.execute("SELECT 1 FROM pending WHERE symbol=? AND side=? AND status='WAIT'", (sym, side)).fetchone():
            con.execute("INSERT INTO pending(ts_alert,symbol,side,alert_px,dist_target,conv,status) VALUES(?,?,?,?,?,?,'WAIT')",
                        (_iso(), sym, side, entry, dist, round(conv, 1)))
        con.commit(); con.close()
    except Exception:
        pass


def _process_pending(con):
    """Evalúa los PENDING con el gatillo iFVG. ENTER → abre 'ifvg'. EXPIRE → cierra el pending (no entra = filtro)."""
    try:
        import ifvg
    except Exception:
        return
    for pid, tsa, sym, side, apx, dist, conv in con.execute(
            "SELECT id,ts_alert,symbol,side,alert_px,dist_target,conv FROM pending WHERE status='WAIT'").fetchall():
        try:
            ts = datetime.fromisoformat(tsa).timestamp()
        except Exception:
            ts = time.time()
        try:
            res = ifvg.pending_trigger(sym, side, apx, ts)
        except Exception:
            continue
        stt = res.get("status")
        if stt == "ENTER":
            entry = res.get("entry") or apx; sl = res.get("sl")
            tp_pct = max(TP_MIN, (dist or 0) / 2.0)
            if side == "LONG":
                tp = entry * (1 + tp_pct / 100.0)
                if not sl:
                    sl = entry * (1 - SL_PCT / 100.0)
            else:
                tp = entry * (1 - tp_pct / 100.0)
                if not sl:
                    sl = entry * (1 + SL_PCT / 100.0)
            _open_trade(con, sym, side, entry, sl, tp, conv, "ifvg")
            con.execute("UPDATE pending SET status='ENTERED' WHERE id=?", (pid,))
        elif stt == "EXPIRE":
            con.execute("UPDATE pending SET status='EXPIRED' WHERE id=?", (pid,))


def manage():
    """Procesa PENDING (gatillo iFVG) + gestiona los PAPER abiertos (ambas variantes): trailing/TP1/SL/timestop. Host loop."""
    cl = 0
    try:
        con = _db()
        _process_pending(con)
        for tid, sym, side, entry, sl, tp, tsopen in con.execute(
                "SELECT id,symbol,side,entry,sl,tp,ts_open FROM trades WHERE status='OPEN'").fetchall():
            try:
                K = _k5(sym)
                if not K:
                    continue
                try:
                    topen = datetime.fromisoformat(tsopen).timestamp()
                except Exception:
                    topen = 0
                after = [b for b in K if b[0] / 1000.0 > topen] if topen else K[-MAXB:]
                cmfe = 0.0; cmae = 0.0; peak = 0.0; active = False; exitpx = None; reason = None; bars = 0
                for b in after:
                    bars += 1; hi, lo, c = b[1], b[2], b[3]
                    fav = ((hi - entry) / entry * 100) if side == "LONG" else ((entry - lo) / entry * 100)
                    adv = ((lo - entry) / entry * 100) if side == "LONG" else ((entry - hi) / entry * 100)
                    cmfe = max(cmfe, fav); cmae = min(cmae, adv)
                    if side == "LONG" and hi >= tp:
                        exitpx = tp; reason = "tp1"; break
                    if side == "SHORT" and lo <= tp:
                        exitpx = tp; reason = "tp1"; break
                    # BE-LOCK: tras +BE_AT de MFE, el stop salta a break-even+fees → el ganador NO puede volver a pérdida.
                    if cmfe >= BE_AT:
                        bepx = entry * (1 + BE_LOCK / 100) if side == "LONG" else entry * (1 - BE_LOCK / 100)
                        sl = max(sl, bepx) if side == "LONG" else min(sl, bepx)
                    if not active and cmfe >= TRAIL_ACT:
                        active = True
                    if active:
                        peak = max(peak, cmfe)
                        lockpx = entry * (1 + (peak - TRAIL_GB) / 100) if side == "LONG" else entry * (1 - (peak - TRAIL_GB) / 100)
                        sl = max(sl, lockpx) if side == "LONG" else min(sl, lockpx)
                    if side == "LONG" and lo <= sl:
                        exitpx = sl; reason = ("trail" if sl > entry else "sl"); break
                    if side == "SHORT" and hi >= sl:
                        exitpx = sl; reason = ("trail" if sl < entry else "sl"); break
                    if bars >= MAXB:
                        exitpx = c; reason = "timestop"; break
                if exitpx is None:
                    con.execute("UPDATE trades SET mfe=?,mae=? WHERE id=?", (round(cmfe, 3), round(cmae, 3), tid)); continue
                pnl = ((exitpx - entry) / entry * 100 if side == "LONG" else (entry - exitpx) / entry * 100) - FEE
                clean = 1 if (cmfe >= 1.0 and cmae >= -0.3) else 0
                con.execute("UPDATE trades SET status='CLOSED',ts_close=?,exit_px=?,pnl_pct=?,mfe=?,mae=?,clean=?,reason=? WHERE id=?",
                            (_iso(), exitpx, round(pnl, 3), round(cmfe, 3), round(cmae, 3), clean, reason, tid)); cl += 1
            except Exception:
                continue
        con.commit(); con.close()
    except Exception:
        pass
    return cl


def report():
    if not os.path.exists(DB):
        return ["\n**Puente ALERTA->trade:** sin DB aún."]
    con = _db()
    L = ["\n**Puente ALERTA->trade (A/B entrada · imm=inmediata vs ifvg=espera rechazo):**"]
    for variant in ("imm", "ifvg"):
        row = con.execute("SELECT COUNT(*),AVG(pnl_pct),AVG(CASE WHEN pnl_pct>0 THEN 1.0 ELSE 0 END)*100,"
                          "AVG(mae),AVG(CASE WHEN clean=1 THEN 1.0 ELSE 0 END)*100 FROM trades WHERE status='CLOSED' AND variant=?", (variant,)).fetchone()
        nop = con.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN' AND variant=?", (variant,)).fetchone()[0]
        n, e, wr, mae, cl = row
        L.append("- %-5s n=%d E=%+.3f%% WR=%.0f%% mae=%.2f limpio=%.0f%% abiertas=%d" % (variant, n or 0, e or 0, wr or 0, mae or 0, cl or 0, nop))
    pend = con.execute("SELECT status,COUNT(*) FROM pending GROUP BY status").fetchall()
    nreal = con.execute("SELECT COUNT(*) FROM trades WHERE venue_real=1").fetchone()[0]
    con.close()
    L.append("- pending: %s · reales espejadas (ifvg)=%d" % (", ".join("%s=%d" % (s, n) for s, n in pend) or "—", nreal))
    return L


if __name__ == "__main__":
    import sys
    if "manage" in sys.argv:
        print("uni_alert manage: cerró=%d" % manage())
    print("\n".join(report()))
