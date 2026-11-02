# -*- coding: utf-8 -*-
"""
convergencias.py — tabla de niveles donde CONFLUYEN metodos independientes.

LA IDEA
    Un nivel que sale de un solo metodo es una opinion. Un nivel donde coinciden
    el perfil de volumen, la estructura de gamma, el iman de liquidaciones y el
    libro de ordenes es un lugar donde hay dinero REAL obligado a operar por
    razones distintas. Eso es lo que se busca.

    El panel ya contaba confluencias, pero solo sobre el OBJETIVO (`conf_n`). Aca
    se arma la tabla COMPLETA: todos los niveles, agrupados en bandas, ordenados
    por cuantas fuentes INDEPENDIENTES coinciden.

FUENTES Y POR QUE PESAN DISTINTO
    El peso no es decorativo: refleja cuanta evidencia hay detras de cada fuente.

      2.0  gamma (muro / flip)   dinero obligado a cubrirse, es mecanico
      1.6  liquidaciones          posiciones que se cierran solas al tocarse
      1.4  POC                    el precio donde mas se opero en 24 h
      1.2  VWAP                   referencia de ejecucion institucional
      1.0  VAH / VAL              bordes del value area
      1.0  order block            zona validada en este repo (OB|SHORT +0.233%/op)
      0.9  muro del libro         liquidez en reposo, se puede retirar
      0.8  PDH / PDL              maximo y minimo del dia previo
      0.7  Asia / Londres         extremos de sesion

    Una banda con 3 o mas fuentes distintas y peso >= 3.5 es la que importa. Una
    con 1 sola fuente es ruido con nombre.

MEDIDO 2026-09-08 — LA DENSIDAD **NO** ESTA DEMOSTRADA
    Se probo con conv_backtest.py sobre 90 dias de velas de 5 min, reconstruyendo
    las bandas historicamente y comparando cada una contra placebos (el mismo nivel
    corrido 0.4-1.5% al azar). Resultado por cantidad de fuentes:

                    BTC                  ETH
        1 fuente    +0.1 pp              -4.2 pp
        2 fuentes   -1.5 pp              +3.8 pp
        3+ fuentes  -0.3 pp              +4.2 pp

    Los dos mercados se CONTRADICEN con magnitudes parecidas y signo opuesto, y en
    NINGUNA fila el intervalo de confianza al 90% quedo por encima del placebo. Esa
    es la firma del ruido, no de una senal.

    Ademas la tasa de rebote ronda el 49-53% en todos lados, o sea una moneda.

    CONCLUSION: la tabla sirve para UBICAR donde coinciden metodos (util para leer
    y para poner objetivos), pero NO para rankear niveles por cantidad de fuentes.
    Por eso en ubicacion.py la convergencia es INFORMATIVA y no vetea: la eleccion
    conservadora quedo validada por la medicion.

USO
    python -X utf8 convergencias.py BTCUSDT
    python -X utf8 convergencias.py TQQQUSDT --banda 0.5
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

PESOS = {
    "gamma": 2.0, "liquidacion": 1.6, "POC": 1.4, "VWAP": 1.2,
    "VAH": 1.0, "VAL": 1.0, "orderblock": 1.0, "libro": 0.9,
    "PDH": 0.8, "PDL": 0.8, "sesion": 0.7,
}
BANDA_PCT = 0.4          # dos niveles a <=0.4% se consideran el mismo lugar


def _perfil(sym):
    out = []
    try:
        import vwap_profile as vp
        p = vp.profile(sym, "5m")
        if not p:
            return [], None
        px = p["px"]
        for nom, k in (("POC", "poc"), ("VWAP", "vwap"), ("VAH", "vah"), ("VAL", "val")):
            if p.get(k):
                out.append((nom, float(p[k]), nom))
        for nom, k in (("+2.5σ", "up_band"), ("-2.5σ", "dn_band")):
            if p.get(k):
                out.append((nom, float(p[k]), "sesion"))
        return out, px
    except Exception:
        return [], None


def _gamma(sym, px):
    """Muros y flip. Cripto por Deribit; TQQQ por QQQ; XAU/CL por su ETF."""
    out = []
    try:
        import gex_live as gl
        if not gl.ready():                 # proceso suelto: calentar el cache
            gl.refresh_once()
        su = sym.upper()
        lv = None
        if gl.coin_for(su):
            lv = gl.levels(gl.coin_for(su), "scalp")
        elif su in getattr(gl, "EQUITY_MAP", {}) or su in getattr(gl, "COMMODITY_MAP", {}):
            tk = (gl.EQUITY_MAP.get(su) or gl.COMMODITY_MAP.get(su))[0]
            # para TQQQ los niveles operables son los del PROPIO TQQQ, no los de QQQ
            prop = {"TQQQUSDT": "TQQQ"}.get(su, tk)
            st = gl.equity_state(prop)
            lv = gl._eq_levels(st, prop) if st else None
        if not lv:
            return out
        for nom, k in (("call wall", "call_wall"), ("put wall", "put_wall"),
                       ("gamma flip", "gamma_flip"), ("imán |GEX|", "max_abs_strike"),
                       ("imán |GEX|", "max_abs")):
            v = lv.get(k)
            if v:
                out.append((nom, float(v), "gamma"))
        return out
    except Exception:
        return out


def _liquidacion(sym):
    out = []
    try:
        import liq_magnets as lm
        m = lm.magnets(sym) or {}
        for lado in ("up", "down"):
            for l in (m.get(lado) or [])[:3]:
                if l.get("px"):
                    out.append(("liq %s" % lado, float(l["px"]), "liquidacion"))
    except Exception:
        pass
    return out


def _libro(sym):
    out = []
    try:
        import orderbook_liq as ob
        d = ob.depth(sym) or {}
        for nom, k in (("muro bid", "bid_wall"), ("muro ask", "ask_wall")):
            w = d.get(k) or {}
            if w.get("px"):
                out.append((nom, float(w["px"]), "libro"))
    except Exception:
        pass
    return out


def _smc(sym):
    out = []
    try:
        import smc_zones as sz
        z = sz.smc_live(sym) or {}
        for k in ("ob_top", "ob_bottom", "bpr", "ifvg"):
            v = z.get(k)
            if isinstance(v, (int, float)) and v:
                out.append(("OB %s" % k, float(v), "orderblock"))
    except Exception:
        pass
    return out


def tabla(sym, banda_pct=BANDA_PCT, max_dist=6.0):
    """Devuelve las bandas de confluencia ordenadas por peso."""
    niveles, px = _perfil(sym)
    if not px:
        return None
    for f in (_gamma(sym, px), _liquidacion(sym), _libro(sym), _smc(sym)):
        niveles += f
    niveles = [(n, v, s) for n, v, s in niveles
               if v > 0 and abs(v - px) / px * 100.0 <= max_dist]
    if not niveles:
        return {"symbol": sym, "px": px, "bandas": []}

    niveles.sort(key=lambda x: x[1])
    bandas = []
    for nom, v, src in niveles:
        col = None
        for b in bandas:
            if abs(v - b["mid"]) / b["mid"] * 100.0 <= banda_pct:
                col = b; break
        if col is None:
            bandas.append({"mid": v, "items": [(nom, v, src)]})
        else:
            col["items"].append((nom, v, src))
            col["mid"] = sum(x[1] for x in col["items"]) / len(col["items"])

    out = []
    for b in bandas:
        fuentes = {}
        for nom, v, src in b["items"]:
            fuentes.setdefault(src, []).append(nom)
        peso = sum(PESOS.get(s, 0.5) for s in fuentes)
        d = (b["mid"] - px) / px * 100.0
        out.append({
            "px": round(b["mid"], 6), "dist_pct": round(d, 2),
            "lado": "arriba" if d > 0 else "abajo",
            "n_fuentes": len(fuentes), "peso": round(peso, 2),
            "fuentes": sorted(fuentes), "detalle": sorted(set(
                n for nom_list in fuentes.values() for n in nom_list)),
            "fuerte": len(fuentes) >= 3 and peso >= 3.5,
        })
    out.sort(key=lambda x: (-x["peso"], abs(x["dist_pct"])))
    return {"symbol": sym, "px": px, "banda_pct": banda_pct, "bandas": out}


def main():
    sym = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    b = BANDA_PCT
    if "--banda" in sys.argv:
        b = float(sys.argv[sys.argv.index("--banda") + 1])
    t = tabla(sym, b)
    if not t:
        print("sin perfil para %s" % sym); return
    print("=" * 80)
    print("CONVERGENCIAS  ·  %s  ·  precio %.6g  ·  banda %.1f%%" % (sym, t["px"], b))
    print("=" * 80)
    print("  %-12s %8s %5s %6s  %s" % ("nivel", "dist", "n", "peso", "fuentes"))
    print("  " + "-" * 74)
    for z in t["bandas"][:14]:
        marca = " <<<" if z["fuerte"] else ""
        print("  %-12.6g %+7.2f%% %5d %6.2f  %s%s" % (
            z["px"], z["dist_pct"], z["n_fuentes"], z["peso"],
            ", ".join(z["fuentes"]), marca))
    fuertes = [z for z in t["bandas"] if z["fuerte"]]
    print()
    if fuertes:
        print("  %d banda(s) FUERTE(S) (3+ fuentes independientes y peso >= 3.5):" % len(fuertes))
        for z in fuertes:
            print("    %-12.6g %+6.2f%%  %s" % (z["px"], z["dist_pct"], " · ".join(z["detalle"])))
    else:
        print("  ninguna banda llega a 3 fuentes independientes: hoy no hay confluencia clara.")
    print()
    print("  OJO: esto NO esta validado. Es lectura, no senal. Para probar que las bandas")
    print("  densas aguantan mas que las flojas hay que medirlo con gex_levels_study.py,")
    print("  que ya compara contra niveles placebo.")


if __name__ == "__main__":
    main()
