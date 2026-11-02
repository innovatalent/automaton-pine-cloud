"""
gex_filter.py — Capa de validación de señales contra la estructura de opciones.

No genera señales. Recibe una señal ya generada (OB 3D Confluence en 1m/5m, o el
panel PERFIL DE VOLUMEN + VWAP EXTREMOS) y responde una sola pregunta:

    ¿la estructura de gamma va a EMPUJAR el precio hacia el TP, lo va a FRENAR,
    o es indiferente?

Premisa mecánica
────────────────
GEX POSITIVO  -> los dealers venden fuerza y compran debilidad.
                 El precio se pega a los strikes. Vol realizada comprimida.
                 => FAVORECE reversión a la media (POC/VWAP). CASTIGA rupturas.

GEX NEGATIVO  -> los dealers compran fuerza y venden debilidad.
                 El precio se acelera. Vol realizada expandida.
                 => FAVORECE barridos de liquidez y continuación.
                    MATA el fade de extremos: un ±2.5σ en gamma negativa
                    no revierte, se extiende.

El GEX no cambia minuto a minuto. Lo que cambia en 1m/5m es la POSICIÓN DEL
PRECIO dentro de esa estructura fija. Por eso este módulo es un filtro de
régimen y de camino, no un generador de entradas.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal, Sequence

from gex_engine import GexState, StrikeLevel

TipoSenal = Literal["REVERSION", "CONTINUACION"]
Lado = Literal["LONG", "SHORT"]

# ─────────────────────────────────────────────────────────────────────────────
# UMBRALES  (todos configurables — están acá para que se puedan calibrar)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Umbrales:
    # Un strike es "muro" si su |GEX| supera esta fracción del mayor |GEX|.
    muro_frac: float = 0.45
    # Banda alrededor del flip donde el régimen es indefinido (%).
    banda_flip_pct: float = 0.50
    # Régimen "profundo": lejos del flip, el sesgo es fuerte (%).
    lejos_flip_pct: float = 1.50
    # Tolerancia para considerar que el TP coincide con un imán (%).
    tol_iman_pct: float = 0.35
    # Horas antes de un vencimiento grande donde domina el pinning.
    horas_pin: float = 12.0
    # Fracción del |GEX| total que hace "grande" a un vencimiento.
    share_vto_grande: float = 0.25
    # Cortes de veredicto.
    corte_ejecutar: int = 70
    corte_reducir: int = 45


@dataclass
class Veredicto:
    veredicto: str                 # EJECUTAR | REDUCIR | VETAR
    score: int                     # 0-100
    velocidad_esperada: str        # PINNING | VIOLENTA | BISAGRA | NORMAL
    regimen: str
    tipo_senal: str
    lado: str

    entrada: float
    tp_original: float
    tp_sugerido: float             # ajustado al primer muro en el camino
    sl_sugerido: float | None

    gamma_flip: float | None
    dist_flip_pct: float | None
    muro_en_camino: dict | None
    muro_en_tp: dict | None
    cruza_flip: bool
    recorrido_pct: float

    motivos: list[str] = field(default_factory=list)
    penalizaciones: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _muros(state: GexState, u: Umbrales) -> list[StrikeLevel]:
    if not state.ladder:
        return []
    mx = max(l.abs_gex for l in state.ladder)
    if mx <= 0:
        return []
    return [l for l in state.ladder if l.abs_gex >= u.muro_frac * mx]


def _entre(a: float, b: float, x: float) -> bool:
    lo, hi = (a, b) if a <= b else (b, a)
    return lo < x < hi


def _lvl_dict(l: StrikeLevel, spot: float) -> dict:
    return {
        "strike": l.strike,
        "net_gex_musd": round(l.net_gex / 1e6, 2),
        "abs_gex_musd": round(l.abs_gex / 1e6, 2),
        "tipo": "ESTABILIZADOR" if l.net_gex > 0 else "ACELERADOR",
        "dist_pct": round((l.strike - spot) / spot * 100.0, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# VALIDADOR
# ─────────────────────────────────────────────────────────────────────────────

def validar(state: GexState,
            lado: Lado,
            entrada: float,
            tp: float,
            sl: float | None = None,
            tipo: TipoSenal = "REVERSION",
            u: Umbrales | None = None) -> Veredicto:
    """
    tipo = "REVERSION"    -> el panel VWAP EXTREMOS (fade de ±2.5σ / fuera del
                             value area, objetivo POC/VWAP) y los rebotes de OB.
    tipo = "CONTINUACION" -> barridos de liquidez, rupturas con desplazamiento,
                             el IFVG/BPR como gatillo de expansión.
    """
    u = u or Umbrales()
    spot = state.spot
    motivos: list[str] = []
    penas: list[str] = []
    score = 50

    dir_up = tp > entrada
    if (lado == "LONG") != dir_up:
        penas.append(f"Incoherencia: {lado} con TP del lado contrario a la entrada.")
        score -= 25

    recorrido = abs(tp - entrada) / entrada * 100.0
    flip = state.gamma_flip
    dflip = state.dist_flip_pct

    # ── 1. RÉGIMEN ──────────────────────────────────────────────────────────
    cerca_flip = dflip is not None and abs(dflip) <= u.banda_flip_pct
    lejos_flip = dflip is not None and abs(dflip) >= u.lejos_flip_pct
    pos = state.regimen == "POSITIVO"

    if cerca_flip:
        score += 5 if tipo == "REVERSION" else 12
        motivos.append(
            f"Precio a {dflip:+.2f}% del gamma flip ({flip:,.0f}): zona de "
            f"transición, el régimen puede darse vuelta dentro del trade.")
    elif tipo == "REVERSION":
        if pos:
            score += 28 if lejos_flip else 16
            motivos.append(
                f"Gamma positiva{' profunda' if lejos_flip else ''} "
                f"({state.net_gex_musd:+,.1f} M$/1%): los dealers venden los "
                f"impulsos y compran las caídas. El fade hacia POC/VWAP tiene "
                f"el flujo de cobertura a favor.")
        else:
            score -= 38
            ubic = (f", {abs(dflip):.2f}% del lado negativo del flip"
                    if dflip is not None else ", sin flip dentro del rango")
            penas.append(
                f"Gamma NEGATIVA ({state.net_gex_musd:+,.1f} M$/1%){ubic}. Los "
                f"dealers amplifican: un extremo de 2.5σ acá no revierte, se "
                f"extiende. Es el escenario que rompe el panel de extremos.")
    else:  # CONTINUACION
        if not pos:
            score += 28 if lejos_flip else 16
            motivos.append(
                f"Gamma negativa: la cobertura de dealers es procíclica. El "
                f"barrido hacia {tp:,.0f} tiene aceleración estructural detrás.")
        else:
            score -= 32
            penas.append(
                f"Gamma positiva ({state.net_gex_musd:+,.1f} M$/1%): cada "
                f"impulso se vende contra vos. La ruptura tiende a morir antes "
                f"del TP.")

    # ── 2. MUROS EN EL CAMINO ───────────────────────────────────────────────
    muros = _muros(state, u)
    # Un muro que cae PRACTICAMENTE SOBRE el TP no es un obstaculo en el camino:
    # es el destino. Sin esta exclusion el mismo strike se penalizaba tres veces
    # (muro en el camino -22, iman en el TP +12, TP mas alla del wall -15) por un
    # unico hecho estructural. Detectado con la cadena real de BTC 2026-09-07:
    # spot 79.107 / TP 81.085 / call wall 81.000.
    en_camino = [l for l in muros
                 if _entre(entrada, tp, l.strike)
                 and abs(l.strike - tp) / tp * 100.0 > u.tol_iman_pct]
    tp_sug = tp
    muro_camino = None

    if en_camino:
        # el más cercano a la entrada es el que frena primero
        primero = min(en_camino, key=lambda l: abs(l.strike - entrada))
        muro_camino = _lvl_dict(primero, spot)
        if primero.net_gex > 0:
            score -= 22
            tp_sug = primero.strike
            penas.append(
                f"Muro estabilizador en {primero.strike:,.0f} "
                f"({primero.net_gex/1e6:+,.1f} M$/1%) entre la entrada y el TP. "
                f"Ahí los dealers frenan el movimiento. TP1 = {primero.strike:,.0f}.")
        else:
            score += 10
            motivos.append(
                f"Strike acelerador en {primero.strike:,.0f} en el camino: "
                f"gamma negativa concentrada, el precio pasa de largo.")
    else:
        score += 14
        _hay_muro_tp = any(abs(l.strike - tp) / tp * 100.0 <= u.tol_iman_pct
                           for l in muros)
        motivos.append(
            "Camino libre hasta el objetivo: la concentración de gamma está EN "
            "el TP, no antes." if _hay_muro_tp else
            "Camino libre: no hay concentración de gamma entre la entrada y el "
            "objetivo.")

    # ── 3. EL TP COMO IMÁN O COMO PARED ─────────────────────────────────────
    muro_tp = None
    cerca_tp = [l for l in muros
                if abs(l.strike - tp) / tp * 100.0 <= u.tol_iman_pct]
    if cerca_tp:
        l = max(cerca_tp, key=lambda x: x.abs_gex)
        muro_tp = _lvl_dict(l, spot)
        if l.net_gex > 0:
            score += 12
            motivos.append(
                f"El TP {tp:,.0f} coincide con un imán de gamma positiva "
                f"({l.strike:,.0f}). Es un objetivo estructural, no arbitrario.")
        else:
            score -= 6
            penas.append(f"El TP cae sobre un strike acelerador "
                         f"({l.strike:,.0f}): el precio tiende a atravesarlo, "
                         f"no a detenerse. Considerar TP dinámico.")

    # TP mas alla del muro principal en esa direccion. Se exige que el TP este
    # MAS ALLA por un margen real: si el TP coincide con el wall (dentro de la
    # tolerancia de iman) el objetivo ES el muro y ya se puntuo arriba.
    _pasa_wall = lambda w: abs(tp - w) / tp * 100.0 > u.tol_iman_pct
    if lado == "LONG" and state.call_wall and tp > state.call_wall and _pasa_wall(state.call_wall):
        score -= 15
        penas.append(f"TP por encima del call wall ({state.call_wall:,.0f}). "
                     f"Estructuralmente caro llegar ahí en una sesión.")
    if lado == "SHORT" and state.put_wall and tp < state.put_wall and _pasa_wall(state.put_wall):
        score -= 15
        penas.append(f"TP por debajo del put wall ({state.put_wall:,.0f}). "
                     f"Ese nivel absorbe el flujo antes que tu objetivo.")

    # ── 4. CRUCE DEL FLIP DENTRO DEL TRADE ──────────────────────────────────
    cruza = bool(flip and _entre(entrada, tp, flip))
    if cruza:
        if tipo == "CONTINUACION":
            score += 12
            motivos.append(
                f"El trade cruza el gamma flip ({flip:,.0f}). Es el punto de "
                f"ignición: el régimen cambia a favor del movimiento.")
        else:
            score -= 12
            penas.append(
                f"El trade cruza el gamma flip ({flip:,.0f}) hacia el régimen "
                f"contrario. La reversión pierde el soporte de cobertura a "
                f"mitad de camino.")

    # ── 5. VENCIMIENTO PRÓXIMO ──────────────────────────────────────────────
    if (state.proximo_vto_horas is not None
            and state.proximo_vto_share_abs is not None
            and state.proximo_vto_horas <= u.horas_pin
            and state.proximo_vto_share_abs >= u.share_vto_grande):
        h = state.proximo_vto_horas
        sh = state.proximo_vto_share_abs
        if tipo == "REVERSION":
            score += 10
            motivos.append(
                f"Vencimiento en {h:.1f} h con {sh:.0%} del |GEX| total: "
                f"régimen de pinning hacia {state.max_abs_strike:,.0f}. "
                f"Favorece el retorno a la media.")
        else:
            score -= 15
            penas.append(
                f"Vencimiento en {h:.1f} h con {sh:.0%} del |GEX|: hasta las "
                f"08:00 UTC el precio queda clavado. La expansión, si llega, "
                f"es DESPUÉS del vencimiento.")

    # ── 6. RECORRIDO vs GAMMA DISPONIBLE ────────────────────────────────────
    if recorrido > 3.0 and state.velocidad_esperada == "PINNING":
        score -= 10
        penas.append(f"Objetivo de {recorrido:.2f}% en régimen de compresión: "
                     f"el recorrido es grande para la vol que la estructura "
                     f"permite hoy.")

    # ── SALIDA ──────────────────────────────────────────────────────────────
    score = max(0, min(100, score))
    if score >= u.corte_ejecutar:
        v = "EJECUTAR"
    elif score >= u.corte_reducir:
        v = "REDUCIR"
    else:
        v = "VETAR"

    # SL sugerido: al otro lado del muro más cercano en contra.
    sl_sug = sl
    if sl is None:
        if lado == "LONG":
            contra = [l for l in muros if l.strike < entrada]
            ref = max(contra, key=lambda l: l.strike) if contra else None
            sl_sug = ref.strike * 0.999 if ref else None
        else:
            contra = [l for l in muros if l.strike > entrada]
            ref = min(contra, key=lambda l: l.strike) if contra else None
            sl_sug = ref.strike * 1.001 if ref else None

    return Veredicto(
        veredicto=v, score=score,
        velocidad_esperada=state.velocidad_esperada,
        regimen=state.regimen, tipo_senal=tipo, lado=lado,
        entrada=entrada, tp_original=tp, tp_sugerido=tp_sug, sl_sugerido=sl_sug,
        gamma_flip=flip, dist_flip_pct=dflip,
        muro_en_camino=muro_camino, muro_en_tp=muro_tp,
        cruza_flip=cruza, recorrido_pct=round(recorrido, 3),
        motivos=motivos, penalizaciones=penas,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ATAJO PARA EL PANEL DE EXTREMOS
# ─────────────────────────────────────────────────────────────────────────────

def validar_extremo_vwap(state: GexState, *, precio: float, poc: float,
                         vwap: float, sigma_dist: float,
                         delta_signo: int) -> Veredicto:
    """
    Adaptador directo del panel PERFIL DE VOLUMEN + VWAP 24h · EXTREMOS.

    sigma_dist  : distancia al VWAP en sigmas (+2.7 = arriba, -2.7 = abajo)
    delta_signo : +1 delta comprador, -1 delta vendedor
    El objetivo del panel es POC o VWAP, el que quede más cerca en esa dirección.
    """
    if sigma_dist > 0 and delta_signo < 0:
        lado: Lado = "SHORT"
    elif sigma_dist < 0 and delta_signo > 0:
        lado = "LONG"
    else:
        lado = "SHORT" if sigma_dist > 0 else "LONG"

    candidatos = [poc, vwap]
    if lado == "SHORT":
        objetivos = [c for c in candidatos if c < precio] or candidatos
        tp = max(objetivos)
    else:
        objetivos = [c for c in candidatos if c > precio] or candidatos
        tp = min(objetivos)

    return validar(state, lado=lado, entrada=precio, tp=tp, tipo="REVERSION")
