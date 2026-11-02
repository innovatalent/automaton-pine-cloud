# -*- coding: utf-8 -*-
"""__BASELINE_BALLENAS_2026_10_11__ — la LINEA BASE PROPIA de cada simbolo para leer
el posicionamiento de ballenas/retail.

EL PROBLEMA QUE ARREGLA (medido el 2026-10-11, 25 simbolos, ~1,9 anios, hold 5d):
el ratio de ballenas (top-traders por TAMANO) esta estructuralmente del lado long.
Decir "ballenas LONG si el %long >= 50" marca LONG el 93,2 % de los dias y no tiene
NINGUN contenido direccional:

    regla                                   n      E/op      t     amplitud
    whale% >= 50        (la vieja)         6709   +0,002 %  +0,02   15/25
    whale vs su MEDIANA 90d propia         6709   +0,664 %  +5,74   23/25
    z-spread 90d >= 1,5  (spec congelada)  2230   +0,614 %  +3,04   18/24
    transversal (vs la mediana del dia)    8896   +0,081 %  +0,83   15/27   <- NO sirve

El efecto es MONOTONO en la ventana (20d +0,03 % · 30d +0,20 % · 60d +0,29 % ·
90d +0,66 %), asi que la ventana corta no alcanza: hace falta historia propia. La
API REST de Binance solo da 30 dias -> se usa el archivo publico ya descargado en
forward_test/binance_vision.db (tools/backfill_posicionamiento.py lo mantiene).

OJO HONESTO: por simbolo-y-mitad la amplitud es floja (9/25 positivos en AMBAS
mitades), igual que biblioteca 173. Esto es CONTEXTO direccional de dias, no una
senial por-toque ni una probabilidad.
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.path.dirname(HERE), "forward_test", "binance_vision.db")
VENTANA = 90            # dias de linea base (medido: mas corto no sirve)
MAX_EDAD_DIAS = 10.0    # la mediana se mueve lento, pero no se sirve historia muerta
MAX_EDAD_Z = 4.0        # el z-spread es puntual: viejo de mas, no se publica
_TTL = 3600.0
_cache = {}


def _frac(ratio):
    """ratio long/short -> fraccion long (0-1). Monotono, asi que no cambia el orden."""
    return ratio / (1.0 + ratio) if ratio and ratio > 0 else None


def _conectar():
    """Abre la base en solo-lectura y se auto-cura si quedo un journal caliente.

    Si el backfill muere a mitad de una transaccion queda un -journal, y entonces
    CUALQUIER select en mode=ro tira "attempt to write a readonly database" porque
    SQLite necesita deshacerlo. Paso el 2026-10-11 y el efecto era que TODOS los
    simbolos se quedaban sin linea base en silencio. Abrir una vez en lectura-
    escritura deja que SQLite lo reponga y despues se sigue leyendo normal.
    """
    uri = "file:%s?mode=ro" % DB.replace("\\", "/")
    try:
        con = sqlite3.connect(uri, uri=True, timeout=20)
        con.execute("SELECT 1 FROM metrics LIMIT 1").fetchone()
        return con
    except sqlite3.OperationalError:
        try:
            cur = sqlite3.connect(DB, timeout=30)
            cur.execute("SELECT 1 FROM metrics LIMIT 1").fetchone()
            cur.commit()
            cur.close()
        except Exception:
            pass
        return sqlite3.connect(uri, uri=True, timeout=20)


def _zultimo(vals, n):
    if len(vals) < n + 1:
        return None
    w = vals[-1 - n:-1]
    m = sum(w) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
    return (vals[-1] - m) / sd if sd > 0 else 0.0


def ventana(symbol):
    """Linea base propia del simbolo, o None si no hay historia suficiente.

    {med_w: %long mediano de las ballenas en los VENTANA dias ANTERIORES (causal),
     z: z-spread 90d (z ballenas - z retail) del ultimo dia cerrado,
     dias: dias usables, ult: ultimo dia con dato, edad: dias de antiguedad}
    """
    now = time.time()
    hit = _cache.get(symbol)
    if hit and now - hit[0] < _TTL:
        return hit[1]

    out = None
    try:
        if os.path.exists(DB):
            con = _conectar()
            con.execute("PRAGMA busy_timeout=20000")
            # Solo la ventana usada y UNA fila por dia (la ultima = causal). El indice
            # (symbol, ts) + el GROUP BY bajan el costo de 1,28s por simbolo (scan de BTC
            # completo) a centesimas, y esto corre por cada simbolo exportado.
            rows = con.execute(
                "SELECT MAX(ts), tt_ratio_tamano, ls_ratio_cuentas FROM metrics "
                "WHERE symbol=? AND ts>=? GROUP BY CAST(ts/86400 AS INT) ORDER BY 1",
                (symbol, now - (VENTANA + 40) * 86400.0)).fetchall()
            con.close()
            por_dia = {}
            for ts, tt, ls in rows:
                if tt is None or ls is None:
                    continue
                d = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
                por_dia[d] = (float(tt), float(ls))       # ultima muestra del dia
            dias = sorted(por_dia)
            if len(dias) >= VENTANA + 1:
                edad = (now - datetime.fromisoformat(dias[-1] + "T00:00:00+00:00").timestamp()) / 86400.0
                W = [por_dia[d][0] for d in dias]
                R = [por_dia[d][1] for d in dias]
                prev = sorted(W[-1 - VENTANA:-1])     # los 90 dias ANTERIORES, sin hoy
                med = prev[VENTANA // 2] if VENTANA % 2 else (prev[VENTANA // 2 - 1] + prev[VENTANA // 2]) / 2.0
                fmed = _frac(med)
                if edad <= MAX_EDAD_DIAS and fmed:
                    zW, zR = _zultimo(W, VENTANA), _zultimo(R, VENTANA)
                    # El z es un valor PUNTUAL: si la historia quedo vieja deja de servir
                    # (el Pine lo usa para rotular conviccion). La MEDIANA en cambio se
                    # mueve lento y aguanta hasta MAX_EDAD_DIAS.
                    z = (round(zW - zR, 2) if zW is not None and zR is not None
                         and edad <= MAX_EDAD_Z else None)
                    # __ZW_ZR_SEPARADOS_2026_10_19__ zW y zR ya se calculan arriba y se
                    # tiraban: solo sobrevivia la resta. Sin las dos por separado el Pine no
                    # puede marcar la DIVERGENCIA ESTRICTA (|zW|>=1,5 + retail al reves >=1,0),
                    # que es la mejor celda medida: +2,646 %/op, t 5,46 (bibl.202) — aunque
                    # solo aparece el 3,6 % de los dias. Y sin la mediana del RETAIL, su %
                    # en pantalla no se puede leer (mismo problema que comparar las ballenas
                    # contra 50 en vez de contra su propia base).
                    prevR = sorted(R[-1 - VENTANA:-1])
                    medR = (prevR[VENTANA // 2] if VENTANA % 2
                            else (prevR[VENTANA // 2 - 1] + prevR[VENTANA // 2]) / 2.0)
                    fmedR = _frac(medR)
                    out = {"med_w": round(fmed * 100, 1), "med_raw": fmed * 100, "z": z,
                           "med_r": (round(fmedR * 100, 1) if fmedR else None),
                           "zw": (round(zW, 2) if zW is not None and edad <= MAX_EDAD_Z else None),
                           "zr": (round(zR, 2) if zR is not None and edad <= MAX_EDAD_Z else None),
                           "dias": len(dias), "ult": dias[-1], "edad": round(edad, 1)}
    except Exception:
        out = None

    _cache[symbol] = (now, out)
    return out


def lado(symbol, whale_long_pct):
    """+1/-1 comparando el %long VIVO de ballenas contra su mediana 90d propia; 0 sin base.

    Compara contra la mediana SIN redondear: con un decimal en los dos lados, un valor
    pegado a la mediana se decidia por el redondeo (y el empate exacto caia en SHORT).
    """
    b = ventana(symbol)
    if not b or whale_long_pct is None:
        return 0, b
    return (1 if whale_long_pct > b.get("med_raw", b["med_w"]) else -1), b


if __name__ == "__main__":
    import sys
    syms = (sys.argv[1].split(",") if len(sys.argv) > 1 else
            ["BTC", "ETH", "SOL", "TAO", "TQQQ", "XAU", "AAPL", "MSFT", "NVDA", "SOXL"])
    for c in syms:
        s = "%sUSDT" % c
        b = ventana(s)
        print("%-10s %s" % (s, b if b else "sin linea base (faltan dias o datos viejos)"))
