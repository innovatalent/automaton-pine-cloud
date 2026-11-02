# -*- coding: utf-8 -*-
"""SESGO POR DÍA DE SEMANA (usuario 2026-09-03, estudio propio ~57k sesiones Yahoo, t-stats + sub-períodos). Aplica a
TQQQ/NDX/acciones, ORO (XAU) y CRUDO (CL) — NO a cripto (el estudio no la incluye). Es un FILTRO DE SESGO ("qué días no
pelear contra la corriente"), NO un gatillo: el edge es chico (~9-11% de un sigma). Sólo los sesgos ROBUSTOS del RÉGIMEN
ACTUAL (2016-2026), que repiten signo en los sub-períodos (el usuario descartó los que no). Horario NY (lun=0..vie=4).

Devuelve (bias LONG/SHORT/None, fuerza 1-2, nota). fuerza 2 = claro (|t|≥2 + sub-períodos) · 1 = débil (candidato)."""

# día NY (0=lun..4=vie) → clase de activo → (bias, fuerza, nota)
_DOW = {
    0: {"equity": ("LONG", 2, "lunes = mejor día del régimen actual (TQQQ/ES sesión +, últ. 2a ES 70% WR)"),
        "crude": ("SHORT", 1, "lunes crudo bajista consistente (venta en sesión, 3 sub-períodos)"),
        "gold": (None, 0, "lunes oro neutral (gap alcista pero sesión plana)")},
    1: {"equity": (None, 0, "martes plano (el edge está en el gap, no en la sesión)"),
        "crude": (None, 0, "martes crudo neutral"), "gold": (None, 0, "martes oro neutral (su peor día)")},
    2: {"equity": ("LONG", 2, "miércoles = el día más robusto (positivo en 40-98 años, todos los sub-períodos)"),
        "gold": ("LONG", 1, "miércoles oro long débil (su mejor día de los últ. 10 años)"),
        "crude": (None, 0, "miércoles crudo neutral")},
    3: {"equity": (None, 0, "jueves flojo pero NO short estadístico (neutral)"),
        "crude": ("LONG", 1, "jueves crudo long débil (su mejor día reciente +0.15%/55%)"),
        "gold": (None, 0, "jueves oro neutral")},
    4: {"equity": (None, 0, "viernes = el más flojo del equity (t=−2.1 vs resto en NDX, NO es día long)"),
        "gold": ("LONG", 2, "viernes = ÚNICO día con sesgo claro del oro (+0.10%, 55.5%, t=2.9, 3 sub-períodos)"),
        "crude": ("LONG", 1, "viernes crudo (más 'no shortear' que long fuerte)")},
}

_EQUITY = {"TQQQ", "NVDA", "TSLA", "AAPL", "META", "MSTR", "COIN", "SOXL", "SNDK", "PLTR", "AMD", "GOOGL", "AMZN",
           "MSFT", "HOOD", "MU", "NFLX", "AVGO", "SKHYNIX"}
_GOLD = {"XAU", "XAG", "GOLD", "SILVER"}
_CRUDE = {"CL", "WTI", "OIL", "USOIL", "UKOIL"}


# AJUSTE POR LADO (usuario 2026-09-04 "restale peso a los longs los viernes para no forzarlos"): NO es un sesgo SHORT (el
# estudio no lo respalda), es quitarle peso al LONG de equity el día más flojo. Delta de confianza (puntos), chico como el edge.
# Tabla completa por DÍA NY × CLASE × LADO (usuario 2026-09-04 "haz lo mismo el resto de días, longs o shorts por separado").
# Escala chica (±2..±6 puntos de conf, como el edge del estudio): +6/+3 = día claro/débil a favor; −3/−2 = no forzar ese lado.
_ADJ = {
    0: {"equity": {"LONG": (+6, "lunes: mejor día del equity (TQQQ/ES sesión +, 70% WR) → LONG con más peso"),
                   "SHORT": (-3, "lunes: día alcista del equity → no forzar SHORT")},
        "btc": {"LONG": (+3, "lunes: BTC long débil (+0.43%/día, t=2.1, 3 sub-períodos +) → LONG leve"),
                "SHORT": (-2, "lunes: BTC con sesgo alcista débil → no forzar SHORT")},
        "crude": {"SHORT": (+3, "lunes: crudo bajista consistente (venta en sesión) → SHORT con más peso"),
                  "LONG": (-2, "lunes: crudo bajista → no forzar LONG")},
        "gold": {}},
    1: {"equity": {}, "crude": {},
        "gold": {"LONG": (-2, "martes: peor día del oro → no forzar LONG")}},
    2: {"equity": {"LONG": (+6, "miércoles: día más robusto del equity (positivo en todos los sub-períodos) → LONG con más peso"),
                   "SHORT": (-3, "miércoles: día alcista del equity → no forzar SHORT")},
        "crypto_alt": {"LONG": (+2, "miércoles: alts positivas en los 3 sub-períodos (canasta +0.40%/día) → LONG leve")},
        "btc": {"LONG": (+6, "miércoles: mejor día de BTC (+0.52%/día, t=2.7, 3 sub-períodos +) → LONG con más peso"),
                "SHORT": (-3, "miércoles: día alcista de BTC → no forzar SHORT")},
        "gold": {"LONG": (+3, "miércoles: oro long débil (su mejor día de los últ. 10 años)"),
                 "SHORT": (-2, "miércoles: oro con sesgo alcista débil → no forzar SHORT")},
        "crude": {}},
    3: {"equity": {"LONG": (-2, "jueves: día flojo del equity (sin sesgo short) → LONG con menos peso")},
        "crude": {"LONG": (+3, "jueves: crudo long débil (su mejor día reciente +0.15%, 55%)"),
                  "SHORT": (-2, "jueves: crudo con sesgo alcista débil → no forzar SHORT")},
        "gold": {}},
    # CRIPTO (estudio propio 2026-09-04, Bybit D 2020-2026, 17 alts + BTC; método idéntico al de equity):
    #  · BTC: MIÉRCOLES LONG fuerza 2 (+0.52%/día, WR 52.5%, t=2.74, vs resto 2.13, 3 sub-períodos +) y LUNES LONG fuerza 1
    #    (+0.43%, t=2.10, vs resto 1.52, 3 sub-períodos +) = el mismo patrón lunes/miércoles del equity (BTC sigue al NDX).
    #  · ALTS (canasta 17): ningún día pasa el filtro fuerte; lo consistente TRANSVERSALMENTE (3 sub-períodos + en 11-12 de 17
    #    alts): MIÉRCOLES (+0.40%/día) y SÁBADO (+0.34%, WR 59%) → sesgo DÉBIL +2 sólo LONG. Jueves/domingo flojos pero no robustos.
    #  · Aciertos individuales (TRX lunes short, DOGE sábado long) = ruido esperable con 126 tests → no se usan.
    4: {"equity": {"LONG": (-3, "viernes: día más flojo del equity (t=−2.1, no es día long) → LONG con menos peso")},
        "gold": {"LONG": (+6, "viernes: único día con sesgo claro del oro (+0.10%, 55.5%, t=2.9) → LONG con más peso"),
                 "SHORT": (-3, "viernes: oro alcista → no forzar SHORT")},
        "crude": {"LONG": (+2, "viernes: crudo 'no shortear' más que long fuerte → LONG leve"),
                  "SHORT": (-3, "viernes: crudo → no forzar SHORT")}},
    5: {"crypto_alt": {"LONG": (+2, "sábado: alts positivas en los 3 sub-períodos (canasta +0.34%/día, WR 59%) → LONG leve")}},
}


def adjust_for(symbol, side):
    """(delta_conf, nota) por día NY + clase de activo + LADO. 0 si no aplica."""
    try:
        cls = _cls(symbol)
        d = _ADJ.get(_ny_weekday(), {}).get(cls, {}).get(str(side or "").upper())
        return (d[0], d[1]) if d else (0, None)
    except Exception:
        return (0, None)


def _ny_weekday():
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("America/New_York")).weekday()
    except Exception:
        return _dt.datetime.utcnow().weekday()


def _cls(symbol):
    s = symbol.replace("USDT", "").replace(".P", "")
    if s in _EQUITY:
        return "equity"
    if s in _GOLD:
        return "gold"
    if s in _CRUDE:
        return "crude"
    if s == "BTC":
        return "btc"                             # estudio cripto 2026-09-04 (dow_study_crypto.py): BTC mié LONG f2, lun LONG f1
    if symbol.upper().endswith("USDT") or symbol.upper().endswith(".P"):
        return "crypto_alt"                      # alts: sólo sesgos débiles transversales (mié y sáb LONG), ver _ADJ
    return None


def bias_for(symbol):
    """(bias LONG/SHORT/None, fuerza 1-2, nota). None para cripto o días neutrales. Filtro de sesgo, no gatillo."""
    cls = _cls(symbol)
    if not cls:
        return (None, 0, None)
    wd = _ny_weekday()
    if wd > 4:                                    # fin de semana: sin sesgo (mercados cerrados)
        return (None, 0, None)
    b = _DOW.get(wd, {}).get(cls)
    if not b or not b[0]:
        return (None, 0, (b[2] if b else None))
    return b


def today_summary():
    """Resumen del sesgo de hoy por clase (para la cabecera del panel). {day, weekday, biases:{equity/gold/crude:(bias,fuerza,nota)}}."""
    wd = _ny_weekday()
    day = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][wd]
    if wd > 4:
        return {"day": day, "weekday": wd, "biases": {}}
    return {"day": day, "weekday": wd, "biases": {c: _DOW.get(wd, {}).get(c) for c in ("equity", "gold", "crude")}}


if __name__ == "__main__":
    import datetime as _dt
    _d = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"][_ny_weekday()]
    print("=== SESGO DÍA DE SEMANA (hoy NY = %s) ===" % _d)
    for s in ["TQQQUSDT", "NVDAUSDT", "XAUUSDT", "CLUSDT", "ETHUSDT"]:
        print("  %-8s ->" % s.replace("USDT", ""), bias_for(s))
