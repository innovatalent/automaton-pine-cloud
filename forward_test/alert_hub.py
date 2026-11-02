# -*- coding: utf-8 -*-
"""ALERT HUB — UNA sola puerta para voz + Telegram (pedido usuario 2026-09-04: "unificá las alertas por voz y por
telegram para que solo me avise las de MÁXIMA confianza que tengan máxima probabilidad de éxito según la estadística").

Cómo funciona (sin IA, puro conteo):
  · Cada emisor (sott_combo_alert, vp_engine, etc.) pasa por `allowed(prefix, side)` ANTES de hablar/mandar.
  · La regla vive en `alert_rules.json` y la RECONSTRUYE `rebuild()` (diario o a mano) leyendo `alert_outcomes.db`
    (cada alerta medida a 3-4h: MFE/MAE/net). Celda = (prefijo de alerta, lado). Se habilita una celda si:
        n >= MIN_N  y  net3h > 0  y  clean2 >= umbral      (clean2 = llegó a +2% con MAE <= 1%)
    umbral = 50% si alguna celda lo alcanza (la META del usuario); si ninguna, las TOP_K mejores con clean2 >= MIN_CLEAN2.
  · Lo silenciado NO se pierde: se escribe en `zscore_alerts_silent.json` (misma forma que la cola de voz) y
    alert_tracker lo ingesta igual → la estadística sigue aprendiendo de TODAS las alertas, hable o no.
  · `VP_MAXIMA` (el motor vp_engine, celda validada) SIEMPRE pasa.

Uso: `python alert_hub.py` = reconstruye reglas + imprime tabla.  `python alert_hub.py --show` = solo tabla.
Reversible: borrar alert_rules.json = comportamiento "todo pasa" (fail-open, no muda el sistema por accidente)."""
import os, json, time, sqlite3, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RULES = os.path.join(HERE, "alert_rules.json")
OUTC = os.path.join(HERE, "alert_outcomes.db")
SILENT = os.path.join(ROOT, "direction_engine", "zscore_alerts_silent.json")
LOG = os.path.join(ROOT, "logs", "alert_hub.log")

GOAL_CLEAN2 = 50.0     # META: 50%+ de llegada limpia al +2% (pedido usuario)
MIN_CLEAN2 = 25.0      # piso provisorio mientras ninguna celda llega a la meta
MIN_N = 30             # muestra mínima por celda (anti-ruido)
TOP_K = 3              # si nadie llega a la meta: solo las 3 mejores celdas
ALWAYS = {"vpmax"}     # prefijos que siempre pasan (motor VP, celda ya validada por su propia regla)
PRIORITY_ASSETS = {"TQQQUSDT"}
ONLY_REAL_OPS = True   # ★ usuario 2026-09-04 03:10: "que me lleguen SOLO cuando realmente opera (voz + Telegram)". Con True, pasan
                       #   ÚNICAMENTE los prefijos ALWAYS (vpmax = motor VP al ABRIR/CERRAR una operación REAL). Todo lo demás se
                       #   silencia (sigue midiéndose en alert_outcomes). Volver al modo estadístico: ONLY_REAL_OPS = False.   # ★ activos PRIORITARIOS (usuario 2026-09-04 "en las alertas de Telegram dale prioridad a TQQQ"): pasan SIEMPRE
_rules_cache = {"ts": 0.0, "data": None}


def _log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def load_json_any(path, default):
    """Lectura tolerante (utf-8 / cp1252): la cola de voz la escriben scripts Windows con encoding por defecto."""
    try:
        b = open(path, "rb").read()
    except Exception:
        return default
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return json.loads(b.decode(enc))
        except Exception:
            continue
    return default


def prefix_of(alert_id):
    """'sottmom-BTCUSDT-SHORT-171...' -> 'sottmom' (misma convención que alert_tracker/ids de la cola de voz)."""
    return re.split(r"[-_]", str(alert_id or ""))[0].lower()


def rules():
    now = time.time()
    if _rules_cache["data"] is not None and now - _rules_cache["ts"] < 60:
        return _rules_cache["data"]
    d = None
    try:
        if os.path.exists(RULES):
            d = json.load(open(RULES, encoding="utf-8"))
    except Exception:
        d = None
    _rules_cache.update(ts=now, data=d)
    return d


def allowed(prefix, side=None):
    """True si la celda (prefijo, lado) está habilitada por la estadística. Sin reglas = fail-open (todo pasa)."""
    p = str(prefix or "").lower()
    if p in ALWAYS:
        return True
    if ONLY_REAL_OPS:
        return False
    r = rules()
    if not r:
        return True
    cells = r.get("allowed") or []
    for c in cells:
        if c.get("prefix") == p and (c.get("side") in (None, "*", side)):
            return True
    return False


def gate(alert_id, asset, side, text=None):
    """Puerta única: devuelve True si puede hablar/mandar. Si NO, registra la alerta en la cola SILENCIOSA (para que
    alert_tracker la mida igual) y devuelve False. Nunca revienta."""
    p = prefix_of(alert_id)
    if str(asset or "").upper() in PRIORITY_ASSETS and not ONLY_REAL_OPS:
        _log("PRIORIDAD %s %s %s (activo prioritario: pasa siempre)" % (alert_id, asset, side))
        return True
    if allowed(p, side):
        return True
    try:
        al = load_json_any(SILENT, [])
        al.append({"id": alert_id, "asset": asset, "side": side, "text": text or "", "ts": int(time.time()), "silenced": 1})
        al = al[-200:]
        json.dump(al, open(SILENT, "w"), ensure_ascii=False)
    except Exception:
        pass
    _log("SILENCIADA %s %s %s (%s)" % (alert_id, asset, side, "solo operaciones REALES del motor" if ONLY_REAL_OPS else "celda no validada"))
    return False


def cell_stats(days=None):
    """Estadística por celda (prefijo, lado) desde alert_outcomes.db. clean2 = mfe>=2 y mae<=1."""
    if not os.path.exists(OUTC):
        return []
    try:
        c = sqlite3.connect("file:%s?mode=ro" % OUTC.replace("\\", "/"), uri=True)
        q = "SELECT id,side,mfe,mae,net3h FROM alerts WHERE done=1"
        if days:
            q += " AND ts >= %d" % int(time.time() - days * 86400)
        rows = c.execute(q).fetchall(); c.close()
    except Exception as e:
        _log("cell_stats err %r" % e); return []
    g = {}
    for aid, side, mfe, mae, net in rows:
        k = (prefix_of(aid), side or "?")
        g.setdefault(k, []).append((mfe or 0.0, abs(mae or 0.0), net or 0.0))
    out = []
    for (p, s), v in g.items():
        n = len(v)
        out.append({"prefix": p, "side": s, "n": n,
                    "hit1": round(100.0 * sum(1 for x in v if x[0] >= 1) / n, 1),
                    "hit2": round(100.0 * sum(1 for x in v if x[0] >= 2) / n, 1),
                    "clean2": round(100.0 * sum(1 for x in v if x[0] >= 2 and x[1] <= 1.0) / n, 1),
                    "net3h": round(sum(x[2] for x in v) / n, 3)})
    out.sort(key=lambda r: (-r["clean2"], -r["net3h"]))
    return out


def rebuild(write=True):
    st = cell_stats()
    elig = [r for r in st if r["n"] >= MIN_N and r["net3h"] > 0]
    goal = [r for r in elig if r["clean2"] >= GOAL_CLEAN2]
    if goal:
        chosen = goal; mode = "META 50%% alcanzada (%d celdas)" % len(goal)
    else:
        chosen = [r for r in elig if r["clean2"] >= MIN_CLEAN2][:TOP_K]
        mode = "sin celda al 50%%: TOP-%d con clean2>=%.0f%%" % (TOP_K, MIN_CLEAN2)
    data = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode, "goal_clean2": GOAL_CLEAN2,
            "min_n": MIN_N, "allowed": [{"prefix": r["prefix"], "side": r["side"], "n": r["n"], "clean2": r["clean2"],
                                         "net3h": r["net3h"]} for r in chosen],
            "always": sorted(ALWAYS), "priority_assets": sorted(PRIORITY_ASSETS), "only_real_ops": ONLY_REAL_OPS, "all_cells": st}
    if write:
        try:
            json.dump(data, open(RULES, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            _rules_cache["ts"] = 0.0
        except Exception as e:
            _log("rebuild write err %r" % e)
    return data


def report():
    st = cell_stats(); r = rules() or {}
    al = {(c["prefix"], c["side"]) for c in (r.get("allowed") or [])}
    out = ["=== ALERT HUB · celdas (alert_outcomes.db, medidas a 3-4h) · META clean2>=%.0f%% ===" % GOAL_CLEAN2,
           "%-12s %-6s %4s %5s %5s %6s %6s  estado" % ("prefijo", "lado", "n", "hit1", "hit2", "clean2", "net3h")]
    for c in st:
        ok = (c["prefix"], c["side"]) in al
        out.append("%-12s %-6s %4d %5.0f %5.0f %6.0f %+6.2f  %s" % (c["prefix"], c["side"], c["n"], c["hit1"], c["hit2"],
                                                                  c["clean2"], c["net3h"], "✅ HABLA" if ok else "🔇"))
    out.append("regla: %s (%s)" % (r.get("mode", "sin reglas = todo pasa"), r.get("generated", "-")))
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if "--show" not in sys.argv:
        d = rebuild(); print("reglas reconstruidas:", d["mode"])
    print(report())
