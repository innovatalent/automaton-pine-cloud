# -*- coding: utf-8 -*-
"""Filtro de CALIDAD por MFE-LIMPIO del activo (2026-08-21, usuario "medir el MFE max limpio desde la entrada en TODAS
las estrategias/alertas y eliminar los activos que no valen"). Medido por bt_asset_mfe.py (momentum LONG+SHORT, MFE hacia
+1% desde la entrada): BLOCK = activos con MFEprom < 1.0% = poco movimiento LIMPIO -> NO operar en NINGUN brazo ni alertar.
Es por ACTIVO (simetrico A/random, no sesga la muralla). Fail-open para simbolos no medidos. Reversible: vaciar BLOCK.
Regenerar: correr bt_asset_mfe.py y actualizar la lista."""

# CRITERIO (2026-08-21, usuario "cambia a mas de 60% de WR"): keep solo WR>=60% + E>0 + MFEprom>=1% (medido bt_asset_mfe,
# salida trailing). El WR>=70% no existe con E+ (techo real ~69%, ver [[feedback_mfe_wr70]]) -> se acordo bar WR>=60.
# BLOCK = todo lo medido que NO cumple. KEEP (complemento): cripto ENA/LDO/ORDI/NEAR/INJ/WLD/STX/APT/GALA/OP/ARB + ETFs TQQQ/SOXL.
BLOCK = {
    # cripto WR<60 (medido n~60-77; incluye majors BTC40/ETH48/SOL55 que se mueven poco % limpio)
    "AAVE", "TAO", "FIL", "SOL", "ADA", "ATOM", "DOT", "SEI", "LINK", "IMX", "CRV", "XRP", "WIF", "SUI",
    "ETH", "AVAX", "JUP", "RUNE", "DOGE", "LTC", "TIA", "BTC", "TRX", "TON",
    # acciones (WR<60 o muestra fina no confiable)
    "NVDA", "TSLA", "META", "MSFT", "GOOGL", "AMZN", "AAPL", "NFLX", "AVGO", "PLTR",
    "COIN", "MSTR", "SMCI", "MU", "AMD", "HOOD",
    # commodities (WR<60, MFE<1)
    "CL", "NATGAS", "BZ", "XAU", "XAG", "USO",
    # ETFs 1x / inverso (poco MFE/WR vs el 3x)
    "QQQ", "SPY", "IWM", "DIA", "SMH", "SQQQ",
}


# WHITELIST estricto para ALERTAS (voz/telegram): SOLO los medidos con WR>=60 + E>0 + MFE>=1 (2026-08-21, usuario
# "unifica las alertas, algunas suenan y no cumplen"). Las alertas NO son fail-open (a diferencia del trading): si no
# esta validado, NO suena. Trading usa mfe_ok (blocklist, fail-open para movers nuevos); alertas usan alert_ok (whitelist).
ALLOW = {
    "ENA", "LDO", "ORDI", "NEAR", "INJ", "WLD", "STX", "APT", "GALA", "OP", "ARB",   # cripto WR>=60
    "TQQQ", "SOXL",   # ETF 3x indice/sector
}


def _base(sym):
    s = str(sym).upper().strip()
    if ":" in s:
        s = s.split(":")[-1]
    for suf in (".P", "-PERP", "PERP"):
        if s.endswith(suf):
            s = s[:-len(suf)]
    if s.endswith("USDT"):
        s = s[:-4]
    elif s.endswith("USD"):
        s = s[:-3]
    return s


def mfe_ok(sym):
    """TRADING: True si el activo no esta en BLOCK (fail-open para movers no medidos)."""
    try:
        return _base(sym) not in BLOCK
    except Exception:
        return True


def alert_ok(sym):
    """ALERTAS voz/telegram: True SOLO si el activo esta en el whitelist validado (WR>=60). NO fail-open."""
    try:
        return _base(sym) in ALLOW
    except Exception:
        return False
