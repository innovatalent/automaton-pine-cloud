# -*- coding: utf-8 -*-
"""
gex_recorder.py — graba los niveles de gamma para poder validarlos despues.

POR QUE SOLO NIVELES Y NO PRECIOS
    Los NIVELES son perecederos: nadie publica el open interest por strike de ayer,
    asi que si no se graban hoy se pierden para siempre. Es exactamente el limite
    que impidio reconstruir el GEX historico.

    Los PRECIOS no son perecederos: las velas se bajan cuando uno quiera. Asi que el
    grabador guarda SOLO la estructura, y el estudio (gex_levels_study.py) reconstruye
    la accion del precio a posteriori. Eso hace el archivo chico y el estudio flexible:
    se puede cambiar la definicion de "rebote" sin volver a grabar nada.

QUE SE GRABA
    Cada snapshot: spot, regimen, estado, GEX neto, concentracion, gamma flip, call
    wall, put wall, strike de mayor |GEX|, vencimiento proximo y su peso, cobertura
    del dato, y la ESCALERA de los strikes con mas |GEX| (que son los niveles
    candidatos a actuar como soporte o resistencia).

ACTIVOS
    BTC y ETH (Deribit+OKX+Bybit, cobertura dominante), QQQ y TQQQ (Nasdaq), GLD
    (oro, cobertura parcial) y USO (petroleo, cobertura debil). La etiqueta de
    cobertura se graba con cada fila para poder separar despues: un resultado que
    solo aparece en la cobertura debil no es un resultado.

USO
    python -X utf8 gex_recorder.py           # una toma
    python -X utf8 gex_recorder.py --loop    # daemon, una toma cada 10 min
    python -X utf8 gex_recorder.py --stats   # que hay grabado
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DB = os.path.join(HERE, "gex_levels.db")
INTERVALO_S = 600          # el OI se publica pocas veces por dia; 10 min sobra
TOP_LADDER = 12            # cuantos strikes por snapshot (los de mayor |GEX|)

CRIPTO = ("BTC", "ETH")
EQUITY = (("QQQ", "nasdaq"), ("TQQQ", "nasdaq"), ("GLD", "oro"), ("USO", "petroleo"))


def _db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS snaps(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL, iso TEXT, asset TEXT, clase TEXT, cobertura TEXT,
        spot REAL, regimen TEXT, estado TEXT, net_gex_musd REAL, conc REAL,
        flip REAL, dist_flip_pct REAL, call_wall REAL, put_wall REAL, max_abs REAL,
        vto_h REAL, vto_share REAL, n_contratos INTEGER,
        vex_musd REAL, cex_musd REAL,
        -- __GRIEGAS_2026_09_13__ los NIVELES de las otras griegas. Son tan
        -- perecederos como el resto: sin iv y vencimiento por contrato no se
        -- pueden reconstruir despues, asi que si no se graban hoy se pierden.
        vanna_wall REAL, vanna_flip REAL, charm_wall REAL,
        delta_wall REAL, delta_zero REAL, vol_trigger REAL, gamma_max REAL,
        -- __CONVENCION_GRIEGAS_2026_09_20__ — la CONVENCION viaja con la fila.
        -- `cex_musd` sale de exposiciones(dte_ramp_days=1.0) y `charm_wall` de
        -- gex_griegas.escaleras, que NO rampea: son dos convenciones en la MISMA
        -- fila y hasta hoy nada lo decia. Medido en vivo sobre 6 activos: la
        -- rampa mueve el |CEX| 1,33 % (BTC) y 1,76 % (ETH), 0,00 % donde no hay
        -- contratos bajo 1 dia, y NUNCA le cambia el signo (42 de 42 negativos
        -- en 7 ventanas de vencimiento). Es chico, pero es PERECEDERO: sin la
        -- cadena guardada no se puede recalcular despues, igual que las griegas.
        cex_musd_ramp0 REAL, cex_ramp_days REAL, cex_dte_max REAL,
        -- __IV_2026_09_13__ la VOLATILIDAD IMPLICITA de la cadena. Sin esto no se
        -- puede probar la hipotesis de vanna, que dice que el nivel SOLO actua
        -- cuando la IV se mueve: hace falta el nivel de IV de cada snapshot para
        -- derivar despues su variacion. Es el dato que faltaba para combinar.
        iv_media REAL, iv_atm REAL,
        -- perfil de volumen de 24h del MISMO instante: sin esto no se puede
        -- medir despues si la confluencia griega+perfil aporta algo.
        poc REAL, vah REAL, val REAL, vwap REAL,
        -- NULL on legacy rows: no historical relabeling or recomputation.
        expiry_share_convention TEXT, expiry_share_version INTEGER,
        expiry_share_ramp_days REAL, captured_at REAL, recorded_at REAL)""")
    for _col, _tipo in (("vanna_wall", "REAL"), ("vanna_flip", "REAL"),
                        ("charm_wall", "REAL"), ("delta_wall", "REAL"),
                        ("delta_zero", "REAL"), ("vol_trigger", "REAL"),
                        ("gamma_max", "REAL"), ("poc", "REAL"), ("vah", "REAL"),
                        ("val", "REAL"), ("vwap", "REAL"),
                        ("iv_media", "REAL"), ("iv_atm", "REAL"),
                        # __CONVENCION_GRIEGAS_2026_09_20__
                        ("cex_musd_ramp0", "REAL"), ("cex_ramp_days", "REAL"),
                        ("cex_dte_max", "REAL"),
                        ("expiry_share_convention", "TEXT"),
                        ("expiry_share_version", "INTEGER"),
                        ("expiry_share_ramp_days", "REAL"),
                        ("captured_at", "REAL"), ("recorded_at", "REAL")):
        try:                       # base vieja: agregar sin perder lo grabado
            c.execute("ALTER TABLE snaps ADD COLUMN %s %s" % (_col, _tipo))
        except Exception:
            pass
    c.execute("""CREATE TABLE IF NOT EXISTS ladder(
        snap_id INTEGER, strike REAL, net_gex_musd REAL, abs_gex_musd REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_snap_asset ON snaps(asset, ts)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_ladder ON ladder(snap_id)")
    return c


# Solo los activos cuyo perfil de volumen esta en LA MISMA ESCALA DE PRECIO que
# sus niveles de griegas. GLD y USO quedan afuera a proposito: sus niveles salen
# de la cadena de GLD/USO (398, 155) pero el perfil liquido esta en XAU/CL (4356,
# 97). Guardar ese perfil haria que la confluencia NUNCA coincida y, peor, que un
# estudio futuro compare escalas distintas sin darse cuenta.
_PERFIL_SYM = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "QQQ": "QQQUSDT",
               "TQQQ": "TQQQUSDT"}


def _sym_perfil(asset):
    """El simbolo del perfil, o None si no hay uno en la misma escala."""
    return _PERFIL_SYM.get(str(asset).upper())


def _guardar(c, asset, clase, cobertura, st):
    # __VEX_CEX_2026_09_08__: las tres exposiciones, para poder probar despues si un
    # nivel donde GEX, VEX y CEX apuntan IGUAL aguanta mas que uno con GEX solo.
    _e3 = {}
    try:
        from gex_engine import exposiciones
        _r = getattr(st, "_rows", None) or []
        # __EXP3_VISTA_2026_09_20__ — la rampa sale del PROPIO estado, no de un
        # literal. Hoy `tomar()` solo graba estados scalp/equity (rampa 1.0), asi
        # que el 1.0 cableado era correcto POR COINCIDENCIA; asi lo es por
        # construccion, y `cex_ramp_days` deja de poder mentir.
        import gex_live as _gl_ramp
        _ramp = _gl_ramp._ramp_de(st)
        _x = exposiciones(_r, st.spot, dte_ramp_days=_ramp)
        _e3 = {"vex": round(_x["vex"] / 1e6, 4), "cex": round(_x["cex"] / 1e6, 4)}
        # __CONVENCION_GRIEGAS_2026_09_20__ — el CEX SIN RAMPA, que es el
        # primitivo declarado, y los dos parametros que lo definen. Se guarda
        # aparte y con nombre propio: NUNCA se los cuenta como dos griegas que
        # se confirman entre si, son la misma familia de contratos con dos pesos.
        _x0 = exposiciones(_r, st.spot, dte_ramp_days=0.0)
        _e3["cex_ramp0"] = round(_x0["cex"] / 1e6, 4)
        _e3["ramp_days"] = _ramp          # __EXP3_VISTA_2026_09_20__
        _e3["dte_max"] = getattr(st, "dte_max", None)
    except Exception:
        pass
    # __GRIEGAS_2026_09_13__ — los niveles del resto de las griegas.
    _gr = {}
    try:
        import gex_griegas as _gg
        _gr = _gg.niveles_de_estado(st) or {}
    except Exception:
        pass

    # Perfil de volumen de 24h del mismo instante. Se graba PARA PODER MEDIR
    # DESPUES si la confluencia (griega sobre POC/VAH/VAL) aporta algo por encima
    # de la griega sola. Sin esto la pregunta no se puede contestar nunca.
    _pf = {}
    _sp = _sym_perfil(asset)
    if _sp:
        try:
            import vwap_profile as _vp
            _pf = _vp.profile(_sp, "5m") or {}
        except Exception:
            pass

    # IV de la cadena: media ponderada por open interest, y la de los strikes
    # pegados al spot (+-5 %), que es la que gobierna la cobertura de corto plazo.
    _ivm = _ivatm = None
    try:
        _rr = getattr(st, "_rows", None) or []
        if _rr:
            _tot = sum(r.oi_coin for r in _rr)
            if _tot > 0:
                _ivm = sum(r.iv * r.oi_coin for r in _rr) / _tot
            _cerca = [r for r in _rr if abs(r.strike - st.spot) / st.spot <= 0.05]
            _totc = sum(r.oi_coin for r in _cerca)
            if _totc > 0:
                _ivatm = sum(r.iv * r.oi_coin for r in _cerca) / _totc
    except Exception:
        pass

    # Preserve only provenance attested by the engine. A newly installed
    # recorder must not label a cached/legacy state as corrected. captured_at
    # is the engine valuation time, while recorded_at is local insert time;
    # neither establishes when the exchange published its underlying chain.
    _share_convention = getattr(st, "expiry_share_convention", None) or None
    _share_version = getattr(st, "expiry_share_version", None) if _share_convention else None
    _share_ramp = getattr(st, "expiry_share_ramp_days", None) if _share_convention else None
    _captured_at = getattr(st, "captured_at", None) if _share_convention else None
    _recorded_at = time.time()
    conc = (abs(st.net_gex) / st.abs_gex) if st.abs_gex > 0 else 0.0
    cur = c.execute(
        "INSERT INTO snaps(ts,iso,asset,clase,cobertura,spot,regimen,estado,"
        "net_gex_musd,conc,flip,dist_flip_pct,call_wall,put_wall,max_abs,"
        "vto_h,vto_share,n_contratos,vex_musd,cex_musd,"
        "vanna_wall,vanna_flip,charm_wall,delta_wall,delta_zero,vol_trigger,"
        "gamma_max,iv_media,iv_atm,poc,vah,val,vwap,"
        # __CONVENCION_GRIEGAS_2026_09_20__
        "cex_musd_ramp0,cex_ramp_days,cex_dte_max,"
        "expiry_share_convention,expiry_share_version,expiry_share_ramp_days,"
        "captured_at,recorded_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (st.ts, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(st.ts)), asset,
         clase, cobertura, st.spot, st.regimen, st.velocidad_esperada,
         round(st.net_gex / 1e6, 3), round(conc, 4), st.gamma_flip,
         st.dist_flip_pct, st.call_wall, st.put_wall, st.max_abs_strike,
         st.proximo_vto_horas, st.proximo_vto_share_abs, st.n_contratos,
         _e3.get("vex"), _e3.get("cex"),
         _gr.get("vanna_wall"), _gr.get("vanna_flip"), _gr.get("charm_wall"),
         _gr.get("delta_wall"), _gr.get("delta_zero"), _gr.get("vol_trigger"),
         _gr.get("gamma_max"), _ivm, _ivatm,
         _pf.get("poc"), _pf.get("vah"), _pf.get("val"), _pf.get("vwap"),
         # __CONVENCION_GRIEGAS_2026_09_20__
         _e3.get("cex_ramp0"), _e3.get("ramp_days"), _e3.get("dte_max"),
         _share_convention, _share_version, _share_ramp, _captured_at, _recorded_at))
    sid = cur.lastrowid
    top = sorted(st.ladder, key=lambda l: l.abs_gex, reverse=True)[:TOP_LADDER]
    c.executemany("INSERT INTO ladder(snap_id,strike,net_gex_musd,abs_gex_musd) VALUES(?,?,?,?)",
                  [(sid, l.strike, round(l.net_gex / 1e6, 4), round(l.abs_gex / 1e6, 4))
                   for l in top])
    return sid


def tomar():
    """Una toma de todos los activos. Devuelve cuantos se grabaron."""
    import gex_live as gl
    c = _db()
    n = 0
    for coin in CRIPTO:
        try:
            gl.refresh_once() if not gl.state(coin, "scalp") else None
            st = gl.state(coin, "scalp")
            if st:
                _guardar(c, coin, "cripto", "dominante", st)
                n += 1
        except Exception as e:
            print("  ! %s: %s" % (coin, e))
    for tk, clase in EQUITY:
        try:
            st = gl.equity_state(tk)
            if st:
                cob = (gl.COBERTURA.get(tk) or ("?", ""))[0]
                _guardar(c, tk, clase, cob, st)
                n += 1
        except Exception as e:
            print("  ! %s: %s" % (tk, e))
    c.commit(); c.close()
    return n


def stats():
    c = _db()
    print("Grabacion de niveles de gamma  ·  %s" % DB)
    tot = c.execute("SELECT COUNT(*) FROM snaps").fetchone()[0]
    if not tot:
        print("  todavia sin datos"); return
    print("  %d snapshots\n" % tot)
    print("  %-6s %-10s %-6s %-6s %-19s %-19s" % ("activo", "cobertura", "snaps", "dias", "desde", "hasta"))
    for a, cob, n, t0, t1 in c.execute(
            "SELECT asset,cobertura,COUNT(*),MIN(ts),MAX(ts) FROM snaps GROUP BY asset ORDER BY 3 DESC"):
        print("  %-6s %-10s %-6d %-6.1f %-19s %-19s" % (
            a, cob, n, (t1 - t0) / 86400.0,
            time.strftime("%Y-%m-%d %H:%M", time.gmtime(t0)),
            time.strftime("%Y-%m-%d %H:%M", time.gmtime(t1))))
    print("\n  regimenes observados")
    for a, r, n in c.execute("SELECT asset,regimen,COUNT(*) FROM snaps GROUP BY asset,regimen"):
        print("    %-6s %-9s %d" % (a, r, n))
    dias = (c.execute("SELECT MAX(ts)-MIN(ts) FROM snaps").fetchone()[0] or 0) / 86400.0
    print("\n  Para que el estudio tenga sentido hacen falta VARIAS SEMANAS y, sobre")
    print("  todo, haber visto los DOS regimenes. Llevas %.1f dias." % dias)
    c.close()


def main():
    if "--stats" in sys.argv:
        stats(); return
    if "--loop" in sys.argv:
        print("grabando cada %d s en %s (Ctrl-C para parar)" % (INTERVALO_S, DB))
        while True:
            t0 = time.time()
            try:
                n = tomar()
                print("%s  %d activos" % (time.strftime("%H:%M:%S"), n), flush=True)
            except Exception as e:
                print("error:", e, flush=True)
            time.sleep(max(30.0, INTERVALO_S - (time.time() - t0)))
    n = tomar()
    print("grabados %d activos" % n)
    stats()


if __name__ == "__main__":
    main()
