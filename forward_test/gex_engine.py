"""
gex_engine.py — Motor de Gamma Exposure (GEX) para cripto, reconstruido localmente.

Fuentes: Deribit (primaria, ~85% del OI de opciones cripto), Bybit y OKX (complemento).
Sin API key. Sólo endpoints públicos.

Diseño:
  1. fetch_chain()      -> descarga la cadena de opciones completa (1 request por exchange)
  2. build_options()    -> normaliza a una lista de OptionRow en unidades de moneda (BTC/ETH)
  3. gamma_profile()    -> recalcula el GEX neto sobre una grilla de spots hipotéticos
                           => de ahí sale el GAMMA FLIP (cruce por cero)
  4. strike_ladder()    -> GEX neto por strike => muros (call wall / put wall / imanes)
  5. GexState           -> snapshot completo listo para servir por HTTP

Convención de signo (estándar de la industria, SpotGamma/SqueezeMetrics):
    CALL  -> GEX positivo
    PUT   -> GEX negativo
Es un SUPUESTO de posicionamiento de dealers, no una posición observada.
Ver README para la discusión y para el modo `flow_signed`.

Fórmula por contrato, adaptada a cripto (multiplicador 1 moneda, no 100 acciones):
    gex_usd = gamma_BS * OI_moneda * S^2 * 0.01     [USD por movimiento de 1%]
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterable, Literal, Sequence

import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

HTTP_TIMEOUT = 12
USER_AGENT = "gex-engine/1.0"

# Multiplicador de contrato -> unidades de moneda subyacente.
# Deribit: open_interest ya viene en la moneda base (BTC/ETH). -> 1.0
# Bybit:   openInterest en contratos; el contrato de opción está denominado en
#          la moneda base. -> 1.0 (VERIFICAR contra la UI en la primera corrida,
#          ver `sanity_report()`).
# OKX:     1.0 porque NO usamos el campo `oi` (contratos) sino `oiCcy`, que la
#          API ya devuelve en moneda base. Verificado en vivo 2026-09-07:
#          oiCcy/oi == 0.01 exacto para BTC, coincide con el 0.01 que se
#          suponia. Usar oiCcy elimina el riesgo de multiplicador equivocado.
CONTRACT_MULTIPLIER = {
    "deribit": {"BTC": 1.0, "ETH": 1.0, "SOL": 1.0, "XRP": 1.0},
    "bybit":   {"BTC": 1.0, "ETH": 1.0, "SOL": 1.0, "XRP": 1.0},
    "okx":     {"BTC": 1.0, "ETH": 1.0, "SOL": 1.0, "XRP": 1.0},
}

# Las opciones de Deribit y Bybit vencen a las 08:00 UTC.
EXPIRY_HOUR_UTC = 8

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

# Piso de tiempo a vencimiento: 10 minutos. Evita gamma -> infinito en el 0DTE.
MIN_T_YEARS = 10.0 / (365.0 * 24.0 * 60.0)

# Numerator and denominator use the same strike_ladder absolute contract GEX
# weights (including its existing time floor), evaluated at the same instant.
EXPIRY_SHARE_CONVENTION = "weighted_abs_gex_v1"
EXPIRY_SHARE_VERSION = 1


# ─────────────────────────────────────────────────────────────────────────────
# ESTRUCTURAS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OptionRow:
    exchange: str
    coin: str
    strike: float
    is_call: bool
    expiry_ts: float          # epoch segundos, UTC
    oi_coin: float            # open interest en unidades de moneda subyacente
    iv: float                 # volatilidad implícita, decimal (0.55 = 55%)
    underlying: float         # precio del subyacente reportado por el exchange
    rate: float = 0.0

    @property
    def dte(self) -> float:
        return max((self.expiry_ts - time.time()) / 86400.0, 0.0)

    @property
    def t_years(self) -> float:
        return max((self.expiry_ts - time.time()) / (365.0 * 86400.0), MIN_T_YEARS)


@dataclass
class StrikeLevel:
    strike: float
    net_gex: float            # USD por 1% de movimiento
    abs_gex: float
    call_gex: float
    put_gex: float
    oi_coin: float


@dataclass
class GexState:
    coin: str
    spot: float
    ts: float
    exchanges: list[str]
    n_contratos: int
    oi_total_coin: float
    oi_total_usd: float

    net_gex: float                 # USD / 1%
    abs_gex: float
    net_gex_musd: float            # millones USD / 1%
    regimen: str                   # "POSITIVO" | "NEGATIVO"

    gamma_flip: float | None
    dist_flip_pct: float | None

    call_wall: float | None
    put_wall: float | None
    max_abs_strike: float | None
    zero_gamma_zone: tuple[float, float] | None

    ladder: list[StrikeLevel]
    profile_x: list[float]         # grilla de spots
    profile_y: list[float]         # GEX neto en cada spot

    proximo_vto_ts: float | None
    proximo_vto_horas: float | None
    proximo_vto_share_abs: float | None   # fracción del |GEX| total que vence

    velocidad_esperada: str        # "PINNING" | "VIOLENTA" | "BISAGRA" | "NORMAL"
    dte_max: float | None
    warnings: list[str] = field(default_factory=list)
    # None means unknown/legacy; only build_state attests the new calculation.
    expiry_share_convention: str | None = None
    expiry_share_version: int | None = None
    expiry_share_ramp_days: float | None = None
    captured_at: float | None = None  # Unix UTC valuation time (=ts), NOT vendor publication/acquisition time

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ladder"] = [asdict(s) for s in self.ladder]
        return d


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────

def _get_json(url: str, params: dict | None = None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# PARSEO DE NOMBRES DE INSTRUMENTO
# ─────────────────────────────────────────────────────────────────────────────

_NAME_RE = re.compile(
    r"^(?P<coin>[A-Z0-9]+)[-_](?P<d>\d{1,2})(?P<m>[A-Z]{3})(?P<y>\d{2})[-_]"
    r"(?P<k>[0-9.]+)[-_](?P<cp>[CP])"
)


# OKX no usa DDMMMYY sino YYMMDD, y agrega un sufijo de familia opcional:
#   BTC-USD-260908-74000-C  ·  BTC-USD_UM-260910-78000-C
_OKX_RE = re.compile(
    r"^(?P<coin>[A-Z0-9]+)-(?P<quote>[A-Z0-9]+?)(?:_[A-Z]+)?-"
    r"(?P<y>\d{2})(?P<mo>\d{2})(?P<d>\d{2})-(?P<k>[0-9.]+)-(?P<cp>[CP])$"
)


def _parse_okx(name: str) -> tuple[str, float, bool, float] | None:
    m = _OKX_RE.match(name.upper())
    if not m:
        return None
    try:
        dt = datetime(2000 + int(m.group("y")), int(m.group("mo")),
                      int(m.group("d")), EXPIRY_HOUR_UTC, 0, 0, tzinfo=timezone.utc)
        return m.group("coin"), float(m.group("k")), m.group("cp") == "C", dt.timestamp()
    except ValueError:
        return None


def parse_instrument(name: str) -> tuple[str, float, bool, float] | None:
    """'BTC-26SEP26-100000-C' -> ('BTC', 100000.0, True, epoch_vto)

    Acepta tambien el formato OKX 'BTC-USD-260908-74000-C' (YYMMDD)."""
    m = _NAME_RE.match(name.upper())
    if not m:
        return _parse_okx(name)
    try:
        year = 2000 + int(m.group("y"))
        month = _MONTHS[m.group("m")]
        day = int(m.group("d"))
        dt = datetime(year, month, day, EXPIRY_HOUR_UTC, 0, 0, tzinfo=timezone.utc)
        return m.group("coin"), float(m.group("k")), m.group("cp") == "C", dt.timestamp()
    except (KeyError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# BLACK-SCHOLES
# ─────────────────────────────────────────────────────────────────────────────

_INV_SQRT_2PI = 0.3989422804014327


def _norm_pdf(x):
    return _INV_SQRT_2PI * np.exp(-0.5 * np.square(x))


def _d1d2(S, K, T, sigma):
    S = np.asarray(S, dtype=float); K = np.asarray(K, dtype=float)
    T = np.clip(np.asarray(T, dtype=float), MIN_T_YEARS, None)
    sigma = np.clip(np.asarray(sigma, dtype=float), 1e-4, 10.0)
    sq = sigma * np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = np.log(S / K) / sq + 0.5 * sq
        d2 = d1 - sq
    return np.nan_to_num(d1), np.nan_to_num(d2), sq, T, sigma


def bs_vanna(S, K, T, sigma):
    """
    Vanna = d(delta)/d(sigma). Cuanto cambia la cobertura del dealer cuando se
    mueve la volatilidad implicita, sin que se mueva el precio.
    Igual para calls y puts: delta_put = delta_call - 1, y la derivada coincide.
    """
    d1, d2, sq, T, sigma = _d1d2(S, K, T, sigma)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = -_norm_pdf(d1) * d2 / sigma
    return np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)


def bs_charm(S, K, T, sigma):
    """
    Charm = d(delta)/d(tiempo). Cuanto cambia la cobertura por el mero paso del
    tiempo, sin que se mueva ni el precio ni la volatilidad. Con r=q=0 es igual
    para calls y puts. Signo: positivo = el delta sube al pasar el tiempo.
    """
    d1, d2, sq, T, sigma = _d1d2(S, K, T, sigma)
    with np.errstate(divide="ignore", invalid="ignore"):
        c = _norm_pdf(d1) * d2 / (2.0 * T)
    return np.nan_to_num(c, nan=0.0, posinf=0.0, neginf=0.0)


def bs_gamma(S, K, T, sigma, r=0.0):
    """
    Gamma de Black-Scholes. Vectorizado.
    Devuelve d(delta)/dS, unidades 1/USD, por 1 unidad de subyacente.
    """
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.clip(np.asarray(T, dtype=float), MIN_T_YEARS, None)
    sigma = np.clip(np.asarray(sigma, dtype=float), 1e-4, 10.0)

    sqrtT = np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrtT)
        g = _norm_pdf(d1) / (S * sigma * sqrtT)
    return np.nan_to_num(g, nan=0.0, posinf=0.0, neginf=0.0)


# ─────────────────────────────────────────────────────────────────────────────
# FETCHERS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_deribit(coin: str = "BTC") -> list[OptionRow]:
    """
    Una sola llamada trae TODA la cadena con OI + mark_iv + underlying_price.
    open_interest viene en unidades de la moneda base.
    """
    data = _get_json("https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                     {"currency": coin, "kind": "option"})
    rows: list[OptionRow] = []
    mult = CONTRACT_MULTIPLIER["deribit"].get(coin, 1.0)
    for it in data.get("result", []):
        parsed = parse_instrument(it.get("instrument_name", ""))
        if not parsed:
            continue
        c, strike, is_call, exp = parsed
        oi = it.get("open_interest") or 0.0
        iv = it.get("mark_iv")
        und = it.get("underlying_price")
        if not oi or not iv or not und:
            continue
        rows.append(OptionRow(
            exchange="deribit", coin=c, strike=strike, is_call=is_call,
            expiry_ts=exp, oi_coin=float(oi) * mult, iv=float(iv) / 100.0,
            underlying=float(und), rate=float(it.get("interest_rate") or 0.0),
        ))
    return rows


def fetch_bybit(coin: str = "BTC") -> list[OptionRow]:
    """
    /v5/market/tickers?category=option&baseCoin=BTC
    Trae openInterest, markIv, underlyingPrice y las griegas ya calculadas.
    Usamos markIv y recalculamos gamma nosotros para que el criterio sea
    idéntico en los tres exchanges.
    """
    data = _get_json("https://api.bybit.com/v5/market/tickers",
                     {"category": "option", "baseCoin": coin})
    if data.get("retCode") != 0:
        raise RuntimeError(f"bybit: {data.get('retMsg')}")
    rows: list[OptionRow] = []
    mult = CONTRACT_MULTIPLIER["bybit"].get(coin, 1.0)
    for it in data.get("result", {}).get("list", []):
        parsed = parse_instrument(it.get("symbol", ""))
        if not parsed:
            continue
        c, strike, is_call, exp = parsed
        try:
            oi = float(it.get("openInterest") or 0.0)
            iv = float(it.get("markIv") or 0.0)
            und = float(it.get("underlyingPrice") or 0.0)
        except (TypeError, ValueError):
            continue
        if oi <= 0 or iv <= 0 or und <= 0:
            continue
        # Bybit devuelve markIv en decimal (0.55), no en porcentaje.
        if iv > 5.0:
            iv /= 100.0
        rows.append(OptionRow("bybit", c, strike, is_call, exp,
                              oi * mult, iv, und))
    return rows


def fetch_okx(coin: str = "BTC") -> list[OptionRow]:
    """
    OKX: 2 llamadas (IV de opt-summary + OI de open-interest), unidas por instId.

    Dos correcciones sobre la version original, verificadas en vivo 2026-09-07:

    1. `uly=BTC-USD` en opt-summary devuelve SOLO la familia `_UM` (margen
       unificado), que tiene 132 BTC de OI. El libro real vive en la familia
       legacy y son 32.579 BTC. El parametro correcto es `instFamily`.
    2. OKX nombra los instrumentos en YYMMDD, no en DDMMMYY. El parser generico
       fallaba en el 100% de las filas, asi que el fetcher devolvia 0 contratos
       aunque las dos llamadas HTTP respondieran bien.

    Se usa `oiCcy` (OI ya en moneda base) en vez de `oi` x multiplicador: es
    exacto y no depende de adivinar el tamano de contrato.
    """
    fam = f"{coin}-USD"
    summ = _get_json("https://www.okx.com/api/v5/public/opt-summary",
                     {"instFamily": fam})
    oi_raw = _get_json("https://www.okx.com/api/v5/public/open-interest",
                       {"instType": "OPTION", "instFamily": fam})
    oi_map = {}
    for d in oi_raw.get("data", []):
        try:
            oi_map[d["instId"]] = float(d.get("oiCcy") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue

    idx = _get_json("https://www.okx.com/api/v5/market/index-tickers",
                    {"instId": fam})
    try:
        spot = float(idx["data"][0]["idxPx"])
    except (KeyError, IndexError, ValueError, TypeError):
        spot = 0.0

    rows: list[OptionRow] = []
    mult = CONTRACT_MULTIPLIER["okx"].get(coin, 1.0)
    for it in summ.get("data", []):
        inst = it.get("instId", "")
        parsed = parse_instrument(inst)
        if not parsed:
            continue
        c, strike, is_call, exp = parsed
        oi = oi_map.get(inst, 0.0)
        try:
            iv = float(it.get("markVol") or 0.0)
        except (TypeError, ValueError):
            continue
        # fwdPx por contrato es mejor referencia que el indice si el indice fallo
        if spot <= 0:
            try:
                spot = float(it.get("fwdPx") or 0.0)
            except (TypeError, ValueError):
                pass
        if oi <= 0 or iv <= 0 or spot <= 0:
            continue
        rows.append(OptionRow("okx", c, strike, is_call, exp,
                              oi * mult, iv, spot))
    return rows


FETCHERS = {"deribit": fetch_deribit, "bybit": fetch_bybit, "okx": fetch_okx}


def build_options(coin: str = "BTC",
                  exchanges: Sequence[str] = ("deribit", "bybit", "okx"),
                  ) -> tuple[list[OptionRow], list[str]]:
    rows: list[OptionRow] = []
    warnings: list[str] = []
    ok: list[str] = []
    for ex in exchanges:
        fn = FETCHERS.get(ex)
        if fn is None:
            warnings.append(f"exchange desconocido: {ex}")
            continue
        try:
            got = fn(coin)
            if not got:
                warnings.append(f"{ex}: 0 contratos con OI>0 (¿API degradada?)")
            else:
                ok.append(ex)
            rows.extend(got)
        except (urllib.error.URLError, TimeoutError, RuntimeError,
                json.JSONDecodeError, KeyError) as e:
            warnings.append(f"{ex} caído: {type(e).__name__}: {e}")
    return rows, (ok, warnings)  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────────
# CÁLCULO DE GEX
# ─────────────────────────────────────────────────────────────────────────────

def _arrays(rows: Sequence[OptionRow], now: float | None = None):
    now = now or time.time()
    K = np.array([r.strike for r in rows], dtype=float)
    T = np.array([max((r.expiry_ts - now) / (365.0 * 86400.0), MIN_T_YEARS)
                  for r in rows], dtype=float)
    sig = np.array([r.iv for r in rows], dtype=float)
    oi = np.array([r.oi_coin for r in rows], dtype=float)
    sign = np.array([1.0 if r.is_call else -1.0 for r in rows], dtype=float)
    return K, T, sig, oi, sign


def net_gex_at(rows: Sequence[OptionRow], spot: float,
               dte_ramp_days: float = 0.0, now: float | None = None) -> float:
    """
    GEX neto (USD por movimiento de 1%) si el spot fuera `spot`.
    IV y T se mantienen fijos: es la convención estándar para el perfil de gamma.

    dte_ramp_days > 0 aplica un peso min(1, DTE/ramp) para que un vencimiento
    0DTE (con su pico de gamma ~1/sqrt(T)) no domine el nivel de régimen.
    """
    if not rows:
        return 0.0
    K, T, sig, oi, sign = _arrays(rows, now)
    g = bs_gamma(spot, K, T, sig)
    w = 1.0
    if dte_ramp_days > 0:
        dte = T * 365.0
        w = np.clip(dte / dte_ramp_days, 0.0, 1.0)
    return float(np.sum(g * oi * sign * w) * spot * spot * 0.01)


def gamma_profile(rows: Sequence[OptionRow], spot: float,
                  span_pct: float = 0.20, n: int = 241,
                  dte_ramp_days: float = 0.0, now: float | None = None):
    """Curva de GEX neto sobre una grilla de spots. Devuelve (xs, ys).

    __HIST_NOW__: `now` permite reconstruir un snapshot HISTORICO. Sin el, T se
    calcularia contra la hora actual y todos los contratos de una fecha pasada
    quedarian vencidos (T=0), que es justo el bug que rompia gex_tardis.py."""
    lo, hi = spot * (1 - span_pct), spot * (1 + span_pct)
    xs = np.linspace(lo, hi, n)
    if not rows:
        return xs, np.zeros_like(xs)
    K, T, sig, oi, sign = _arrays(rows, now)
    w = 1.0
    if dte_ramp_days > 0:
        w = np.clip((T * 365.0) / dte_ramp_days, 0.0, 1.0)
    coef = oi * sign * w
    # (n_grid, n_opts)
    g = bs_gamma(xs[:, None], K[None, :], T[None, :], sig[None, :])
    ys = (g * coef[None, :]).sum(axis=1) * xs ** 2 * 0.01
    return xs, ys


def find_gamma_flip(xs: np.ndarray, ys: np.ndarray, spot: float) -> float | None:
    """Cruce por cero más cercano al spot, con interpolación lineal."""
    s = np.sign(ys)
    idx = np.where(np.diff(s) != 0)[0]
    if idx.size == 0:
        return None
    roots = []
    for i in idx:
        y0, y1 = ys[i], ys[i + 1]
        if y1 == y0:
            continue
        roots.append(xs[i] + (xs[i + 1] - xs[i]) * (-y0) / (y1 - y0))
    if not roots:
        return None
    return float(min(roots, key=lambda r: abs(r - spot)))


def strike_ladder(rows: Sequence[OptionRow], spot: float,
                  bucket: float | None = None,
                  dte_ramp_days: float = 0.0,
                  now: float | None = None) -> list[StrikeLevel]:
    """GEX por strike, evaluado en el spot actual (o en `now` si es historico)."""
    if not rows:
        return []
    K, T, sig, oi, sign = _arrays(rows, now)
    g = bs_gamma(spot, K, T, sig)
    w = np.ones_like(K)
    if dte_ramp_days > 0:
        w = np.clip((T * 365.0) / dte_ramp_days, 0.0, 1.0)
    gex = g * oi * sign * w * spot * spot * 0.01

    if bucket:
        Kb = np.round(K / bucket) * bucket
    else:
        Kb = K

    out: dict[float, StrikeLevel] = {}
    for k, v, s, o in zip(Kb, gex, sign, oi):
        lvl = out.get(k)
        if lvl is None:
            lvl = out[k] = StrikeLevel(float(k), 0.0, 0.0, 0.0, 0.0, 0.0)
        lvl.net_gex += float(v)
        lvl.abs_gex += abs(float(v))
        lvl.oi_coin += float(o)
        if s > 0:
            lvl.call_gex += float(v)
        else:
            lvl.put_gex += float(v)
    return sorted(out.values(), key=lambda x: x.strike)


# ─────────────────────────────────────────────────────────────────────────────
# SNAPSHOT
# ─────────────────────────────────────────────────────────────────────────────

def build_state(coin: str = "BTC",
                exchanges: Sequence[str] = ("deribit", "bybit", "okx"),
                dte_max: float | None = None,
                dte_ramp_days: float = 0.0,
                bucket: float | None = None,
                span_pct: float = 0.20,
                rows: Sequence[OptionRow] | None = None,
                now: float | None = None) -> GexState:
    """
    dte_max      : limita la cadena a vencimientos <= N días.
                   None = estructural (toda la cadena).
                   7    = vista scalp (la gamma que realmente pesa hoy).
    dte_ramp_days: suaviza el pico de gamma del 0DTE al calcular el flip.
    bucket       : agrupa strikes (ej. 1000 para BTC) para un ladder legible.
    """
    warnings: list[str] = []
    ok_ex: list[str] = list(exchanges)

    if rows is None:
        rows, meta = build_options(coin, exchanges)
        ok_ex, warnings = meta  # type: ignore[misc]
    else:
        ok_ex = sorted({r.exchange for r in rows})

    # El chequeo de unidades se hace sobre la cadena COMPLETA, antes de recortar
    # por DTE. Si se corre sobre la cadena filtrada da falsos positivos: el OI de
    # Deribit se concentra en vencimientos largos, asi que dentro de <=7 DTE su
    # cuota cae y los otros venues parecen implausiblemente grandes. Medido en
    # vivo 2026-09-07 con ETH: cadena completa deribit 72,4% / okx 15,0% /
    # bybit 12,6%, pero <=7 DTE deribit 43,5% / bybit 28,9% / okx 27,6%. El
    # multiplicador estaba bien; el chequeo miraba la poblacion equivocada.
    _chain_all = list(rows)

    _now = now if now is not None else time.time()
    if dte_max is not None:
        rows = [r for r in rows if 0 < (r.expiry_ts - _now) / 86400.0 <= dte_max]

    if not rows:
        raise RuntimeError(f"sin datos de opciones para {coin} ({', '.join(exchanges)})")

    # Spot = mediana del underlying reportado (robusto a un exchange desfasado).
    spot = float(np.median([r.underlying for r in rows]))
    if bucket is None:
        bucket = _auto_bucket(spot)

    xs, ys = gamma_profile(rows, spot, span_pct=span_pct, dte_ramp_days=dte_ramp_days, now=_now)
    flip = find_gamma_flip(xs, ys, spot)
    ladder = strike_ladder(rows, spot, bucket=bucket, dte_ramp_days=dte_ramp_days, now=_now)

    net = net_gex_at(rows, spot, dte_ramp_days=dte_ramp_days, now=_now)
    absg = sum(l.abs_gex for l in ladder)

    # Muros: mayor GEX positivo arriba del spot / mayor GEX negativo abajo.
    above = [l for l in ladder if l.strike > spot]
    below = [l for l in ladder if l.strike < spot]
    call_wall = max(above, key=lambda l: l.net_gex).strike if above else None
    put_wall = min(below, key=lambda l: l.net_gex).strike if below else None
    max_abs = max(ladder, key=lambda l: l.abs_gex).strike if ladder else None

    # Zona de transición: ±0.5% alrededor del flip.
    zone = (flip * 0.995, flip * 1.005) if flip else None

    # Vencimiento más próximo y su peso en |GEX|.
    now = _now
    exps = sorted({r.expiry_ts for r in rows if r.expiry_ts > now})
    prox_ts = exps[0] if exps else None
    prox_h = (prox_ts - now) / 3600.0 if prox_ts else None
    prox_share = None
    if prox_ts:
        sub = [r for r in rows if r.expiry_ts == prox_ts]
        sub_abs = sum(l.abs_gex for l in strike_ladder(
            sub, spot, bucket=bucket, dte_ramp_days=dte_ramp_days, now=_now))
        prox_share = (sub_abs / absg) if absg > 0 else 0.0

    oi_coin = sum(r.oi_coin for r in rows)
    velocidad = _classify_velocity(net, absg, flip, spot)

    if flip is None:
        warnings.append(f"sin cruce por cero en ±{span_pct:.0%} del spot: "
                        f"régimen homogéneo, el flip está fuera de rango")
    warnings += _sanity(_chain_all, ok_ex)

    _st = GexState(
        coin=coin, spot=spot, ts=now, exchanges=ok_ex,
        n_contratos=len(rows), oi_total_coin=oi_coin, oi_total_usd=oi_coin * spot,
        net_gex=net, abs_gex=absg, net_gex_musd=net / 1e6,
        regimen="POSITIVO" if net >= 0 else "NEGATIVO",
        gamma_flip=flip,
        dist_flip_pct=((spot - flip) / spot * 100.0) if flip else None,
        call_wall=call_wall, put_wall=put_wall, max_abs_strike=max_abs,
        zero_gamma_zone=zone,
        ladder=ladder, profile_x=[float(v) for v in xs], profile_y=[float(v) for v in ys],
        proximo_vto_ts=prox_ts, proximo_vto_horas=prox_h,
        proximo_vto_share_abs=prox_share,
        velocidad_esperada=velocidad, dte_max=dte_max, warnings=warnings,
        expiry_share_convention=EXPIRY_SHARE_CONVENTION,
        expiry_share_version=EXPIRY_SHARE_VERSION,
        expiry_share_ramp_days=float(dte_ramp_days), captured_at=_now,
    )
    # se conservan las filas para poder calcular VEX/CEX sin volver a descargar
    try:
        object.__setattr__(_st, "_rows", list(rows))
        # __EXP3_VISTA_2026_09_20__ — la RAMPA viaja con el objeto, no por el
        # nombre de la vista. `_exp3` la tomaba de un diccionario global y se la
        # aplicaba a las filas de cualquier estado: la rampa y las filas podian
        # venir de vistas distintas. Se conserva este atributo privado para los
        # consumidores existentes; expiry_share_ramp_days es metadato publico.
        object.__setattr__(_st, "_dte_ramp_days", float(dte_ramp_days))
    except Exception:
        pass
    return _st


def exposiciones(rows, spot, dte_ramp_days=0.0, now=None):
    """
    __VEX_CEX_2026_09_08__ — las TRES exposiciones que el dealer debe cubrir,
    normalizadas para que sean comparables entre si:

        GEX : USD por cada 1% de movimiento del subyacente
        VEX : USD por cada 1 PUNTO de volatilidad implicita (1 vol point)
        CEX : USD por cada DIA calendario que pasa

    Mismo supuesto de signo que el GEX (call +, put -). El GEX dice cuanto frena o
    amplifica el dealer si se mueve el PRECIO; el VEX, si se mueve la VOL; el CEX,
    si simplemente pasa el TIEMPO. Un nivel donde los tres apuntan igual es una
    zona de respuesta apilada; donde se contradicen, la reaccion es menos fiable.
    """
    if not rows:
        return {"gex": 0.0, "vex": 0.0, "cex": 0.0}
    K, T, sig, oi, sign = _arrays(rows, now)
    w = 1.0
    if dte_ramp_days > 0:
        w = np.clip((T * 365.0) / dte_ramp_days, 0.0, 1.0)
    coef = oi * sign * w
    g = bs_gamma(spot, K, T, sig)
    v = bs_vanna(spot, K, T, sig)
    c = bs_charm(spot, K, T, sig)
    return {
        "gex": float(np.sum(g * coef) * spot * spot * 0.01),
        "vex": float(np.sum(v * coef) * spot * 0.01),
        "cex": float(np.sum(c * coef) * spot / 365.0),
    }


def _gex_vector(rows: Sequence[OptionRow], spot: float, now=None) -> np.ndarray:
    K, T, sig, oi, sign = _arrays(rows, now)
    return bs_gamma(spot, K, T, sig) * oi * sign * spot * spot * 0.01


def _auto_bucket(spot: float) -> float:
    for thr, b in ((50000, 1000.0), (10000, 500.0), (1000, 100.0),
                   (100, 10.0), (10, 1.0)):
        if spot >= thr:
            return b
    return 0.1


def _classify_velocity(net: float, absg: float, flip: float | None,
                       spot: float) -> str:
    """
    Estado de la estructura. Son CUATRO, no tres, porque "violenta" mezclaba dos
    situaciones que se operan al reves una de la otra:

        BISAGRA : el precio esta a <0.5% del gamma flip. El regimen esta
                  INDEFINIDO y puede darse vuelta dentro del trade. No es que
                  vaya a acelerar; es que no se sabe de que lado va a estar.
                  Se opera reduciendo tamano.
        VIOLENTA: gamma NEGATIVA concentrada. Los dealers compran fuerza y venden
                  debilidad = persiguen el precio. Direccional: favorece el
                  barrido de liquidez y rompe el fade.
        PINNING : gamma POSITIVA concentrada. Venden fuerza y compran debilidad =
                  el precio se pega a los strikes. Terreno del fade al POC/VWAP.
        NORMAL  : la estructura NO tiene opinion. El libro esta tan repartido
                  entre calls y puts que la cobertura se cancela (concentracion
                  <= 0.15). No significa "mercado tranquilo": significa que el
                  GEX no aporta y hay que decidir con el resto del panel.

    OJO: los cortes 0.15 de concentracion y 0.5% de banda del flip son valores
    por defecto SIN CALIBRAR. Lo unico medido (gex_calibrate.py) es el SIGNO del
    regimen. Tratar estos estados como lectura cualitativa, no como base de sizing.
    """
    if absg <= 0:
        return "NORMAL"
    conc = abs(net) / absg           # qué tan neta es la posición
    near_flip = flip is not None and abs(spot - flip) / spot < 0.005
    if near_flip:
        return "BISAGRA"             # sobre el flip el régimen está indefinido
    if net < 0 and conc > 0.15:
        return "VIOLENTA"            # gamma negativa: los dealers persiguen
    if net > 0 and conc > 0.15:
        return "PINNING"             # gamma positiva: los dealers frenan
    return "NORMAL"                  # libro repartido: la estructura no opina


def _sanity(rows: Sequence[OptionRow], ok: Sequence[str]) -> list[str]:
    """Chequeos de cordura de unidades. El error clásico es el multiplicador."""
    w = []
    by_ex: dict[str, float] = {}
    for r in rows:
        by_ex[r.exchange] = by_ex.get(r.exchange, 0.0) + r.oi_coin
    d = by_ex.get("deribit", 0.0)
    if d > 0:
        for ex, v in by_ex.items():
            if ex == "deribit":
                continue
            ratio = v / d
            if ratio > 0.60:
                w.append(f"{ex} pesa {ratio:.0%} del OI de Deribit — implausible "
                         f"(Deribit concentra ~85% del mercado). Revisar "
                         f"CONTRACT_MULTIPLIER['{ex}'].")
            elif ratio < 0.005:
                w.append(f"{ex} pesa {ratio:.2%} del OI de Deribit — sospechoso, "
                         f"puede ser openInterest=0 en la API.")
    # El chequeo de Deribit es especifico de CRIPTO. Las cadenas de renta variable
    # (gex_equity.py) vienen de OCC via yfinance y no tienen nada que ver, asi que
    # no se les aplica: antes emitian un "Deribit NO respondio" que era falso.
    _es_cripto = any(r.exchange in ("deribit", "bybit", "okx") for r in rows)
    if _es_cripto and "deribit" not in ok:
        w.append("Deribit NO respondió. El GEX está calculado sobre <15% del "
                 "mercado: no es representativo. Tratar como no disponible.")
    return w


# ─────────────────────────────────────────────────────────────────────────────
# UTILIDAD
# ─────────────────────────────────────────────────────────────────────────────

def sanity_report(coin: str = "BTC") -> str:
    rows, (ok, warns) = build_options(coin, ("deribit", "bybit", "okx"))  # type: ignore
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r.exchange, []).append(r.oi_coin)
    lines = [f"— Chequeo de unidades · {coin} —"]
    for ex, v in by.items():
        lines.append(f"{ex:>8}: {len(v):>5} contratos · OI {sum(v):>12,.1f} {coin}")
    lines += [f"  ! {w}" for w in warns]
    lines.append("Comparar cada OI contra la pantalla del exchange. Si difiere "
                 "por un factor exacto (100x, 0.01x), corregir CONTRACT_MULTIPLIER.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    coin = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    print(sanity_report(coin))
    st = build_state(coin, dte_max=7, dte_ramp_days=1.0)
    print(f"\nspot {st.spot:,.0f} · flip {st.gamma_flip:,.0f} "
          f"({st.dist_flip_pct:+.2f}%) · {st.regimen} · "
          f"{st.net_gex_musd:+,.1f} M$/1% · {st.velocidad_esperada}")
