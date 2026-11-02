# -*- coding: utf-8 -*-
"""
ctx_fila.py — resuelve el CONTEXTO DE MERCADO global para UNA fila concreta.

EL PROBLEMA QUE RESUELVE
    Toda la informacion de contexto (BTC, dominancia, DXY, tasas, VIX, DVOL, gamma,
    sesgo del dia, OPEX) vive en una barra arriba del panel, igual para todas las
    filas. Leerla obliga a hacer la traduccion a mano: "el dolar baja, entonces el
    oro deberia subir, pero esta fila es una alt asi que el dolar no le habla, y su
    correlacion con BTC es 0,39 asi que el contexto de BTC si aplica..." .

    Este modulo hace esa traduccion y devuelve, POR FILA, que capas del contexto le
    hablan a ESE activo, si van a favor o en contra de SU direccion, y cuanto pesan.
    Con eso la tabla puede mostrar el contexto en una columna y la barra de arriba
    deja de ser necesaria.

LO QUE ESTA MEDIDO Y LO QUE NO
    MEDIDO (12.600 eventos, 30 alts, horizonte 24 h) — la correlacion de 48 h con BTC
    decide si el contexto de BTC le habla a la alt, y cuanto:

        corr >= 0,60    +0,129   sigue a BTC pero el contexto predice MENOS
        corr 0,30-0,60  +0,283   la banda de MAYOR poder predictivo
        corr <  0,30    -0,035   deja de predecir
        corr <  0,15    -0,189   predice al REVES

    MEDIDO (750 dias) — el regimen de gamma como veto del fade: en gamma negativa
    el fade rinde -0,362%/op contra -0,169% en positiva, orden estable 5 de 6 anios.

    NO MEDIDO, se muestra como LECTURA y va marcado como tal: el peso de las tasas
    por clase, el sesgo por dia de semana, el DXY inverso sobre oro/crudo/tech y el
    conmutador VIX/DVOL. Vienen de bibliografia o de estudios externos, no de una
    corrida propia con Monte Carlo. Se registran para validarlos con datos propios.

USO
    import ctx_fila
    ctx_fila.resolver(row)        # row del panel, con symbol y decision ya puestos
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Bandas de correlacion medidas. La de en medio es la de mayor poder predictivo:
# la alt sigue a BTC lo suficiente para que el contexto le hable, pero no tanto
# como para ser un clon (donde el contexto ya esta en el precio).
CORR_MIN = 0.30
CORR_OPTIMA = (0.30, 0.60)

# Que clase de activo es cada fila. Define QUE capas del contexto le hablan.
_CRIPTO_LIDER = ("BTCUSDT", "ETHUSDT")
_MATERIAS = {"XAUUSDT": "gold", "XAGUSDT": "gold", "CLUSDT": "crude"}


def _clase(sym, stocks=()):
    sym = (sym or "").upper()
    if sym in _MATERIAS:
        return "materia"
    if sym in _CRIPTO_LIDER:
        return "cripto-lider"
    if sym in stocks or not sym.endswith("USDT"):
        return "accion"
    return "cripto-alt"


def _dir_row(row):
    d = row.get("decision") or {}
    v = d.get("dec") or d.get("side")
    if v in ("LONG", "SHORT"):
        return v
    v = (row.get("verdict") or {}).get("side")
    return v if v in ("LONG", "SHORT") else None


def _capa(nombre, texto, delta, tip, medido=False):
    return {"n": nombre, "v": texto, "d": int(delta), "t": tip, "m": bool(medido)}


def resolver(row, mctx=None, rates_ctx=None, regime=None, gex_btc=None,
             stocks=(), dow=None):
    """
    Devuelve el contexto de mercado TRADUCIDO a esta fila.

        {"clase", "dir", "capas":[...], "neto", "veredicto", "expansion", "corr"}

    `neto` es la suma de los pesos de las capas que le hablan a este activo, con
    signo respecto de SU direccion. No es una probabilidad: es el mismo peso que
    ya se le aplica a la confianza del veredicto, expuesto para poder leerlo.
    """
    sym = (row.get("symbol") or "").upper()
    cls = _clase(sym, stocks)
    dirr = _dir_row(row)
    capas = []
    neto = 0
    expansion = False
    corr_info = row.get("corr_btc")

    # ── carga perezosa de los globales, si no vinieron dados ──────────────────
    if mctx is None:
        try:
            import market_regime as _mr
            mctx = _mr.context()
        except Exception:
            mctx = {}
    mctx = mctx or {}
    if rates_ctx is None:
        try:
            import rates_regime as _rr
            rates_ctx = _rr.context()
        except Exception:
            rates_ctx = {}
    rates_ctx = rates_ctx or {}

    # ── ① BTC + DOMINANCIA, con la compuerta de correlacion ──────────────────
    if cls in ("cripto-alt", "cripto-lider"):
        alt = mctx.get("alt_bias")
        efecto = mctx.get("alt_efecto") or ""
        btcd = mctx.get("btc_dir") or "?"
        domd = mctx.get("dom_dir") or "?"
        base = "BTC %s (%s%% 4h) · dominancia %s → %s" % (
            btcd, mctx.get("btc_4h"), domd, efecto)

        if cls == "cripto-lider":
            if alt in ("LONG", "SHORT") and dirr:
                d = 10 if alt == dirr else -14
                capas.append(_capa("BTC/dom", ("a favor" if d > 0 else "EN CONTRA"), d,
                                   base + ". Es el propio BTC/ETH: el contexto es su "
                                          "propio precio, sin compuerta de correlacion."))
                neto += d
            else:
                capas.append(_capa("BTC/dom", "lateral", 0, base))
        else:
            c = (corr_info or {}).get("corr")
            aplica = (corr_info or {}).get("aplica_contexto", True)
            if c is None:
                capas.append(_capa("BTC/dom", "sin correlacion", 0,
                                   base + ". No hay lectura de correlacion para esta fila."))
            elif not aplica or c < CORR_MIN:
                # Compuerta medida: bajo 0,30 el contexto de BTC deja de predecir.
                capas.append(_capa("BTC/dom", "DESACOPLADA · sin contexto BTC", 0,
                                   "correlacion de 48 h %.2f, por debajo de %.2f. MEDIDO sobre "
                                   "12.600 eventos: bajo ese umbral el poder predictivo del "
                                   "contexto de BTC pasa de +0,28 a -0,04, y bajo 0,15 se "
                                   "INVIERTE a -0,19. Por eso el contexto de BTC no se le "
                                   "aplica a esta fila: ni suma ni resta. Caso que lo motivo: "
                                   "DOT subio 11,46%% con BTC en -0,77%% y su correlacion "
                                   "habia caido de 0,52 a 0,39. Contexto global: %s"
                                   % (c, CORR_MIN, base), medido=True))
                if c < 0.15:
                    capas.append(_capa("corr", "INVERSA (<0,15)", 0,
                                       "correlacion %.2f: en esta banda el contexto de BTC "
                                       "predijo al REVES (-0,189). No se opera al reves "
                                       "-seria sobreajuste- pero tampoco se le hace caso."
                                       % c, medido=True))
            elif alt in ("LONG", "SHORT") and dirr:
                lo, hi = CORR_OPTIMA
                optima = lo <= c < hi
                peso = (10 if optima else 6)
                d = peso if alt == dirr else -(peso + 4)
                capas.append(_capa(
                    "BTC/dom",
                    ("a favor" if d > 0 else "EN CONTRA") + (" · banda optima" if optima else ""),
                    d,
                    base + ". Correlacion de 48 h %.2f, banda %s. MEDIDO: la banda 0,30-0,60 es "
                           "la de MAYOR poder predictivo (+0,283); por encima de 0,60 la alt es "
                           "casi un clon de BTC y el contexto predice MENOS (+0,129) porque ya "
                           "esta en el precio."
                    % (c, "OPTIMA 0,30-0,60" if optima else "alta >=0,60"), medido=True))
                neto += d
            else:
                # BTC lateral: el contexto no empuja para ningun lado, pero la banda
                # de correlacion se muestra igual porque es lo que decide si el
                # contexto le hablara a esta fila cuando BTC SI se defina.
                lo, hi = CORR_OPTIMA
                banda = ("OPTIMA 0,30-0,60" if lo <= c < hi else "alta >=0,60")
                capas.append(_capa("BTC/dom", "lateral · corr %.2f (%s)" % (c, banda), 0,
                                   base + ". Correlacion de 48 h %.2f, banda %s. MEDIDO: la banda "
                                          "0,30-0,60 es la de MAYOR poder predictivo (+0,283); por "
                                          "encima de 0,60 la alt es casi un clon de BTC y el "
                                          "contexto predice MENOS (+0,129)." % (c, banda),
                                   medido=True))

    # ── ② DOLAR (DXY), inverso, para acciones / metales / crudo ──────────────
    if cls in ("accion", "materia"):
        dxy = mctx.get("dxy_dir")
        if dxy in ("SUBE", "BAJA"):
            favorece = "SHORT" if dxy == "SUBE" else "LONG"
            im = (mctx.get("intermarket") or {})
            clave = {"XAUUSDT": "XAU", "XAGUSDT": "XAU", "CLUSDT": "CL"}.get(sym, "tech")
            est = im.get(clave)
            diverge = bool(est and est[1] == "DIVERGE")
            d = 0
            if dirr:
                d = 6 if favorece == dirr else -6
                if diverge:
                    d = 0
            capas.append(_capa(
                "DXY", "%s → favorece %s%s" % (dxy, favorece, " · DIVERGE" if diverge else ""), d,
                "El dolar se mueve INVERSO a oro, plata, crudo y tech. DXY %s %s%%. "
                "%s NO medido con corrida propia: viene de la relacion intermercado "
                "clasica (Murphy). Se registra para validarlo."
                % (dxy, mctx.get("dxy_chg"),
                   ("El activo DIVERGE de lo que predice el dolar, o sea que se mueve CON el "
                    "dolar: eso invalida la lectura para esta fila y el peso queda en cero. "
                    if diverge else ""))))
            neto += d

    # ── ③ TASAS, por clase y por lado ────────────────────────────────────────
    reg = rates_ctx.get("regime")
    if reg in ("HAWKISH", "DOVISH") and dirr:
        if cls == "materia" and sym in ("XAUUSDT", "XAGUSDT"):
            base_d = 2
        elif cls == "materia":
            base_d = 0                       # el crudo es neutro a las tasas
        else:
            base_d = 3                       # riesgo: acciones y cripto
        signo = -1 if reg == "HAWKISH" else 1
        d = base_d * signo * (1 if dirr == "LONG" else -1)
        if d:
            capas.append(_capa("tasas", "%s → %s el %s" % (reg, "resta" if d < 0 else "suma", dirr), d,
                               "Curva corta de EE.UU. y titulares de la Fed: %s (score %s). "
                               "%s. NO medido con corrida propia: es el peso de la "
                               "bibliografia, se registra en cada etiqueta para validarlo."
                               % (reg, rates_ctx.get("score"),
                                  " · ".join(rates_ctx.get("why") or []) or "sin senal")))
            neto += d

    # ── ④ SESGO POR DIA DE SEMANA (solo acciones, oro, crudo) ────────────────
    db = row.get("dow_bias") or None
    if db and db.get("side") and dirr:
        d = (3 if db["side"] == dirr else -3) * max(1, int(db.get("strength") or 1)) // 1
        d = max(-6, min(6, d))
        capas.append(_capa("dia", "%s %s" % (db["side"], "a favor" if db["side"] == dirr else "en contra"),
                           d, (db.get("note") or "") +
                           " Sesgo estadistico por dia de semana sobre ~57k sesiones. "
                           "Es un FILTRO, no un gatillo, y no aplica a cripto."))
        neto += d

    # ── ⑤ GAMMA de BTC: regimen del complejo cripto ──────────────────────────
    if gex_btc is None:
        gex_btc = (row.get("_gex_btc") or None)
    G = gex_btc or {}
    if cls in ("cripto-alt", "cripto-lider") and G.get("regimen"):
        pos = G.get("regimen") == "POSITIVO"
        dfp = G.get("dist_flip_pct")
        cerca = dfp is not None and abs(dfp) <= 0.5
        c = (corr_info or {}).get("corr")
        if cerca:
            txt, d = "BISAGRA · reducir tamano", 0
        elif pos:
            txt, d = "PINNING · el fade tiene el flujo a favor", 0
            # La alt DESACOPLADA en pinning de BTC es la candidata a EXPANDIR: la
            # cobertura de dealers comprime a BTC, y el riesgo que no lo sigue se
            # va por su cuenta. Es la observacion del usuario (caso DOT +11,46%);
            # NO esta medida todavia, va marcada como hipotesis.
            if cls == "cripto-alt" and c is not None and c < CORR_MIN:
                expansion = True
                txt = "PINNING de BTC + esta alt DESACOPLADA → puede EXPANDIR"
        else:
            txt, d = "VIOLENTA · NO fadear el extremo", 0
        capas.append(_capa(
            "gamma BTC", txt, d,
            "Regimen de gamma de BTC: %s, gamma flip %s%s. MEDIDO sobre 750 dias: el fade "
            "en gamma NEGATIVA rinde -0,362%%/op contra -0,169%% en positiva, con el orden "
            "estable 5 de 6 anios. Es el UNICO veto de contexto que paso la prueba. Para las "
            "alts es una inferencia POR BETA, no su estructura propia. %s"
            % (G.get("regimen"),
               ("{:,.0f}".format(G["gamma_flip"]).replace(",", ".") if G.get("gamma_flip") else "-"),
               (" (%+.2f%% del spot)" % dfp) if dfp is not None else "",
               ("HIPOTESIS SIN MEDIR: con BTC en pinning y esta alt desacoplada (corr %.2f), "
                "el capital que no sigue a BTC tiende a irse por su cuenta, o sea EXPANSION en "
                "vez de fade. Es la observacion del caso DOT. Todavia no tiene corrida propia: "
                "se muestra como aviso, no cambia el veredicto." % c) if expansion else ""),
            medido=True))

    # ── ⑤bis VENCIMIENTO DE GAMMA: el pinning se concentra cerca del vto ─────
    if cls in ("cripto-alt", "cripto-lider") and G.get("proximo_vto_horas") is not None:
        hh = G["proximo_vto_horas"]
        share = G.get("proximo_vto_share_abs") or 0.0
        if hh <= 12 and share >= 0.25:
            capas.append(_capa(
                "vto gamma", "en %.1f h con %d%% del |GEX|" % (hh, round(share * 100)), 0,
                "El proximo vencimiento de opciones concentra el %d%% de la gamma total y cae "
                "en %.1f horas. Mientras ese contrato vive, la cobertura de los dealers ancla "
                "el precio a sus strikes; cuando vence, esa gamma DESAPARECE de golpe y el "
                "anclaje se suelta. O sea: el pinning es mas fuerte AHORA y el rango se "
                "libera despues del vencimiento. NO medido con corrida propia."
                % (round(share * 100), hh)))

    # ── ⑥ CONMUTADOR VIX / DVOL: reversion o continuacion ────────────────────
    R = regime or row.get("_regime") or {}
    if R:
        if cls in ("cripto-alt", "cripto-lider") and R.get("dvol_pct") is not None:
            on = bool(R.get("dvol_on"))
            capas.append(_capa("DVOL", "p%d · %s" % (R["dvol_pct"], "REVERSION ON" if on else "CONTINUACION"),
                               0,
                               "Volatilidad implicita de BTC en percentil sobre 250 dias. Por encima "
                               "de p66 la reversion diaria paga; por debajo manda el momento. "
                               "Conmuta las fuentes diarias del motor, no esta fila."))
        if cls == "accion" and R.get("vix") is not None:
            on = bool(R.get("vix_on"))
            capas.append(_capa("VIX", "%.1f · %s" % (R["vix"], "REVERSION ON" if on else "reversion off"),
                               0,
                               "Con VIX > 20 la reversion diaria en ETFs y acciones paga (+1,2 a +2,0 "
                               "por operacion, 14-18 de 18 anios); con VIX <= 20 no hay ventaja y el "
                               "motor no abre esas fuentes."))
        if cls == "accion":
            b = R.get("breadth") or {}
            if b.get("on") is not None:
                capas.append(_capa("amplitud", "SPY %s · QQQ %s · %s"
                                   % (("%.2f" % b["spy"]) if b.get("spy") is not None else "-",
                                      ("%.2f" % b["qqq"]) if b.get("qqq") is not None else "-",
                                      "ON" if b["on"] else "off"), 0,
                                   "AMPLITUD: el cierre debil de una accion solo paga fuerte si el "
                                   "INDICE tambien cerro debil ese dia. Con amplitud a favor +3,5 por "
                                   "operacion en los apalancados 3x contra -0,09 sin ella. Los numeros "
                                   "son el cierre relativo al rango del dia de SPY y QQQ."))
            if R.get("opex_next"):
                capas.append(_capa("OPEX", str(R["opex_next"]), 0,
                                   "Proximo vencimiento de opciones de renta variable (tercer viernes). "
                                   "Si la semana del vencimiento fue roja, la semana siguiente tiende a "
                                   "rebotar en los apalancados, y el efecto es mas fuerte en los "
                                   "vencimientos trimestrales de marzo, junio, septiembre y diciembre."))
            if R.get("fomc_today"):
                capas.append(_capa("FOMC", "HOY · fuentes diarias apagadas", 0,
                                   "Dia de decision de la Fed: las fuentes diarias del motor no abren, "
                                   "porque su rendimiento medido cae a cerca de cero en esos dias."))

    # ── veredicto ────────────────────────────────────────────────────────────
    if not dirr:
        ver = "sin lado"
    elif not capas:
        ver = "sin contexto"
    elif neto >= 8:
        ver = "A FAVOR"
    elif neto <= -8:
        ver = "EN CONTRA"
    else:
        ver = "neutro"

    return {"clase": cls, "dir": dirr, "capas": capas, "neto": neto,
            "veredicto": ver, "expansion": expansion,
            "corr": (corr_info or {}).get("corr")}


def main():
    import json
    demo = {"symbol": "DOTUSDT", "decision": {"dec": "SHORT"}}
    try:
        import corr_btc as _cb
        demo["corr_btc"] = _cb.corr("DOTUSDT")
    except Exception:
        pass
    print(json.dumps(resolver(demo), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
