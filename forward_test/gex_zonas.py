# -*- coding: utf-8 -*-
"""
gex_zonas.py — P1/P2/N1/N2/A1/A2 por CLUSTERING sobre la curva gamma que YA existe.

QUE ES Y QUE NO ES
    NO construye nada nuevo. Consume la escalera por strike que `gex_engine`
    ya agrega desde Deribit + Bybit + OKX (`GexState.ladder`), la misma que
    alimenta el gamma flip, el call wall y el put wall.

    Lo unico que agrega es AGRUPAMIENTO: en vez de devolver "el strike con el
    valor maximo", junta strikes vecinos con gamma concentrada en una ZONA con
    borde inferior, borde superior, centro ponderado y fuerza.

        Sin clustering:  P1 = 111500
        Con clustering:  P1 ZONE 111000-112000 · CENTER 111500 · STRENGTH 92

    Por que importa: la gamma no vive en un strike, vive en un barrio. Los
    dealers cubren contra todo el barrio, y el precio se frena o se acelera en
    la zona, no en el numero redondo.

LAS SEIS ZONAS
    P1, P2   los dos mayores cumulos de gamma POSITIVA.
             Zonas de ESTABILIZACION: los dealers venden fuerza y compran
             debilidad -> pinning, reversion a la media, desaceleracion.
    N1, N2   los dos mayores cumulos de gamma NEGATIVA.
             Zonas de EXPANSION: los dealers venden debilidad y compran fuerza
             -> ruptura, continuacion, expansion de volatilidad.
    A1, A2   los dos mayores cumulos de gamma ABSOLUTA, sin importar el signo.
             NO tienen direccion. Solo dicen "esta zona es importante".

LA FUERZA
    strength = 100 x |gamma del cumulo| / |gamma del cumulo mas grande de todos|

    O sea que el cumulo mas grande del mapa siempre vale 100, y el resto se lee
    contra el. Es comparable entre zonas del mismo activo; NO es comparable
    entre activos distintos.

CAUSALIDAD
    No hay nada que mirar al futuro: la escalera es la foto del open interest de
    ahora. La zona se calcula con la informacion del momento y punto.

USO
    from gex_zonas import zonas_desde_ladder, zonas_para
    z = zonas_para("BTC")            # usa gex_live.state()
    z["P1"]["center"], z["P1"]["strength"], z["P1"]["dist_pct"]
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ---------------------------------------------------------------------------
# EL AGRUPAMIENTO
# ---------------------------------------------------------------------------
# Primera version: encadenar strikes vecinos mientras la separacion fuera chica.
# NO sirve. Con BTC dio "P1 = 79000-90000" y "A1 = 70000-90000": zonas del 14 % y
# del 25 % del precio. Eso no es una zona, es el mapa entero. La escalera es casi
# continua, asi que cualquier regla de proximidad pega todo con todo.
#
# Lo que corresponde es separar por PICOS Y VALLES (cuenca): se buscan los maximos
# locales y cada uno se expande hacia los costados mientras la curva BAJA. En
# cuanto vuelve a subir hay un valle, y ahi termina la zona y empieza la del pico
# siguiente. Asi P1 y P2 quedan separados por construccion.
FRAC_PICO = 0.25      # el borde de la zona cae cuando el valor baja de esta fraccion del pico
ANCHO_MAX_PCT = 4.0   # tope de cordura: una zona no puede ser mas ancha que esto del spot
FUERZA_MIN = 5        # por debajo de esta fuerza no se reporta la zona: es ruido


def _picos(vals):
    """Indices de maximos locales, con mesetas resueltas al centro."""
    n = len(vals)
    out, i = [], 0
    while i < n:
        j = i
        while j + 1 < n and vals[j + 1] == vals[i]:
            j += 1
        izq = (i == 0) or vals[i] > vals[i - 1]
        der = (j == n - 1) or vals[j] > vals[j + 1]
        if izq and der and vals[i] > 0:
            out.append((i + j) // 2)
        i = j + 1
    return out


def _cumulos(items, spot, frac=FRAC_PICO, ancho_max_pct=ANCHO_MAX_PCT):
    """
    items = [(strike, valor)] con valor >= 0. Devuelve zonas por cuenca,
    ordenadas por peso de mayor a menor y sin solaparse.
    """
    items = [(k, v) for k, v in items if v > 0]
    if not items:
        return []
    items.sort(key=lambda x: x[0])
    ks = [k for k, _ in items]
    vs = [v for _, v in items]
    ancho_max = (ancho_max_pct / 100.0) * spot if spot else float("inf")

    zonas = []
    for p in _picos(vs):
        piso = frac * vs[p]
        a = p
        while a - 1 >= 0 and vs[a - 1] <= vs[a] and vs[a - 1] >= piso                 and (ks[p] - ks[a - 1]) <= ancho_max / 2.0:
            a -= 1
        b = p
        while b + 1 < len(vs) and vs[b + 1] <= vs[b] and vs[b + 1] >= piso                 and (ks[b + 1] - ks[p]) <= ancho_max / 2.0:
            b += 1
        peso = sum(vs[a:b + 1])
        if peso <= 0:
            continue
        centro = sum(ks[t] * vs[t] for t in range(a, b + 1)) / peso
        zonas.append({"lo": float(ks[a]), "hi": float(ks[b]),
                      "center": float(centro), "peso": float(peso),
                      "n_strikes": b - a + 1, "pico_strike": float(ks[p]),
                      "_a": a, "_b": b})

    # Dos picos vecinos pueden compartir faldas. Se ordena por peso y se descarta
    # la zona que se superpone con una ya aceptada mas grande.
    zonas.sort(key=lambda z: z["peso"], reverse=True)
    fin = []
    for z in zonas:
        if any(not (z["_b"] < o["_a"] or z["_a"] > o["_b"]) for o in fin):
            continue
        fin.append(z)
    for z in fin:
        z.pop("_a", None)
        z.pop("_b", None)
    return fin


def _bucket(strikes):
    """Paso tipico de la escalera. Robusto a huecos: usa la mediana."""
    if len(strikes) < 2:
        return 0.0
    d = sorted(b - a for a, b in zip(strikes, strikes[1:]) if b > a)
    return d[len(d) // 2] if d else 0.0


def _redondear(z, paso):
    """Bordes al paso de la escalera, para que la zona se lea como en el tablero."""
    if paso and paso > 0:
        z["lo"] = round(z["lo"] / paso) * paso
        z["hi"] = round(z["hi"] / paso) * paso
        if z["hi"] < z["lo"]:
            z["lo"], z["hi"] = z["hi"], z["lo"]
    return z


def zonas_desde_ladder(ladder, spot, atr_pct=None,
                       frac=FRAC_PICO, ancho_max_pct=ANCHO_MAX_PCT,
                       fuerza_min=FUERZA_MIN):
    """
    ladder: lista de objetos con .strike/.net_gex/.abs_gex, o de dicts con esas
            claves. Es exactamente `GexState.ladder`.
    spot:   precio actual.
    atr_pct: ATR en % del precio, opcional, para la distancia en ATR.

    Devuelve {"P1":zona|None, "P2":..., "N1":..., "N2":..., "A1":..., "A2":...,
              "spot":..., "paso":...}
    donde cada zona es:
        lo, hi, center, strength (0-100), dist_pct, dist_atr, n_strikes,
        gex_musd (con signo para P/N, absoluto para A), tipo
    """
    filas = []
    for l in (ladder or []):
        if isinstance(l, dict):
            k, net, ab = l.get("strike"), l.get("net_gex"), l.get("abs_gex")
        else:
            k, net, ab = getattr(l, "strike", None), getattr(l, "net_gex", None), getattr(l, "abs_gex", None)
        if k is None or net is None:
            continue
        if ab is None:
            ab = abs(net)
        filas.append((float(k), float(net), float(ab)))

    vacio = {k: None for k in ("P1", "P2", "N1", "N2", "A1", "A2")}
    if not filas or not spot:
        vacio.update({"spot": spot, "paso": 0.0, "n_strikes": 0})
        return vacio

    paso = _bucket(sorted(k for k, _, _ in filas))

    pos = _cumulos([(k, net) for k, net, _ in filas if net > 0], spot, frac, ancho_max_pct)
    neg = _cumulos([(k, -net) for k, net, _ in filas if net < 0], spot, frac, ancho_max_pct)
    abso = _cumulos([(k, ab) for k, _, ab in filas], spot, frac, ancho_max_pct)

    # La fuerza se mide contra el cumulo mas grande DE TODO EL MAPA, asi el
    # numero dice "cuanto pesa esta zona comparada con la mas cargada".
    maximo = max([z["peso"] for z in (pos + neg + abso)] or [0.0])

    def armar(z, etiqueta, signo):
        if not z or maximo <= 0:
            return None
        fuerza = int(round(100.0 * z["peso"] / maximo))
        if fuerza < fuerza_min:      # ruido: no se reporta
            return None
        z = _redondear(dict(z), paso)
        d_pct = 100.0 * (z["center"] - spot) / spot
        return {
            "zona": etiqueta,
            "lo": round(z["lo"], 8),
            "hi": round(z["hi"], 8),
            "center": round(z["center"], 8),
            "strength": fuerza,
            "dist_pct": round(d_pct, 2),
            "dist_atr": (round(d_pct / atr_pct, 2)
                         if atr_pct and atr_pct > 0 else None),
            "n_strikes": z["n_strikes"],
            "gex_musd": round(signo * z["peso"] / 1e6, 2),
            "pico_strike": z["pico_strike"],
            "tipo": ("ESTABILIZACION" if etiqueta.startswith("P") else
                     "EXPANSION" if etiqueta.startswith("N") else "CONCENTRACION"),
        }

    out = {
        "P1": armar(pos[0] if len(pos) > 0 else None, "P1", +1),
        "P2": armar(pos[1] if len(pos) > 1 else None, "P2", +1),
        "N1": armar(neg[0] if len(neg) > 0 else None, "N1", -1),
        "N2": armar(neg[1] if len(neg) > 1 else None, "N2", -1),
        "A1": armar(abso[0] if len(abso) > 0 else None, "A1", +1),
        "A2": armar(abso[1] if len(abso) > 1 else None, "A2", +1),
        "spot": float(spot),
        "paso": float(paso),
        "n_strikes": len(filas),
    }
    return out


# ---------------------------------------------------------------------------
# CONFLUENCIA CON EL PERFIL DE VOLUMEN
# ---------------------------------------------------------------------------
# Tolerancia: una zona "esta cerca" de un nivel del perfil si el nivel cae dentro
# de la zona, o a menos de este % del spot del borde mas cercano.
TOL_CONF_PCT = 0.35


def _cerca(zona, nivel, spot, tol_pct=TOL_CONF_PCT):
    if not zona or nivel is None or not spot:
        return False
    try:
        nivel = float(nivel)
    except (TypeError, ValueError):
        return False
    if zona["lo"] <= nivel <= zona["hi"]:
        return True
    borde = zona["lo"] if nivel < zona["lo"] else zona["hi"]
    return abs(nivel - borde) / spot * 100.0 <= tol_pct


def confluencias(z, poc=None, val=None, vah=None, tol_pct=TOL_CONF_PCT):
    """
    Cruce de las zonas de gamma con el perfil de volumen del panel.

    Las cuatro que se muestran, con su lectura:
      P1 + POC  estabilizacion sobre el precio mas negociado -> iman e iman.
      P1 + VAL  estabilizacion en el piso del area de valor -> soporte doble.
      N1 + VAL  expansion en el piso del area -> riesgo de que el piso ceda.
      A1 + VAH  concentracion grande en el techo -> zona de gamma mayor.
    """
    spot = (z or {}).get("spot")
    out = []
    reglas = [
        ("P1", "POC", poc, "🟢", "P1 + POC", "estabilizacion sobre el precio mas negociado"),
        ("P1", "VAL", val, "🟢", "P1 + VAL", "soporte doble: gamma positiva en el piso del area"),
        ("N1", "VAL", val, "🟡", "N1 + VAL", "Expansion Risk: el piso del area con gamma negativa"),
        ("A1", "VAH", vah, "🔵", "A1 + VAH", "Major Gamma Zone en el techo del area"),
    ]
    for zk, nk, nivel, icono, etiq, nota in reglas:
        hay = _cerca((z or {}).get(zk), nivel, spot, tol_pct)
        out.append({"zona": zk, "nivel": nk, "hay": bool(hay),
                    "icono": icono, "etiqueta": etiq, "nota": nota,
                    "precio_nivel": nivel})
    return out


# ---------------------------------------------------------------------------
# APORTE AL SCORE — solo modifica puntajes, NO genera senales
# ---------------------------------------------------------------------------
# Reglas deliberadamente CHICAS y explicables. No son un edge medido: son una
# ponderacion de contexto. Nada de esto abre una operacion por si solo.
BOOST_MAX = 25

# __GEX_APORTE_OFF_2026_09_10__ — EL APORTE AL SCORE ESTA APAGADO.
#
# POR QUE. Los tres sumandos se midieron uno por uno y ninguno se verifica
# (biblioteca/16, secciones 12 y 15):
#   Gamma 12/8/4  los baldes de distancia NO son monotonos.
#   Value +4      la zona con confluencia REBOTA MENOS que un placebo puesto a la
#                 MISMA distancia (t -2,46), y la atraviesan mas.
#   Flow +5/-4    P y N no separan; en los dos baldes cercanos la P se atraviesa
#                 MAS que la N, que es lo contrario del mecanismo que la justifica.
#
# Y en iter.3 se midio lo que faltaba, que era la SUMA (bt_gex_aporte_off.py):
#   · el `total` NO ordena el resultado. Tercil alto contra tercil bajo:
#     FADE +0,132 pp (t +0,95 por racimo), TENDENCIA -0,017 pp (t -0,05) y ahi el
#     tercil ALTO le PIERDE a su propio azar del mismo lado (t -2,06).
#     Criterio pre-registrado (t>=2 Y las dos mitades Y el fuera de muestra en
#     tiempo del mismo signo): no se sostiene en ninguno de los dos modos.
#   · sobre BTC/ETH el `total` vale en promedio +12,96 (FADE) y +11,88 (TENDENCIA),
#     con mediana +13 / +11 y signo negativo solo el 5-7 % de las veces. O sea que
#     NO discrimina: es un subsidio casi constante de ~13 puntos de conf a los
#     activos con cadena de opciones, aplicado por una regla que no se verifica.
#   · el -4 del Flow dispara en el 25 % de las filas.
#
# QUE SE APAGA Y QUE NO. Se apaga UNICAMENTE el numero que se suma al score
# (`total` -> 0). Los tres sumandos se siguen calculando y se devuelven en
# `total_bruto`, `gamma`, `value`, `flow` y `detalle`, porque el panel y el
# indicador los MUESTRAN y eso informa. El dibujo no se toca.
#
# COMO SE VUELVE A PRENDER: `APORTE_AL_SCORE = True`. Antes de hacerlo hace falta
# una medicion que pase la vara, no una impresion.
APORTE_AL_SCORE = False


def aporte_score(z, confl, lado=None, modo=None, spot=None):
    """
    Devuelve {"gamma": n, "value": n, "flow": n, "total": n, "detalle": [...]}
    Los tres sumandos son los que pediste: Gamma, Value y Flow.

    · gamma  cuan cerca esta el precio de una zona fuerte y de que tipo es.
    · value  cuantas confluencias con el perfil hay.
    · flow   coherencia entre el tipo de zona y el modo de la fila:
             FADE/REVERSION casa con P (estabilizacion);
             TENDENCIA/CONTINUACION casa con N (expansion).
    """
    det, g, v, f = [], 0, 0, 0
    if not z:
        return {"gamma": 0, "value": 0, "flow": 0, "total": 0,
                "total_bruto": 0, "aplicado": bool(APORTE_AL_SCORE), "detalle": []}

    # --- gamma: la zona mas cercana manda
    cercanas = [zz for zz in (z.get("P1"), z.get("N1"), z.get("A1"))
                if zz and zz.get("dist_pct") is not None]
    if cercanas:
        cerca = min(cercanas, key=lambda x: abs(x["dist_pct"]))
        d = abs(cerca["dist_pct"])
        if d <= 0.5:
            g += 12
        elif d <= 1.5:
            g += 8
        elif d <= 3.0:
            g += 4
        if cerca["strength"] >= 80:
            g += 3
        det.append("%s a %.2f%% (fuerza %d)" % (cerca["zona"], cerca["dist_pct"],
                                                cerca["strength"]))

    # --- value: confluencias con el perfil
    activas = [c for c in (confl or []) if c.get("hay")]
    v += min(4 * len(activas), 8)
    for c in activas:
        det.append("%s %s" % (c["icono"], c["etiqueta"]))

    # --- flow: coherencia zona/modo
    if cercanas and modo:
        cerca = min(cercanas, key=lambda x: abs(x["dist_pct"]))
        rev = str(modo).upper() in ("FADE", "REVERSION")
        cont = str(modo).upper() in ("TENDENCIA", "CONTINUACION")
        if cerca["zona"].startswith("P") and rev:
            f += 5
            det.append("zona de estabilizacion a favor de la reversion")
        elif cerca["zona"].startswith("N") and cont:
            f += 5
            det.append("zona de expansion a favor de la continuacion")
        elif cerca["zona"].startswith("P") and cont:
            f -= 4
            det.append("zona de estabilizacion EN CONTRA de la continuacion")
        elif cerca["zona"].startswith("N") and rev:
            f -= 4
            det.append("zona de expansion EN CONTRA de la reversion")

    # __GEX_APORTE_OFF_2026_09_10__ — `total_bruto` es lo que la formula da y se
    # sigue MOSTRANDO; `total` es lo que se APLICA al score, y con el interruptor
    # apagado vale 0. Quien puntue tiene que leer `total`; quien dibuje, `total_bruto`.
    bruto = max(-BOOST_MAX, min(BOOST_MAX, g + v + f))
    if not APORTE_AL_SCORE:
        det.append("aporte al score APAGADO (__GEX_APORTE_OFF_2026_09_10__): "
                   "los tres sumandos no pasaron la vara; el numero informa, no puntua")
    return {"gamma": g, "value": v, "flow": f,
            "total": (bruto if APORTE_AL_SCORE else 0),
            "total_bruto": bruto, "aplicado": bool(APORTE_AL_SCORE),
            "detalle": det}


# ---------------------------------------------------------------------------
# ATAJO EN VIVO
# ---------------------------------------------------------------------------
def zonas_para(coin="BTC", view="scalp", atr_pct=None):
    """Zonas del snapshot vivo de `gex_live`. Devuelve None si no hay foto."""
    try:
        import gex_live as gl
    except Exception:
        return None
    st = gl.state(coin, view)
    if st is None:
        return None
    return zonas_desde_ladder(st.ladder, st.spot, atr_pct=atr_pct)


def texto(z, confl=None):
    """Bloque GAMMA STRUCTURE para consola o tarjeta del panel."""
    if not z:
        return "GAMMA STRUCTURE: sin datos"
    L = ["GAMMA STRUCTURE"]
    for k in ("P1", "P2", "N1", "N2", "A1", "A2"):
        zz = z.get(k)
        if not zz:
            L.append("  %-3s —" % k)
            continue
        L.append("  %-3s %-12s zona %g-%g · fuerza %3d · %+.2f%%%s"
                 % (k, ("%g" % zz["center"]), zz["lo"], zz["hi"], zz["strength"],
                    zz["dist_pct"],
                    (" · %.2f ATR" % zz["dist_atr"]) if zz.get("dist_atr") is not None else ""))
    if confl:
        L.append("CONFLUENCE")
        for c in confl:
            L.append("  %-10s %s" % (c["etiqueta"], "YES" if c["hay"] else "NO"))
    return "\n".join(L)


if __name__ == "__main__":
    import json
    for c in ("BTC", "ETH"):
        z = zonas_para(c)
        if not z:
            print("%s: sin snapshot (¿esta corriendo gex_live?)" % c)
            continue
        print("=" * 70)
        print("%s  spot %g  paso %g  strikes %d" % (c, z["spot"], z["paso"], z["n_strikes"]))
        print(texto(z))
        print(json.dumps({k: z[k] for k in ("P1", "N1", "A1") if z.get(k)},
                         ensure_ascii=False, indent=2)[:600])


# ---------------------------------------------------------------------------
# MARCAS DE CONFLUENCIA POR ZONA
# ---------------------------------------------------------------------------
# El usuario quiere ver SOLO la estructura de gamma en el grafico, pero con la
# confluencia MARCADA. O sea: no dibujar el POC, pero si avisar que P1 cae encima
# del POC. La marca viaja pegada a la zona, no como una linea aparte.
#
# Se marcan dos clases de coincidencia:
#   contra el PERFIL   POC, VAL, VAH  -> el nivel de gamma cae donde se negocio
#   contra la GAMMA    flip, call wall, put wall, y las otras zonas -> apilamiento
#
# La segunda importa tanto como la primera: cuando A1 cae sobre P1, la zona pesada
# ademas estabiliza; cuando A1 cae sobre N1, la zona pesada ademas acelera.
TOL_MARCA_PCT = 0.35


def marcas_confluencia(z, poc=None, val=None, vah=None,
                       call_wall=None, put_wall=None, flip=None,
                       obs=None, tol_pct=TOL_MARCA_PCT):
    """
    Devuelve {"P1": "POC+CW", "A1": "P1", ...} con lo que coincide con cada zona.
    Cadena vacia (no la clave) cuando no coincide con nada.
    """
    z = z or {}
    spot = z.get("spot")
    out = {}
    externos = [("POC", poc), ("VAL", val), ("VAH", vah),
                ("CW", call_wall), ("PW", put_wall), ("FLIP", flip)]
    claves = ("P1", "P2", "N1", "N2", "A1", "A2")
    for k in claves:
        zz = z.get(k)
        if not zz:
            continue
        marcas = []
        for et, nivel in externos:
            if _cerca(zz, nivel, spot, tol_pct):
                marcas.append(et)
        # apilamiento entre zonas: solo se anota una vez, en la de menor jerarquia
        for k2 in claves:
            if k2 == k:
                continue
            z2 = z.get(k2)
            if not z2:
                continue
            # se solapan los rangos, o los centros estan a menos de la tolerancia
            solapan = not (zz["hi"] < z2["lo"] or zz["lo"] > z2["hi"])
            cerca = spot and abs(zz["center"] - z2["center"]) / spot * 100.0 <= tol_pct
            if (solapan or cerca) and claves.index(k2) < claves.index(k):
                marcas.append(k2)
        # ORDER BLOCKS. Se marcan porque el usuario los quiere ver, y ver la
        # coincidencia informa. NO suman puntaje: el order block se midio dos
        # veces en esta casa y las dos perdio (igual que el azar como gatillo;
        # con menos rechazo que un placebo como nivel). Es descripcion, no señal.
        for ob in (obs or []):
            try:
                t, b = float(ob["top"]), float(ob["bottom"])
            except (KeyError, TypeError, ValueError):
                continue
            solapa = not (zz["hi"] < b or zz["lo"] > t)
            cerca = spot and abs(zz["center"] - (t + b) / 2.0) / spot * 100.0 <= tol_pct
            if solapa or cerca:
                marcas.append("OB" + ("+" if str(ob.get("lado")) == "LONG" else "-"))
                break
        if marcas:
            out[k] = "+".join(marcas)
    return out


# ---------------------------------------------------------------------------
# QUE SE ESPERA DEL PRECIO EN CADA NIVEL
# ---------------------------------------------------------------------------
# Pedido del usuario: no alcanza con marcar la confluencia, hay que decir QUE se
# esperaria que haga el precio ahi. Se resuelve aca, del lado del servidor, y
# viaja como texto listo: el grafico solo lo imprime.
#
# La lectura sale del MECANISMO de cobertura, no de una medicion:
#   gamma POSITIVA  el dealer vende fuerza y compra debilidad -> FRENA
#   gamma NEGATIVA  el dealer vende debilidad y compra fuerza -> ACELERA
#   |gamma| grande  mucha cobertura de cualquier signo -> la zona DECIDE
# y se matiza con lo que coincide y con si la zona esta arriba o abajo del precio.
#
# OJO: esto es interpretacion, NO una probabilidad medida. Los pesos de este
# sistema NO pasaron la vara de robustez (ver biblioteca/16, seccion 8).

def lectura_zona(clave, marca="", dist_pct=None):
    """
    Frase corta de que se esperaria del precio en esa zona.
    `clave` = P1/P2/N1/N2/A1/A2 · `marca` = "POC+CW" · `dist_pct` = distancia con signo.
    """
    if not clave:
        return ""
    m = set((marca or "").upper().split("+")) - {""}
    arriba = (dist_pct is not None and dist_pct > 0)
    donde = "arriba" if arriba else "abajo"
    tipo = clave[0]

    ob = "OB+" in m or "OB-" in m
    if tipo == "P":
        base = "frena y tiende a revertir"
        if ob and "POC" not in m and "CW" not in m and "PW" not in m:
            return ("estabiliza sobre un order block " +
                    ("de demanda" if "OB+" in m else "de oferta") +
                    " - apilamiento (el OB no valido en pruebas)")
        if "POC" in m:
            return "iman doble: tiende a quedar pegado (pinning)"
        if "CW" in m:
            return "techo duro: los dealers tapan la suba"
        if "PW" in m:
            return "piso duro: los dealers sostienen"
        if "VAL" in m:
            return "piso reforzado: favorece el rebote"
        if "VAH" in m:
            return "techo reforzado: favorece el rechazo"
        if "FLIP" in m:
            return "estabiliza justo en el cambio de regimen: zona indecisa"
        return base + " (" + donde + ")"

    if tipo == "N":
        if "VAL" in m:
            return "riesgo de expansion: si pierde el piso, cae rapido"
        if "PW" in m:
            return "el piso puede ceder con violencia"
        if "VAH" in m or "CW" in m:
            return "si rompe el techo, extiende el movimiento"
        if "POC" in m:
            return "el precio mas negociado sin amortiguacion: movimiento amplio"
        if "FLIP" in m:
            return "cambio de regimen con aceleracion: cuidado con el latigazo"
        return "si la atraviesa, acelera (" + donde + ")"

    # A: concentracion, sin direccion propia. Manda con quien coincide.
    if m & {"P1", "P2"}:
        return "zona pesada que ADEMAS estabiliza: freno fuerte"
    if m & {"N1", "N2"}:
        return "zona pesada que ADEMAS acelera: ruptura fuerte"
    if "POC" in m:
        return "maxima gamma sobre el precio mas negociado: iman"
    if "CW" in m:
        return "toda la carga en el techo: resistencia principal"
    if "PW" in m:
        return "toda la carga en el piso: soporte principal"
    if "VAH" in m:
        return "zona pesada en el techo del area: define el movimiento"
    if "VAL" in m:
        return "zona pesada en el piso del area: define el movimiento"
    return "zona pesada: sin direccion, pero decide (" + donde + ")"


# ---------------------------------------------------------------------------
# ESCALADO A UN APALANCADO (TQQQ desde QQQ, SOXL desde SMH, SPXL desde SPY)
# ---------------------------------------------------------------------------
# Los apalancados NO tienen cadena de opciones representativa propia: el nocional
# vive en el indice (QQQ 67,9 B contra TQQQ 4,4 B). Pero el nivel del indice SI se
# puede llevar a la escala del apalancado, porque el ETF replica el retorno DIARIO
# multiplicado:
#
#     un movimiento de x % en QQQ  ->  aproximadamente  3x %  en TQQQ
#
#     nivel_tqqq = spot_tqqq * (1 + LEV * (nivel_qqq / spot_qqq - 1))
#
# Es exacto a primer orden DENTRO DEL DIA, que es el horizonte en el que se usan
# estos niveles. NO sirve para varios dias: ahi entra el decaimiento por
# rebalanceo (medido en 10_tqqq_vs_nasdaq.md: peaje de -12 % anual).
#
# Se marca como `escalado` para que quede claro que es un nivel DERIVADO y no un
# strike donde haya open interest de verdad.
# Activo destino -> (de que cadena se lee, multiplicador de retorno DIARIO).
#
# Dos familias distintas, misma matematica:
#   APALANCADOS  el ETF replica el retorno diario del indice x3 (o x-3).
#   PROXIES 1:1  el perpetuo NO tiene opciones propias, pero existe un ETF sobre el
#                MISMO subyacente y con el mismo retorno. Oro: XAUUSDT y GLD se
#                mueven igual en %. Petroleo: CLUSDT y USO, con la reserva de que
#                USO arrastra el rolleo de los futuros (intradia es despreciable,
#                a varios dias no).
#
# ★ Por que no hay mas cripto: verificado el 2026-09-10 contra la API de Deribit,
#   los UNICOS con instrumentos de opciones VIVOS son BTC (954) y ETH (820).
#   TAO, UNI, SOL, XRP y el resto figuran como monedas pero tienen CERO opciones.
#   No es una limitacion del bot: no existen los contratos, y sin contratos no hay
#   open interest por strike, y sin eso no hay gamma. Punto.
APALANCADOS = {"TQQQ": ("QQQ", 3.0), "SOXL": ("SMH", 3.0), "SPXL": ("SPY", 3.0),
               "SQQQ": ("QQQ", -3.0), "SOXS": ("SMH", -3.0),
               # proxies 1:1 sobre el mismo subyacente
               "XAU": ("GLD", 1.0), "XAG": ("SLV", 1.0), "CL": ("USO", 1.0),
               "GOLD": ("GLD", 1.0), "SILVER": ("SLV", 1.0), "OIL": ("USO", 1.0)}


def escalar_precio(nivel, spot_indice, spot_apalancado, lev):
    if not nivel or not spot_indice or not spot_apalancado:
        return None
    return spot_apalancado * (1.0 + lev * (nivel / spot_indice - 1.0))


def escalar_zonas(z, spot_indice, spot_apalancado, lev):
    """Devuelve una copia de las zonas llevadas a la escala del apalancado."""
    if not z or not spot_indice or not spot_apalancado:
        return None
    out = {"spot": spot_apalancado, "paso": None,
           "n_strikes": z.get("n_strikes"), "escalado": True,
           "desde_spot": spot_indice, "lev": lev}
    for k in ("P1", "P2", "N1", "N2", "A1", "A2"):
        zz = z.get(k)
        if not zz:
            out[k] = None
            continue
        c = escalar_precio(zz["center"], spot_indice, spot_apalancado, lev)
        lo = escalar_precio(zz["lo"], spot_indice, spot_apalancado, lev)
        hi = escalar_precio(zz["hi"], spot_indice, spot_apalancado, lev)
        if lev < 0 and lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo          # el inverso da vuelta el rango
        d = (100.0 * (c - spot_apalancado) / spot_apalancado) if c else None
        n = dict(zz)
        n.update({"center": c, "lo": lo, "hi": hi, "dist_pct":
                  round(d, 2) if d is not None else None, "escalado": True})
        out[k] = n
    return out
