"""
__GRIEGAS_2026_09_13__ - los niveles de griegas que faltaban.

El motor GEX (gex_engine.py) ya calculaba la GAMMA por strike: de ahi salen el
gamma flip, el call wall, el put wall y las zonas P/N/A. Pero la gamma es UNA
sola de las derivadas que el dealer tiene que cubrir. Este modulo agrega las
otras tres, con la MISMA convencion de signo que el GEX (call +, put -, es decir
"el dealer esta corto de lo que el publico compro"), y las convierte en NIVELES
de precio dibujables:

    VEX  vanna    d(delta)/d(vol)    -> que hace el dealer si se mueve la VOL
    CEX  charm    d(delta)/d(tiempo) -> que hace el dealer si solo PASA el tiempo
    DEX  delta    delta              -> cuanto subyacente tiene que tener cubierto

NIVELES QUE SALEN DE ACA
    vanna_wall   strike con mayor |VEX|. Si la vol cae ahi, el dealer tiene que
                 reajustar fuerte. Es el nivel que se activa en las compresiones
                 de volatilidad (y el que explica los rallies de "vanna squeeze"
                 cuando la IV se desinfla despues de un evento).
    vanna_flip   cruce por cero del perfil de VEX. Arriba y abajo de ese precio
                 el signo de la cobertura por vol se da vuelta.
    charm_wall   strike con mayor |CEX|. Es el iman que CRECE solo con el paso
                 del tiempo: el pinning de vencimiento vive aca.
    delta_wall   strike con mayor |DEX|. Donde se concentra el delta que el
                 dealer ya tiene cubierto; romperlo obliga a re-cubrir en masa.
    delta_zero   precio donde el DEX total cruza cero = punto de equilibrio del
                 posicionamiento. Arriba el dealer esta net corto de delta,
                 abajo net largo (con la convencion de signo de arriba).
    vol_trigger  precio donde el GEX neto cruzaria cero SI la vol se expandiera.
                 Es el gamma flip "estresado": avisa cuanto margen hay antes de
                 que un aumento de volatilidad de vuelta el regimen.
    gamma_max    strike con mayor |GEX| (ya existia como max_abs_strike; se
                 repite aca para que el paquete de griegas sea autocontenido).

HONESTIDAD SOBRE LA EVIDENCIA
    Estos niveles son MECANISMO, no edge medido. Salen de la matematica de la
    cobertura del dealer bajo Black-Scholes con r = q = 0, la misma convencion
    que ya usa el motor. NO estan backtesteados contra retornos futuros y NO
    deben sumar puntaje a ninguna decision hasta que lo esten. Se dibujan por lo
    mismo que el order block: informan donde puede haber reaccion mecanica.

    El `vol_trigger` es ademas un PROXY: la definicion de la industria usa la
    superficie de vol completa; aca se aproxima escalando la IV de toda la cadena
    (factor `bump`) y buscando el nuevo cruce por cero. Sirve para saber si el
    regimen es fragil o robusto, no como precio exacto.
"""

from __future__ import annotations

import math

import numpy as np

from gex_engine import _arrays, _d1d2, bs_gamma, bs_vanna, bs_charm


# ---------------------------------------------------------------------------
# DELTA
# ---------------------------------------------------------------------------

def _erf_escalar(x):
    """Abramowitz-Stegun 7.1.26. Error < 1.5e-7, de sobra para un nivel."""
    s = 1.0 if x >= 0 else -1.0
    x = abs(float(x))
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * math.exp(-x * x)
    return s * y


_erf_vec = np.vectorize(_erf_escalar, otypes=[float])


def _norm_cdf(x):
    """Phi(x) sin scipy: 0.5*(1+erf(x/sqrt(2))), vectorizado."""
    return 0.5 * (1.0 + _erf_vec(np.asarray(x, dtype=float) / math.sqrt(2.0)))


def bs_delta(S, K, T, sigma, is_call):
    """
    Delta de Black-Scholes con r = q = 0.
        call: Phi(d1)
        put : Phi(d1) - 1
    `is_call` es un array booleano difundible contra K.
    """
    d1, _d2, _sq, _T, _sig = _d1d2(S, K, T, sigma)
    nd1 = _norm_cdf(d1)
    return np.where(np.asarray(is_call, dtype=bool), nd1, nd1 - 1.0)


# ---------------------------------------------------------------------------
# ESCALERAS POR STRIKE
# ---------------------------------------------------------------------------

def _bucketizar(K, valores, bucket):
    """Agrupa por strike redondeado. Devuelve (strikes, sumas) ordenado."""
    Kb = np.round(K / bucket) * bucket if bucket else K
    out = {}
    for k, v in zip(Kb, valores):
        out[float(k)] = out.get(float(k), 0.0) + float(v)
    ks = sorted(out)
    return (np.array(ks, dtype=float),
            np.array([out[k] for k in ks], dtype=float))


def escaleras(rows, spot, bucket=None, now=None):
    """
    Las tres exposiciones POR STRIKE, con la unidad de `exposiciones()`:

        gex : USD por 1 % de movimiento del subyacente
        vex : USD por 1 punto de volatilidad implicita
        cex : USD por dia calendario
        dex : USD de delta nocional

    Devuelve {"strikes", "gex", "vex", "cex", "dex"} como arrays alineados.
    """
    if not rows:
        z = np.zeros(0)
        return {"strikes": z, "gex": z, "vex": z, "cex": z, "dex": z}
    K, T, sig, oi, sign = _arrays(rows, now)
    coef = oi * sign

    g = bs_gamma(spot, K, T, sig) * coef * spot * spot * 0.01
    v = bs_vanna(spot, K, T, sig) * coef * spot * 0.01
    c = bs_charm(spot, K, T, sig) * coef * spot / 365.0
    d = bs_delta(spot, K, T, sig, sign > 0) * coef * spot

    if bucket is None:
        from gex_engine import _auto_bucket
        bucket = _auto_bucket(spot)

    ks, gg = _bucketizar(K, g, bucket)
    _, vv = _bucketizar(K, v, bucket)
    _, cc = _bucketizar(K, c, bucket)
    _, dd = _bucketizar(K, d, bucket)
    return {"strikes": ks, "gex": gg, "vex": vv, "cex": cc, "dex": dd}


# ---------------------------------------------------------------------------
# PERFILES (mismo metodo que gamma_profile: se mueve el SPOT, se fijan IV y T)
# ---------------------------------------------------------------------------

def _perfil(rows, spot, cual, span_pct=0.20, n=241, now=None, bump=1.0):
    lo, hi = spot * (1 - span_pct), spot * (1 + span_pct)
    xs = np.linspace(lo, hi, n)
    if not rows:
        return xs, np.zeros_like(xs)
    K, T, sig, oi, sign = _arrays(rows, now)
    sig = sig * bump
    coef = (oi * sign)[None, :]
    X = xs[:, None]
    Kb, Tb, Sb = K[None, :], T[None, :], sig[None, :]
    if cual == "gex":
        ys = (bs_gamma(X, Kb, Tb, Sb) * coef).sum(axis=1) * xs ** 2 * 0.01
    elif cual == "vex":
        ys = (bs_vanna(X, Kb, Tb, Sb) * coef).sum(axis=1) * xs * 0.01
    elif cual == "cex":
        ys = (bs_charm(X, Kb, Tb, Sb) * coef).sum(axis=1) * xs / 365.0
    elif cual == "dex":
        ys = (bs_delta(X, Kb, Tb, Sb, (sign > 0)[None, :]) * coef).sum(axis=1) * xs
    else:
        raise ValueError(cual)
    return xs, ys


def _cruce_cero(xs, ys, ref):
    """Cruce por cero mas cercano a `ref`, interpolado. None si no hay."""
    s = np.sign(ys)
    idx = np.where(np.diff(s) != 0)[0]
    if idx.size == 0:
        return None
    raices = []
    for i in idx:
        y0, y1 = ys[i], ys[i + 1]
        if y1 == y0:
            continue
        raices.append(xs[i] + (xs[i + 1] - xs[i]) * (-y0) / (y1 - y0))
    if not raices:
        return None
    return float(min(raices, key=lambda r: abs(r - ref)))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

_VACIO = {"vanna_wall": None, "vanna_wall_signo": 0, "vanna_flip": None,
          "charm_wall": None, "charm_wall_signo": 0,
          "delta_wall": None, "delta_wall_signo": 0, "delta_zero": None,
          "vol_trigger": None, "gamma_max": None,
          "vex_total": 0.0, "cex_total": 0.0, "dex_total": 0.0}


def niveles(rows, spot, bucket=None, now=None, span_pct=0.20, bump=1.35):
    """
    Todos los niveles de griegas de una cadena. `bump` es el factor por el que se
    escala la IV para el `vol_trigger` (1.35 = "y si la vol sube un 35 %").

    Devuelve un dict de precios (o None donde el dato no alcanza) + los totales.
    Nunca levanta: si la cadena esta vacia o rota, devuelve todo en None.
    """
    try:
        if not rows or not spot or float(spot) <= 0:
            return dict(_VACIO)
        spot = float(spot)
        esc = escaleras(rows, spot, bucket=bucket, now=now)
        ks = esc["strikes"]
        if ks.size == 0:
            return dict(_VACIO)

        def _pico(arr):
            i = int(np.argmax(np.abs(arr)))
            v = float(arr[i])
            return float(ks[i]), (1 if v > 0 else -1 if v < 0 else 0)

        vw, vw_s = _pico(esc["vex"])
        cw, cw_s = _pico(esc["cex"])
        dw, dw_s = _pico(esc["dex"])
        gm, _gs = _pico(esc["gex"])

        xs_v, ys_v = _perfil(rows, spot, "vex", span_pct=span_pct, now=now)
        xs_d, ys_d = _perfil(rows, spot, "dex", span_pct=span_pct, now=now)
        xs_g, ys_g = _perfil(rows, spot, "gex", span_pct=span_pct, now=now,
                             bump=bump)

        return {
            "vanna_wall": vw, "vanna_wall_signo": vw_s,
            "vanna_flip": _cruce_cero(xs_v, ys_v, spot),
            "charm_wall": cw, "charm_wall_signo": cw_s,
            "delta_wall": dw, "delta_wall_signo": dw_s,
            "delta_zero": _cruce_cero(xs_d, ys_d, spot),
            "vol_trigger": _cruce_cero(xs_g, ys_g, spot),
            "gamma_max": gm,
            "vex_total": float(np.sum(esc["vex"])),
            "cex_total": float(np.sum(esc["cex"])),
            "dex_total": float(np.sum(esc["dex"])),
        }
    except Exception:                                    # noqa: BLE001
        return dict(_VACIO)


def niveles_de_estado(st, span_pct=0.20, bump=1.35):
    """
    Igual que `niveles()` pero partiendo de un GexState ya construido: usa las
    filas que `build_state` deja guardadas en `_rows` para no volver a bajar la
    cadena. Si el estado no las tiene (snapshot viejo), devuelve todo en None.
    """
    if st is None:
        return dict(_VACIO)
    rows = getattr(st, "_rows", None)
    if not rows:
        return dict(_VACIO)
    return niveles(rows, float(getattr(st, "spot", 0.0) or 0.0),
                   now=getattr(st, "ts", None), span_pct=span_pct, bump=bump)


_LECTURAS = {
    "vanna_flip": "arriba y abajo se da vuelta la cobertura por volatilidad",
    "charm_wall": "iman de vencimiento - se refuerza solo con el paso del tiempo",
    "delta_zero": "equilibrio del posicionamiento - sin sesgo de cobertura",
    "vol_trigger": ("con la vol expandida el regimen se da vuelta aca - "
                    "medida de fragilidad, no precio exacto"),
    "gamma_max": "maxima concentracion de gamma - la reaccion mas fuerte aca",
}


def lectura(nombre, precio, spot):
    """Una linea de que se esperaria del precio ahi. Es INTERPRETACION."""
    if not precio or not spot:
        return ""
    arriba = float(precio) > float(spot)
    if nombre == "vanna_wall":
        return ("si la vol se desinfla, el dealer empuja hacia aca"
                if arriba else "si la vol se desinfla, presion de venta aca")
    if nombre == "delta_wall":
        return ("romperlo obliga a re-cubrir delta en masa"
                if arriba else "perderlo obliga a vender cobertura")
    return _LECTURAS.get(nombre, "")
