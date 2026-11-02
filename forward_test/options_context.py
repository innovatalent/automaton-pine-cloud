# -*- coding: utf-8 -*-
"""__OPTIONS_CONTEXT_2026_09_23__ — capa institucional de opciones para la TORTUGA.

QUE ES
    El objeto unificado `OptionsMarketContext` (spec del usuario §2) construido COMPONIENDO
    el motor GEX ya existente y VALIDADO (`gex_engine.build_state`), al que le agrega lo que
    faltaba: **Max Pain para cripto**, distancias a cada nivel, densidad de niveles, buckets
    multi-vencimiento y el estado de frescura de los datos (fail-safe §23).

    NO genera señales ni decide nada. Es CONTEXTO. La puntuación (si una ruptura de la Tortuga
    entra en un régimen que favorece la continuación) vive en `options_score.py`, y qué features
    aportan de verdad lo decide el backtest/ablation, no este módulo.

POLARIDAD (Tortuga de Richard Dennis = breakout/continuación, NO Turtle Soup de fade):
    - gamma NEGATIVA  → dealers amplifican → favorece que la RUPTURA CONTINÚE.
    - gamma POSITIVA  → pinning/mean-reversion → penaliza la ruptura (falsa ruptura probable).
    Es CONTEXTO mecánico, NO edge medido. En cripto ya se refutaron como señal suelta el
    gamma-flip-como-régimen, las zonas gamma como soporte y walls/maxpain como señal. Acá se
    calculan bien y se dejan que el backtest condicional decida. Ver biblioteca 16/gamma.

SIN LOOKAHEAD (spec §15)
    `build_context(rows=..., now=...)` acepta la cadena histórica reconstruida y un timestamp
    explícito: todo se calcula SOLO con `expiry_ts`/OI disponibles a ese `now`. Nunca se usa la
    cadena actual para reconstruir un contexto pasado. Para vivo, `build_context(coin=...)`
    descarga la cadena y sella el `ts` de la descarga.

FÓRMULAS (auditables, spec §26)
    Max Pain(expiry) = strike K que MINIMIZA el valor intrínseco total que cobran los tenedores:
        pain(K) = Σ_s call_oi[s]·max(0, K−s) + Σ_s put_oi[s]·max(0, s−K)
        max_pain = argmin_K pain(K)      (K recorre los strikes listados de ese vencimiento)
    Put/Call Ratio = total_put_oi / total_call_oi   (del vencimiento medido).
    distancia_a_X_pct = (X − spot)/spot · 100        (signo: + arriba del spot).
    call/put wall (gamma) = strike de mayor GEX+ arriba / menor GEX− abajo (del motor GEX).
    wall_strength = |GEX del muro| / |GEX| total del ladder   ∈ [0,1].
    option_level_density = # de strikes con OI relevante dentro de ±BAND_PCT del spot.

USO
    from options_context import build_context
    ctx = build_context("BTC")                       # vivo
    ctx = build_context(rows=filas, now=ts_epoch)     # histórico, sin lookahead
    ctx.as_dict()                                     # para /api, tv_pine_full, logging
"""
from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gex_engine as _ge  # noqa: E402  (motor GEX validado; se compone, no se reimplementa)

# Buckets de vencimiento (spec §11): el más próximo domina el intradía, los largos son macro.
BUCKETS = (("0-2d", 0.0, 2.0), ("3-7d", 2.0, 7.0),
           ("8-30d", 7.0, 30.0), (">30d", 30.0, float("inf")))
BAND_PCT = 3.0            # ventana ±% para densidad de niveles (heurística inicial, §10)
OI_REL_FRAC = 0.05        # un strike "cuenta" si su OI ≥ 5% del OI del strike más cargado
STALE_SEC = 3600.0        # datos de opciones más viejos que esto → STALE (§23)
NEUTRAL_MUSD = 5.0        # |net GEX| por debajo (en M USD) → régimen NEUTRAL


# ─────────────────────────────────────────────────────────────────────────────
#  MAX PAIN — el que faltaba para cripto (stocks ya lo tienen en stock_liquidity)
# ─────────────────────────────────────────────────────────────────────────────
def _oi_por_strike(rows: Sequence["_ge.OptionRow"]):
    """Devuelve (strikes ordenados, call_oi{K}, put_oi{K}) sumando OI por strike."""
    call_oi: dict[float, float] = {}
    put_oi: dict[float, float] = {}
    for r in rows:
        d = call_oi if r.is_call else put_oi
        d[r.strike] = d.get(r.strike, 0.0) + float(r.oi_coin)
    strikes = sorted(set(call_oi) | set(put_oi))
    return strikes, call_oi, put_oi


def max_pain(rows: Sequence["_ge.OptionRow"]):
    """Max Pain de UN conjunto de filas (se asume un solo vencimiento; filtrar antes).

    Devuelve dict {max_pain, total_call_oi, total_put_oi, put_call_ratio} o None si no hay OI.
    """
    strikes, call_oi, put_oi = _oi_por_strike(rows)
    if not strikes:
        return None
    tot_call = sum(call_oi.values())
    tot_put = sum(put_oi.values())
    if tot_call <= 0 and tot_put <= 0:
        return None
    best_k, best_pain = None, None
    for K in strikes:
        pain = 0.0
        for s in strikes:
            if s < K:
                pain += call_oi.get(s, 0.0) * (K - s)     # calls ITM si expira en K
            elif s > K:
                pain += put_oi.get(s, 0.0) * (s - K)      # puts ITM si expira en K
        if best_pain is None or pain < best_pain:
            best_pain, best_k = pain, K
    return {
        "max_pain": best_k,
        "total_call_oi": tot_call,
        "total_put_oi": tot_put,
        "put_call_ratio": (tot_put / tot_call) if tot_call > 0 else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  OBJETO UNIFICADO
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class OptionsMarketContext:
    coin: str
    spot_price: Optional[float]
    ts: float                                   # sello del snapshot (epoch UTC)
    data_status: str                            # OK | STALE | INCOMPLETE | ERROR
    age_sec: Optional[float] = None
    source: Optional[str] = None                # exchanges usados

    # vencimiento primario (el más próximo)
    expiry_ts: Optional[float] = None
    expiry_str: Optional[str] = None
    hours_to_expiry: Optional[float] = None
    dte: Optional[float] = None

    # max pain + OI del vencimiento primario
    max_pain: Optional[float] = None
    total_call_oi: Optional[float] = None
    total_put_oi: Optional[float] = None
    put_call_ratio: Optional[float] = None

    # estructura gamma (del motor GEX, cadena operativa)
    gamma_flip: Optional[float] = None
    gamma_regime: str = "UNKNOWN"               # POSITIVE_GAMMA | NEGATIVE_GAMMA | NEUTRAL
    net_gex_musd: Optional[float] = None
    call_wall_1: Optional[float] = None
    call_wall_2: Optional[float] = None
    put_wall_1: Optional[float] = None
    put_wall_2: Optional[float] = None
    call_wall_strength: Optional[float] = None
    put_wall_strength: Optional[float] = None
    largest_call_strikes: list = field(default_factory=list)   # [{strike, oi}]
    largest_put_strikes: list = field(default_factory=list)

    # distancias y densidad
    nearest_option_level_above: Optional[float] = None
    nearest_option_level_below: Optional[float] = None
    distance_to_call_wall_pct: Optional[float] = None
    distance_to_put_wall_pct: Optional[float] = None
    distance_to_max_pain_pct: Optional[float] = None
    distance_to_gamma_flip_pct: Optional[float] = None
    option_level_density: Optional[int] = None

    # multi-expiry (spec §11)
    buckets: dict = field(default_factory=dict)

    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _dist_pct(level, spot):
    if level is None or not spot:
        return None
    return round((level - spot) / spot * 100.0, 3)


def _regime(net_gex_musd, spot, zero_zone) -> str:
    if net_gex_musd is None:
        return "UNKNOWN"
    if zero_zone and spot is not None and zero_zone[0] <= spot <= zero_zone[1]:
        return "NEUTRAL"
    if abs(net_gex_musd) < NEUTRAL_MUSD:
        return "NEUTRAL"
    return "POSITIVE_GAMMA" if net_gex_musd >= 0 else "NEGATIVE_GAMMA"


def _second_wall(ladder, spot, above: bool, exclude):
    """Segundo muro de gamma del mismo lado (excluyendo el primario)."""
    cand = [l for l in ladder if (l.strike > spot) == above and l.strike != exclude]
    if not cand:
        return None
    key = (lambda l: l.net_gex) if above else (lambda l: -l.net_gex)
    return max(cand, key=key).strike


def _largest_by_oi(ladder, spot, above: bool, k=3):
    cand = [l for l in ladder if (l.strike > spot) == above]
    cand.sort(key=lambda l: -l.oi_coin)
    return [{"strike": l.strike, "oi": round(l.oi_coin, 2)} for l in cand[:k]]


def _bucket_summary(rows, now, exchanges):
    """Resumen liviano por bucket de vto: max pain (vto más próximo del bucket), walls, régimen."""
    out = {}
    for name, lo, hi in BUCKETS:
        sub = [r for r in rows if lo < (r.expiry_ts - now) / 86400.0 <= hi]
        if not sub:
            out[name] = None
            continue
        d = {"n_contratos": len(sub), "total_oi_coin": round(sum(r.oi_coin for r in sub), 2)}
        # max pain del vencimiento más próximo DENTRO del bucket (max pain es por-vto)
        exps = sorted({r.expiry_ts for r in sub})
        near = [r for r in sub if r.expiry_ts == exps[0]]
        mp = max_pain(near)
        if mp:
            d.update({"max_pain": mp["max_pain"], "put_call_ratio":
                      round(mp["put_call_ratio"], 3) if mp["put_call_ratio"] else None})
        try:
            st = _ge.build_state(rows=sub, now=now, exchanges=exchanges)
            d.update({"gamma_regime": _regime(st.net_gex_musd, st.spot, st.zero_gamma_zone),
                      "gamma_flip": st.gamma_flip, "call_wall": st.call_wall,
                      "put_wall": st.put_wall, "net_gex_musd": round(st.net_gex_musd, 2)})
        except Exception:
            pass
        out[name] = d
    return out


def build_context(coin: str = "BTC",
                  exchanges: Sequence[str] = ("deribit", "bybit", "okx"),
                  rows: Optional[Sequence["_ge.OptionRow"]] = None,
                  now: Optional[float] = None,
                  dte_max: Optional[float] = None,
                  with_buckets: bool = True) -> OptionsMarketContext:
    """Construye el OptionsMarketContext. FAIL-SOFT: nunca levanta; marca data_status.

    - rows/now explícitos → modo HISTÓRICO sin lookahead (backtest).
    - sin rows → descarga la cadena en vivo y sella ts = ahora.
    - dte_max: limita la cadena OPERATIVA para flip/walls (None = estructural).
    """
    _now = now if now is not None else time.time()
    ctx = OptionsMarketContext(coin=coin, spot_price=None, ts=_now, data_status="ERROR")

    # 1) traer la cadena
    try:
        if rows is None:
            all_rows, meta = _ge.build_options(coin, exchanges)
            ctx.source = ",".join(meta[0]) if meta and meta[0] else None
        else:
            all_rows = list(rows)
            ctx.source = ",".join(sorted({r.exchange for r in all_rows})) or None
    except Exception as e:                       # sin datos → ERROR, no explota
        ctx.warnings.append("no se pudo traer la cadena: %s" % e)
        return ctx
    all_rows = [r for r in all_rows if r.expiry_ts > _now]   # solo vencimientos vivos
    if not all_rows:
        ctx.data_status = "INCOMPLETE"
        ctx.warnings.append("cadena vacía o sin vencimientos vivos")
        return ctx

    # 2) estado gamma con el motor validado (cadena operativa)
    try:
        st = _ge.build_state(rows=all_rows, now=_now, exchanges=exchanges, dte_max=dte_max)
    except Exception as e:
        ctx.data_status = "INCOMPLETE"
        ctx.warnings.append("build_state falló: %s" % e)
        return ctx

    spot = st.spot
    ctx.spot_price = spot
    ctx.age_sec = round(time.time() - _now, 1) if now is None else 0.0
    ctx.data_status = "STALE" if (ctx.age_sec and ctx.age_sec > STALE_SEC) else "OK"
    ctx.net_gex_musd = round(st.net_gex_musd, 3)
    ctx.gamma_flip = st.gamma_flip
    ctx.gamma_regime = _regime(st.net_gex_musd, spot, st.zero_gamma_zone)
    ctx.warnings += list(st.warnings)

    # 3) walls (gamma) primario/secundario + fuerza + mayores por OI
    ladder = st.ladder
    absg = sum(abs(l.net_gex) for l in ladder) or 1.0
    ctx.call_wall_1 = st.call_wall
    ctx.put_wall_1 = st.put_wall
    ctx.call_wall_2 = _second_wall(ladder, spot, above=True, exclude=st.call_wall)
    ctx.put_wall_2 = _second_wall(ladder, spot, above=False, exclude=st.put_wall)
    cw = next((l for l in ladder if l.strike == st.call_wall), None)
    pw = next((l for l in ladder if l.strike == st.put_wall), None)
    ctx.call_wall_strength = round(abs(cw.net_gex) / absg, 4) if cw else None
    ctx.put_wall_strength = round(abs(pw.net_gex) / absg, 4) if pw else None
    ctx.largest_call_strikes = _largest_by_oi(ladder, spot, above=True)
    ctx.largest_put_strikes = _largest_by_oi(ladder, spot, above=False)

    # 4) vencimiento primario + max pain de ESE vencimiento
    ctx.expiry_ts = st.proximo_vto_ts
    ctx.hours_to_expiry = round(st.proximo_vto_horas, 2) if st.proximo_vto_horas else None
    if st.proximo_vto_ts:
        ctx.dte = round((st.proximo_vto_ts - _now) / 86400.0, 3)
        ctx.expiry_str = datetime.fromtimestamp(st.proximo_vto_ts, timezone.utc).strftime("%Y-%m-%d")
        near = [r for r in all_rows if r.expiry_ts == st.proximo_vto_ts]
        mp = max_pain(near)
        if mp:
            ctx.max_pain = mp["max_pain"]
            ctx.total_call_oi = round(mp["total_call_oi"], 3)
            ctx.total_put_oi = round(mp["total_put_oi"], 3)
            ctx.put_call_ratio = round(mp["put_call_ratio"], 4) if mp["put_call_ratio"] else None

    # 5) distancias y densidad
    ctx.distance_to_call_wall_pct = _dist_pct(st.call_wall, spot)
    ctx.distance_to_put_wall_pct = _dist_pct(st.put_wall, spot)
    ctx.distance_to_max_pain_pct = _dist_pct(ctx.max_pain, spot)
    ctx.distance_to_gamma_flip_pct = _dist_pct(st.gamma_flip, spot)
    niveles = [x for x in (st.call_wall, st.put_wall, ctx.max_pain, st.gamma_flip,
                           st.max_abs_strike) if x is not None]
    arriba = [x for x in niveles if x > spot]
    abajo = [x for x in niveles if x < spot]
    ctx.nearest_option_level_above = min(arriba) if arriba else None
    ctx.nearest_option_level_below = max(abajo) if abajo else None
    if ladder:
        oi_max = max((l.oi_coin for l in ladder), default=0.0) or 1.0
        lo, hi = spot * (1 - BAND_PCT / 100.0), spot * (1 + BAND_PCT / 100.0)
        ctx.option_level_density = sum(
            1 for l in ladder if lo <= l.strike <= hi and l.oi_coin >= OI_REL_FRAC * oi_max)

    # 6) multi-expiry
    if with_buckets:
        try:
            ctx.buckets = _bucket_summary(all_rows, _now, exchanges)
        except Exception as e:
            ctx.warnings.append("buckets fallaron: %s" % e)

    return ctx


if __name__ == "__main__":
    for c in ("BTC", "ETH"):
        try:
            ctx = build_context(c)
        except Exception as e:
            print("%s: %s" % (c, e)); continue
        print("=" * 78)
        print("%s  status=%s  spot=%s  src=%s" % (c, ctx.data_status, ctx.spot_price, ctx.source))
        print("  régimen=%s  net_gex=%s M$  flip=%s (%.2f%%)"
              % (ctx.gamma_regime, ctx.net_gex_musd, ctx.gamma_flip,
                 ctx.distance_to_gamma_flip_pct or 0))
        print("  call_wall=%s (%.2f%%, f=%s)  put_wall=%s (%.2f%%, f=%s)"
              % (ctx.call_wall_1, ctx.distance_to_call_wall_pct or 0, ctx.call_wall_strength,
                 ctx.put_wall_1, ctx.distance_to_put_wall_pct or 0, ctx.put_wall_strength))
        print("  max_pain=%s (%.2f%%)  PCR=%s  vto=%s (%sh)  densidad=%s"
              % (ctx.max_pain, ctx.distance_to_max_pain_pct or 0, ctx.put_call_ratio,
                 ctx.expiry_str, ctx.hours_to_expiry, ctx.option_level_density))
        for name, b in (ctx.buckets or {}).items():
            if b:
                print("    %-6s vto→maxpain=%s régimen=%s cw=%s pw=%s"
                      % (name, b.get("max_pain"), b.get("gamma_regime"),
                         b.get("call_wall"), b.get("put_wall")))
