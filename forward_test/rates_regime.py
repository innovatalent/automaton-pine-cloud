# -*- coding: utf-8 -*-
"""RÉGIMEN DE TASAS (usuario 2026-09-04: "qué pesos podemos agregar según las noticias de subas o bajas de tasas; importá esa info
cada día"). Lectura OBJETIVA + noticias:
  1. CURVA CORTA de EE.UU. (yfinance, gratis, diario): ^IRX (letra 3 meses), ^FVX (5 años), ^TNX (10 años). Si el tramo corto SUBE,
     el mercado descuenta SUBAS de tasa (hawkish); si BAJA, recortes (dovish). Se mide el cambio en bps a 1, 5 y 20 días.
  2. TITULARES (Google News RSS, gratis): últimos 7 días sobre "Federal Reserve interest rates", clasificados por léxico
     hawkish (hike/raise/higher/hawkish/tighten/inflation elevada) vs dovish (cut/lower/ease/dovish). Sólo confirma; la curva manda.
Score −4..+4 → HAWKISH (≥+2) / DOVISH (≤−2) / NEUTRAL. Cache 6 h (`rates_regime.json`) → se re-importa solo varias veces por día.

PESOS (a priori, chicos como los demás sesgos; se registran en label_outcomes para validarlos OOS):
  activos de riesgo (acciones/TQQQ, BTC, alts):  HAWKISH → LONG −3 (fuerte −4) / SHORT +2 · DOVISH → LONG +3 (fuerte +4) / SHORT −2
  oro/plata:                                     HAWKISH → LONG −2 / SHORT +1        · DOVISH → LONG +2 / SHORT −1
  crudo: sin peso (ambiguo).
Uso: `python rates_regime.py` (muestra) · `--refresh` (fuerza) · `context()` / `adjust_for(symbol, side)` desde el panel."""
import os, re, json, time, html
import urllib.request as _ur

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "rates_regime.json")
TTL = 6 * 3600
_MEM = {"ts": 0.0, "data": None}
_HAWK = re.compile(r"\b(hike|hikes|hiking|raise|raises|raising|higher for longer|higher rates|hawkish|tighten|tightening|"
                   r"inflation (?:too )?(?:high|stubborn|elevated|sticky)|hold(?:ing)? rates? steady)\b", re.I)
_DOVE = re.compile(r"\b(rate cut|rate cuts|cut rates|cuts rates|cutting rates|lower(?:ing)? rates|ease|easing|dovish|"
                   r"cut interest)\b", re.I)
_PRESSURE = re.compile(r"\b(trump|vance|white house|president)\b", re.I)   # presión política ≠ intención de la Fed
_EQUITY = {"TQQQ", "NVDA", "TSLA", "AAPL", "META", "MSTR", "COIN", "SOXL", "SNDK", "PLTR", "AMD", "GOOGL", "AMZN",
           "MSFT", "HOOD", "MU", "NFLX", "AVGO", "SKHYNIX"}
_GOLD = {"XAU", "XAG", "GOLD", "SILVER"}
_CRUDE = {"CL", "WTI", "OIL", "USOIL", "UKOIL"}


def _cls(symbol):
    s = str(symbol or "").upper().replace("USDT", "").replace(".P", "")
    if s in _EQUITY:
        return "risk"
    if s in _GOLD:
        return "gold"
    if s in _CRUDE:
        return "crude"
    if str(symbol or "").upper().endswith("USDT"):
        return "risk"                            # cripto = activo de riesgo (BTC y alts)
    return None


def _yields():
    out = {}
    try:
        import yfinance as yf
    except Exception:
        return out
    for tk, name in (("^IRX", "3m"), ("^FVX", "5y"), ("^TNX", "10y")):
        try:
            h = yf.Ticker(tk).history(period="2mo")["Close"].dropna()
            if len(h) < 6:
                continue
            last = float(h.iloc[-1])
            out[name] = {"last": round(last, 3),
                         "d1": round((last - float(h.iloc[-2])) * 100, 1),
                         "d5": round((last - float(h.iloc[-6])) * 100, 1),
                         "d20": round((last - float(h.iloc[-21 if len(h) >= 21 else 0])) * 100, 1),
                         "date": str(h.index[-1].date())}
        except Exception:
            continue
    return out


def _news():
    res = {"hawk": 0, "dove": 0, "n": 0, "headlines": []}
    try:
        u = "https://news.google.com/rss/search?q=Federal+Reserve+interest+rates&hl=en-US&gl=US&ceid=US:en"
        raw = _ur.urlopen(_ur.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read().decode("utf-8", "ignore")
        items = re.findall(r"<item>(.*?)</item>", raw, re.S)
        cutoff = time.time() - 7 * 86400
        for it in items:
            t = re.search(r"<title>(.*?)</title>", it, re.S); d = re.search(r"<pubDate>(.*?)</pubDate>", it)
            if not t:
                continue
            title = html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1))).strip()
            try:
                from email.utils import parsedate_to_datetime
                ts = parsedate_to_datetime(d.group(1)).timestamp() if d else time.time()
            except Exception:
                ts = time.time()
            if ts < cutoff:
                continue
            res["n"] += 1
            hk = bool(_HAWK.search(title)); dv = bool(_DOVE.search(title)) and not _PRESSURE.search(title)
            if hk and not dv:
                res["hawk"] += 1; tag = "H"
            elif dv and not hk:
                res["dove"] += 1; tag = "D"
            else:
                tag = "-"
            if len(res["headlines"]) < 12 and tag != "-":
                res["headlines"].append("[%s] %s" % (tag, title[:110]))
    except Exception as e:
        res["err"] = str(e)[:80]
    return res


def _score(y, n):
    sc = 0.0; why = []
    f = y.get("5y") or y.get("3m") or {}
    d5 = f.get("d5"); d20 = f.get("d20")
    if d5 is not None:
        if d5 >= 8: sc += 2; why.append("tramo corto +%.0f bps/5d (descuenta SUBAS)" % d5)
        elif d5 >= 4: sc += 1; why.append("tramo corto +%.0f bps/5d" % d5)
        elif d5 <= -8: sc -= 2; why.append("tramo corto %.0f bps/5d (descuenta RECORTES)" % d5)
        elif d5 <= -4: sc -= 1; why.append("tramo corto %.0f bps/5d" % d5)
    if d20 is not None:
        if d20 >= 15: sc += 1; why.append("+%.0f bps/20d" % d20)
        elif d20 <= -15: sc -= 1; why.append("%.0f bps/20d" % d20)
    tot = (n.get("hawk", 0) + n.get("dove", 0))
    if tot >= 5:
        ratio = (n["hawk"] - n["dove"]) / float(tot)
        if ratio >= 0.3: sc += 1; why.append("titulares hawkish %d vs dovish %d" % (n["hawk"], n["dove"]))
        elif ratio <= -0.3: sc -= 1; why.append("titulares dovish %d vs hawkish %d" % (n["dove"], n["hawk"]))
    sc = max(-4, min(4, int(round(sc))))
    regime = "HAWKISH" if sc >= 2 else ("DOVISH" if sc <= -2 else "NEUTRAL")
    return sc, regime, why


def context(force=False):
    now = time.time()
    if not force and _MEM["data"] and now - _MEM["ts"] < TTL:
        return _MEM["data"]
    if not force:
        try:
            d = json.load(open(CACHE, encoding="utf-8"))
            if now - float(d.get("ts") or 0) < TTL:
                _MEM.update(ts=now, data=d); return d
        except Exception:
            pass
    y = _yields(); n = _news()
    sc, regime, why = _score(y, n)
    d = {"ts": now, "when": time.strftime("%Y-%m-%d %H:%M"), "regime": regime, "score": sc, "yields": y,
         "news": {k: v for k, v in n.items() if k != "headlines"}, "headlines": n.get("headlines", []),
         "why": why, "note": "%s (%+d): %s" % (regime, sc, " · ".join(why) if why else "sin señal clara")}
    try:
        json.dump(d, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass
    _MEM.update(ts=now, data=d)
    return d


def adjust_for(symbol, side):
    """(delta_conf, nota) por régimen de tasas × clase × lado. 0 si neutral / clase sin peso."""
    try:
        cls = _cls(symbol)
        if cls in (None, "crude"):
            return 0, None
        d = context(); reg = d.get("regime"); sc = abs(int(d.get("score") or 0)); strong = sc >= 3
        if reg == "NEUTRAL":
            return 0, None
        side = str(side or "").upper()
        if cls == "risk":
            tab = {"HAWKISH": {"LONG": -4 if strong else -3, "SHORT": +2}, "DOVISH": {"LONG": +4 if strong else +3, "SHORT": -2}}
        else:  # gold
            tab = {"HAWKISH": {"LONG": -2, "SHORT": +1}, "DOVISH": {"LONG": +2, "SHORT": -1}}
        delta = tab.get(reg, {}).get(side, 0)
        if not delta:
            return 0, None
        return delta, "tasas %s (%+d): %s" % (reg, int(d.get("score") or 0), (d.get("why") or ["-"])[0])
    except Exception:
        return 0, None


if __name__ == "__main__":
    import sys
    d = context(force="--refresh" in sys.argv)
    print(d["when"], d["note"])
    for k, v in (d.get("yields") or {}).items():
        print("  %-3s %.3f  1d %+.1f · 5d %+.1f · 20d %+.1f bps (%s)" % (k, v["last"], v["d1"], v["d5"], v["d20"], v["date"]))
    print("  titulares 7d: n=%d hawk=%d dove=%d" % (d["news"].get("n", 0), d["news"].get("hawk", 0), d["news"].get("dove", 0)))
    for h in d.get("headlines", [])[:8]:
        print("   ", h)
    for s, sd in (("TQQQUSDT", "LONG"), ("TQQQUSDT", "SHORT"), ("BTCUSDT", "LONG"), ("SOLUSDT", "LONG"), ("XAUUSDT", "LONG"), ("CLUSDT", "LONG")):
        print("  %-9s %-5s -> %s" % (s, sd, adjust_for(s, sd)))
