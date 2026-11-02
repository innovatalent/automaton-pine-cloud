# -*- coding: utf-8 -*-
"""SCORE DE CONVICCIÓN (2026-08-07, pedido usuario: "máximo %/trade, pocos y grandes, mixto por convicción").

Combina TODO lo VALIDADO esta sesión en un score 0..5. Solo opera cuando hay VARIAS señales alineadas (selectivo ->
pocos trades, mayor calidad -> sube el %/trade). Alta convicción = swing (dejar correr); baja = no operar.

Componentes (cada uno validado):
  +2  HTF-fade en el lado favorecido y en régimen BLOWOFF (+0.52% WR57%) / +1 si solo favorecido
  +1  activo MOVER (no dormido -> las entradas van a algún lado, no mueren)
  +1  celda (arm×activo) GANADORA probada (no solo no-bloqueada)
  +1  imán de LIQUIDEZ alineado en la dirección del trade (hay a dónde ir)

blocks(symbol, side, arm, entry) -> (True/False, motivo). is_swing(...) -> alta convicción (dejar correr).
Fail-open por componente. Reversible con MIN_CONV.
"""
MIN_CONV = 2      # 2026-08-08: PROBÉ 3 en vivo y bloqueaba ~95% de entradas no-frozen (casi todo puntúa "1/5: mover") =
                  # apagaba value/mover en vez de mejorarlos. Vuelvo a 2 (filtra el junk de 1-sola-señal). La mejora de gatillo
                  # "ir sobre seguro" va por la CONFIRMACIÓN DE GIRO en value (no cachetear cuchillo), no por subir este umbral.
SWING_CONV = 4    # >=4 = convicción máxima -> swing (TP ancho / dejar correr). Resto = intradía (TP en liquidez).


def score(symbol, side, arm, entry):
    s = 0
    parts = []
    # 1. HTF-fade (dirección validada)
    try:
        import htf_bias as _h
        d = _h.favored_dir(symbol)
        if d.get("favored") == side:
            if "blowoff" in str(d.get("regime")):
                s += 2; parts.append("HTF-blowoff")
            else:
                s += 1; parts.append("HTF-fade")
    except Exception:
        pass
    # 2/3. vitalidad + celda ganadora (selección)
    try:
        import arm_cell_selector as _a
        dat = _a._load()
        if symbol not in (dat.get("dormant") or []):
            s += 1; parts.append("mover")
        if symbol in ((dat.get("arms") or {}).get(arm, {}).get("assets_allow") or []):
            s += 1; parts.append("celda+")
    except Exception:
        pass
    # 4. liquidez alineada (hay imán a dónde ir en la dirección del trade)
    try:
        import bybit_exec as _b
        if _b._liq_tp_pct(symbol, side, entry) is not None:
            s += 1; parts.append("liq")
    except Exception:
        pass
    return s, parts


def blocks(symbol, side, arm, entry):
    """True si la convicción es BAJA (<MIN_CONV) -> NO operar (ser selectivo sube el %/trade)."""
    try:
        s, parts = score(symbol, side, arm, entry)
    except Exception:
        return False, ""
    if s < MIN_CONV:
        return True, "convicción baja (%d/5: %s)" % (s, ",".join(parts) or "ninguna señal")
    return False, ""


def is_swing(symbol, side, arm, entry):
    """True si la convicción es máxima -> dejar correr (swing), no cerrar temprano."""
    try:
        s, _ = score(symbol, side, arm, entry)
        return s >= SWING_CONV
    except Exception:
        return False
