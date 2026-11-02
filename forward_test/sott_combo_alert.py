# -*- coding: utf-8 -*-
"""ALERTA 'CASI SEGURO' (voz + Telegram) — pedido usuario 2026-08-22. Dispara SOLO cuando se alinea TODO el stack
que hace mas probable el movimiento desde el extremo:
  ① CONFLUENCIA DE EXTREMOS: SOTT en zona (src<1020/>1080) + precio en VWAP -/+2.5sigma (rolling 24h) + HTF (4h Y 1d) SOTT en la misma zona.
     (2026-08-24: agregada temporalidad DIARIA (1d) — no es requisito, sube el GRADO a MULTI-TF. Mensaje estructurado LUGAR/FLUJO/GATILLO + objetivo de liquidez.)
  ② GATILLO: el SOTT src CRUZA la OTT viniendo del extremo (giro confirmado).
  ③ FLUJO OK: CVD a favor (delta 3 velas) + POCO flujo en contra.
  ④ IGNICION: volumen >= 2.5x el promedio + vela a favor (el movimiento ARRANCA).
Espeja el indicador sott_azul_giro_vwap (SOTT 200/20/2/2.0). REEMPLAZA las alertas viejas (zscore_alert / imminent_move_alert).
Solo cripto que revierte (el SOTT-reversion PIERDE en TQQQ, medido). Gate asset_quality.alert_ok. NO opera; solo avisa."""
import os, sys, json, time

os.environ.setdefault("ENGINE_TG", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "direction_engine"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import sott_zone as SZ
import imminent_move_alert as IMM       # reuso _klines (Binance+Bitunix), _delta, _load, VOICE
try:
    import telegram_notify as TG
except Exception:
    TG = None
try:
    import asset_quality as AQ
except Exception:
    AQ = None

# canasta = movers cripto que REVIERTEN (donde el SOTT rinde). TQQQ/stocks/petroleo NO (tienden) -> fuera del alerta.
BASKET = ["SUIUSDT", "INJUSDT", "SOLUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "TAOUSDT", "ENAUSDT",
          "WLDUSDT", "GALAUSDT", "ORDIUSDT", "OPUSDT", "ARBUSDT", "XRPUSDT", "LDOUSDT", "APTUSDT", "NEARUSDT"]
CONFBARS = 6         # velas max desde el extremo para validar el cruce
VWAPK = 2.5          # banda VWAP +/- 2.5 sigma
ROLL = 24            # VWAP rolling 24 velas (24h en 1h)
VOLX = 2.5           # ignicion: volumen >= 2.5x avg20
COOLDOWN = 90 * 60   # 90 min por activo+lado
STATE = os.path.join(HERE, "sott_combo_state.json")
VOICE = IMM.VOICE

__ALERT_HUB_PATCHED__ = True   # parche alert_hub 2026-09-04 (no borrar: idempotencia)
def _HUB_OK(prefix, side, sym):
    """Puerta única de alertas: sólo hablan/mandan las celdas (prefijo, lado) validadas por alert_hub. Fail-open."""
    try:
        import alert_hub as _AH
        import time as _t
        return _AH.gate("%s-%s-%s-%d" % (prefix, sym, side, int(_t.time())), sym, side)
    except Exception:
        return True


def _sott(hi, lo, cl):
    """SOTT del Pine (200/20/2/2.0): src (azul) + OTT[2] (violeta)."""
    sto = SZ._stoch(cl, hi, lo, 200)
    k = SZ._var_func(sto, 20)
    src = [x + 1000.0 for x in k]
    mavg = SZ._var_func(src, 2)
    ott, _d = SZ._ott(mavg, 2.0)
    n = len(ott)
    ott2 = [ott[i - 2] if i >= 2 else ott[i] for i in range(n)]
    return src, ott2


def _vwap_bands(K, n, k):
    """VWAP rolling de las ultimas n velas + banda k*sigma (ultima barra)."""
    i = len(K) - 1
    j0 = max(0, i - n + 1)
    sv = spv = sp2 = 0.0
    for j in range(j0, i + 1):
        tp = (K[j][2] + K[j][3] + K[j][4]) / 3.0; v = K[j][5]
        sv += v; spv += tp * v; sp2 += tp * tp * v
    vw = spv / sv if sv > 0 else K[i][4]
    sig = max(sp2 / sv - vw * vw, 0.0) ** 0.5 if sv > 0 else 0.0
    return vw + k * sig, vw - k * sig


def _spoken(sym):
    return sym.replace("USDT", "")


def _fmt(p):
    return ("%.4f" % p) if p < 10 else ("%.2f" % p)


# NOISE-GATE (usuario 2026-08-27): SOLO alertas del LADO EXTERNO operando HACIA el POC/VWAP (reversión al centro).
# Filtra continuación-hacia-la-extensión y alertas del medio del rango -> menos ruido en voz/telegram. Flag para apagar.
TOWARD_CENTER_ONLY = True


def _toward_center_ok(sym, side, K=None):
    """True si la alerta pasa el noise-gate (externo -> centro). Fail-open sin vwap_profile/data."""
    if not TOWARD_CENTER_ONLY:
        return True
    try:
        import vwap_profile as _VP
        ok, _ = _VP.alert_toward_center(sym, side, K=K)
        return ok
    except Exception:
        return True


def _nivel_tg(sym, side):
    """Linea TG con el PERFIL 24h (POC iman + VWAP sigma + delta del nivel). CONTEXTO (no gatea). '' si no hay data."""
    try:
        import vwap_profile as _VP
        tag, txt = _VP.line(sym, side)
        return ("\U0001f4ca %s: %s\n" % (tag, txt)) if tag else ""
    except Exception:
        return ""


def _nivel_voz(sym, side):
    """Fragmento de voz corto con el nivel (POC a favor/en contra + si esta estirado en VWAP)."""
    try:
        import vwap_profile as _VP
        p = _VP.profile(sym)
        if not p:
            return ""
        toward = "a favor" if (side == "LONG") == (p["poc_dist"] > 0) else "en contra"
        ext = ", estirado en v-wap" if abs(p["sig_pos"]) >= _VP.SIGMA_K else ""
        return ", perfil con poc %s%s" % (toward, ext)
    except Exception:
        return ""


def _emit(sym, side, volx, px, comp, liq=None):
    if not _HUB_OK("sottcombo", side, sym): return
    now = time.time(); sp = _spoken(sym); word = "long" if side == "LONG" else "short"
    zona = "piso" if side == "LONG" else "techo"
    liq_ok = (liq is not None and liq > 0)          # imán de liquidez a favor (pata 'liq' de la convicción)
    star = " ⭐ con liquidez" if liq_ok else ""
    liqvoz = (", con objetivo de liquidez en mas %.1f por ciento" % liq) if liq_ok else ""
    objetivo = ("\n\U0001f4b0 objetivo liquidez (dónde apuntar): +%.2f%%" % liq) if liq_ok else "\n\U0001f4b0 sin imán de liquidez claro a favor"
    _sync_lv = ""; _sync_dt = ""
    try:
        import sync_check as _SC
        _sync_lv, _sync_dt = _SC.line(sym, side)   # veredicto SYNC (imán+régimen+flujo) como CONTEXTO
    except Exception:
        _sync_lv = ""; _sync_dt = ""
    # VOZ (arranca con 'long en'/'short en' para pasar el whitelist del dashboard)
    try:
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "sottcombo-%s-%s-%d" % (sym, side, int(now)), "asset": sym, "side": side,
                       "text": "%s en %s, casi seguro%s, extremo SOTT en 5, 15 y 60 minutos, VWAP con cruce y volumen desde %s" % (word, sp, liqvoz, zona),
                       "ts": int(now)})
        alerts = alerts[-30:]
        json.dump(alerts, open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    # TELEGRAM (prioritario) — estructura LUGAR / FLUJO / GATILLO (checklist manual)
    if TG is not None:
        try:
            emo = "\U0001f7e2 LONG" if side == "LONG" else "\U0001f534 SHORT"
            _tg_sync = ("\U0001f517 <b>SYNC</b>: %s — %s\n" % (_sync_lv, _sync_dt)) if _sync_lv else ""
            _tg_niv = _nivel_tg(sym, side)
            TG._send("\U0001f3af <b>%s %s — CASI SEGURO%s</b>\n"
                     "\U0001f4cd <b>LUGAR</b>: extremo SOTT <b>5m+15m+1h</b> + VWAP2.5σ (%s)\n"
                     "\U0001f30a <b>FLUJO</b>: CVD a favor + ignición vol %.1fx\n"
                     "\U0001f52b <b>GATILLO</b>: cruce SOTT desde el %s%s\n"
                     "%s%s"
                     "precio $%s"
                     % (sp, emo, star, zona, volx, zona, objetivo, _tg_sync, _tg_niv, _fmt(px)),
                     force=True, zalert=True)
        except Exception:
            pass


def _sott_extreme_recent(sym, tf, up, bars=10):
    """True si el SOTT del tf TOCO la zona extrema (piso<1020 / techo>1080) en las ultimas `bars` barras cerradas.
    None si no hay data. Los TF rapidos recuperan antes que el 1h -> se mide 'toco reciente', no 'ahora mismo'."""
    K = IMM._klines(sym, tf, 400)
    if not K or len(K) < 300:
        return None
    Kc = K[:-1]
    src, _o = _sott([b[2] for b in Kc], [b[3] for b in Kc], [b[4] for b in Kc])
    seg = src[-bars:] if len(src) >= bars else src
    return any(x < 1020 for x in seg) if up else any(x > 1080 for x in seg)


def scan(dry=False, verbose=False):
    st = IMM._load(STATE, {}); now = time.time(); fired = 0; rows = []
    for sym in BASKET:
        try:
            if AQ is not None and not AQ.alert_ok(sym):
                continue
            K = IMM._klines(sym, "1h", 400)
            if not K or len(K) < 300:
                continue
            Kc = K[:-1]                                     # solo barras cerradas
            hi = [b[2] for b in Kc]; lo = [b[3] for b in Kc]; cl = [b[4] for b in Kc]; op = [b[1] for b in Kc]; v = [b[5] for b in Kc]
            src, ott2 = _sott(hi, lo, cl)
            i = len(Kc) - 1
            # ② GATILLO: cruce del SOTT viniendo del extremo
            cameCheap = any(src[j] < 1020 for j in range(max(0, i - CONFBARS), i + 1))
            cameExp = any(src[j] > 1080 for j in range(max(0, i - CONFBARS), i + 1))
            crossUp = src[i] > ott2[i] and src[i - 1] <= ott2[i - 1] and cameCheap
            crossDn = src[i] < ott2[i] and src[i - 1] >= ott2[i - 1] and cameExp
            side = "LONG" if crossUp else ("SHORT" if crossDn else None)
            if side is None:
                continue
            up = side == "LONG"
            # ① CONFLUENCIA: VWAP -/+2.5sigma tocada
            vb_up, vb_dn = _vwap_bands(Kc, ROLL, VWAPK)
            vwap_ok = (up and lo[i] <= vb_dn) or (not up and hi[i] >= vb_up)
            # ① EXTREMO SOTT MULTI-TF (2026-08-25, pedido usuario "extremo SOTT en 5m+15m+1h"): el 1h ya tocó el extremo
            # (cameCheap/cameExp del cruce); exijo que el SOTT también haya tocado la MISMA zona extrema RECIENTE en 5m y 15m.
            # Reemplaza al HTF 4h/1d. (TF rápidos recuperan antes -> se mide 'tocó en últimas ~10 barras', no 'ahora'.)
            mtf5_ok = _sott_extreme_recent(sym, "5m", up)
            mtf15_ok = _sott_extreme_recent(sym, "15m", up)
            mtf4h_ok = _sott_extreme_recent(sym, "4h", up)     # superior: no requisito, sube el grado MULTI-TF
            mtf1d_ok = _sott_extreme_recent(sym, "1d", up)     # superior: no requisito, sube el grado MULTI-TF
            mtf_ok = bool(mtf5_ok) and bool(mtf15_ok)
            # ③ FLUJO OK: CVD a favor (3 velas) + poco flujo en contra
            d = [IMM._delta(Kc, j) for j in range(len(Kc))]
            fav = sum(max(0.0, (dd if up else -dd)) for dd in d[i - 2:i + 1])
            opp = sum(max(0.0, (-dd if up else dd)) for dd in d[i - 2:i + 1])
            cvd_ok = fav > 0 and opp <= 0.5 * fav
            # ④ IGNICION: volumen >= 2.5x + vela a favor
            avgv = sum(v[i - 20:i]) / 20 if i >= 20 else 0
            volx = v[i] / avgv if avgv > 0 else 0
            candle = (up and cl[i] > op[i]) or (not up and cl[i] < op[i])
            ignit = volx >= VOLX and candle
            comp = ["cruce-1h", "SOTT-1h"]
            if mtf5_ok: comp.append("SOTT-5m")
            if mtf15_ok: comp.append("SOTT-15m")
            if mtf4h_ok: comp.append("SOTT-4h")
            if mtf1d_ok: comp.append("SOTT-1d")
            if vwap_ok: comp.append("VWAP2.5s")
            if cvd_ok: comp.append("CVD-ok")
            if ignit: comp.append("ignicion")
            # CASI SEGURO = extremo SOTT 5m+15m+1h + VWAP2.5σ + cruce + CVD + ignición (TODO requerido).
            ok = mtf_ok and vwap_ok and cvd_ok and ignit
            rows.append((sym, side, round(volx, 1), mtf5_ok, mtf15_ok, vwap_ok, cvd_ok, ignit, ok))
            if dry:
                continue
            if not ok:
                continue
            s = st.get(sym, {})
            if s.get("side") == side and (now - s.get("ts", 0)) < COOLDOWN:
                continue
            if not _toward_center_ok(sym, side):     # NOISE-GATE: solo externo->centro (POC/VWAP)
                continue
            # objetivo de liquidez (donde apuntar, checklist) — solo al disparar, evita llamadas en el barrido
            liq = None
            try:
                import bybit_exec as _bx
                liq = _bx._liq_tp_pct(sym, side, cl[i])
            except Exception:
                pass
            _emit(sym, side, volx, cl[i], comp, liq)
            st[sym] = {"side": side, "ts": now}; fired += 1
        except Exception:
            continue
    if not dry:
        try:
            json.dump(st, open(STATE, "w"))
        except Exception:
            pass
    if verbose or dry:
        print("=== SOTT COMBO 'casi seguro' (extremo SOTT 5m+15m+1h + VWAP2.5 + cruce + CVD + ignicion) ===")
        print("%-10s %-5s %5s %5s %5s %5s %5s %5s %4s" % ("sym", "side", "volx", "5m", "15m", "VWAP", "CVD", "IGN", "GO"))
        for r in rows:
            print("%-10s %-5s %5.1f %5s %5s %5s %5s %5s %4s" % (r[0], r[1], r[2], "Y" if r[3] else "-", "Y" if r[4] else "-",
                                                                "Y" if r[5] else "-", "Y" if r[6] else "-", "Y" if r[7] else "-", "GO" if r[8] else "-"))
    return fired


# ============================================================================
# ALERTA 'SOTT MTF EXTREMO' (pedido usuario 2026-08-24): avisa cuando el SOTT coincide en ZONA EXTREMA en 5m, 15m Y 1h
# a la vez (confluencia intradia pura). Heads-up de zona de reversion PROBABLE — mas debil que 'CASI SEGURO' (no exige
# cruce/VWAP/ignicion), pero util para vigilar. Edge-triggered (latch) + cooldown 60min para no spamear mientras persiste.
MTF_TFS = ("5m", "15m", "1h")
MTF_STATE = os.path.join(HERE, "sott_mtf_state.json")
MTF_COOLDOWN = 60 * 60      # 60 min por simbolo+lado


def _sott_last(sym, tf, limit=400):
    """SOTT src (azul) de la ULTIMA barra cerrada en tf, o None si no hay data suficiente."""
    K = IMM._klines(sym, tf, limit)
    if not K or len(K) < 300:
        return None
    Kc = K[:-1]
    src, _o = _sott([b[2] for b in Kc], [b[3] for b in Kc], [b[4] for b in Kc])
    return src[-1]


def _emit_mtf(sym, side, vals, htf=None):
    if not _HUB_OK("sottmtf", side, sym): return
    now = time.time(); sp = _spoken(sym); word = "long" if side == "LONG" else "short"
    zona = "piso" if side == "LONG" else "techo"
    vtxt = " · ".join("%s %.0f" % (tf, vals[tf]) for tf in MTF_TFS)
    htf = htf or []
    _htfvoz = {"4h": "4 horas", "1d": "1 día"}
    htfvoz = (", confirmado también en " + " y ".join(_htfvoz[h] for h in htf)) if htf else ""
    htftg = "".join(" + %s" % h for h in htf)
    grade = "MULTI-TF" if htf else "EXTREMO"
    # VOZ (arranca con 'long en'/'short en' para el whitelist del dashboard)
    try:
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "sottmtf-%s-%s-%d" % (sym, side, int(now)), "asset": sym, "side": side,
                       "text": "%s en %s, SOTT en zona %s alineado en 5 minutos, 15 minutos y 1 hora%s" % (word, sp, zona, htfvoz),
                       "ts": int(now)})
        alerts = alerts[-30:]
        json.dump(alerts, open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    # TELEGRAM
    if TG is not None:
        try:
            emo = "\U0001f7e2 LONG" if side == "LONG" else "\U0001f534 SHORT"
            _slv = ""; _sdt = ""
            try:
                import sync_check as _SC
                _slv, _sdt = _SC.line(sym, side)
            except Exception:
                _slv = ""; _sdt = ""
            _tg_sync = ("\U0001f517 SYNC: <b>%s</b> — %s\n" % (_slv, _sdt)) if _slv else ""
            _tg_niv = _nivel_tg(sym, side)
            TG._send("\U0001f4ca <b>%s %s — SOTT %s</b>\n"
                     "SOTT en <b>%s</b> alineado en <b>5m + 15m + 1h%s</b>\n"
                     "src: %s\n%s%s<i>heads-up de reversión — esperá cruce/ignición para confirmar</i>"
                     % (sp, emo, grade, zona, htftg, vtxt, _tg_sync, _tg_niv), force=True, zalert=True)
        except Exception:
            pass


def scan_mtf(dry=False, verbose=False):
    """Dispara cuando el SOTT esta en la MISMA zona extrema (piso<1020 / techo>1080) en 5m, 15m y 1h a la vez."""
    st = IMM._load(MTF_STATE, {}); now = time.time(); fired = 0; rows = []
    for sym in BASKET:
        try:
            # SIN gate AQ.alert_ok aca (a diferencia de 'casi seguro'): este es un heads-up TECNICO de zona -> aplica a
            # TODA la canasta de movers que revierten (incluye TAO, activo principal del usuario). La canasta ya es el filtro.
            vals = {}; ok_data = True
            for tf in MTF_TFS:
                v = _sott_last(sym, tf)
                if v is None:
                    ok_data = False; break
                vals[tf] = v
            if not ok_data:
                continue
            allCheap = all(vals[tf] < 1020 for tf in MTF_TFS)   # todas en piso -> LONG
            allExp = all(vals[tf] > 1080 for tf in MTF_TFS)     # todas en techo -> SHORT
            side = "LONG" if allCheap else ("SHORT" if allExp else None)
            htf = []
            if side is not None:
                for _htf in ("4h", "1d"):                       # superiores: no requisito, suben el GRADO a MULTI-TF
                    hv = _sott_last(sym, _htf)
                    if hv is not None and ((side == "LONG" and hv < 1020) or (side == "SHORT" and hv > 1080)):
                        htf.append(_htf)
            rows.append((sym, vals, side))
            if dry or side is None:
                continue
            s = st.get(sym, {})
            if s.get("side") == side and (now - s.get("ts", 0)) < MTF_COOLDOWN:
                continue
            if not _toward_center_ok(sym, side):     # NOISE-GATE: solo externo->centro
                continue
            _emit_mtf(sym, side, vals, htf)
            st[sym] = {"side": side, "ts": now}; fired += 1
        except Exception:
            continue
    if not dry:
        try:
            json.dump(st, open(MTF_STATE, "w"))
        except Exception:
            pass
    if verbose or dry:
        print("=== SOTT MTF EXTREMO (5m + 15m + 1h en la MISMA zona) ===")
        print("%-10s %6s %6s %6s  %s" % ("sym", "5m", "15m", "1h", "senal"))
        for sym, vals, side in rows:
            print("%-10s %6.0f %6.0f %6.0f  %s" % (sym, vals["5m"], vals["15m"], vals["1h"], side or "-"))
    return fired


# ============================================================================
# ALERTA 'MOMENTUM MTF' (pedido usuario 2026-08-25): avisa (voz+telegram) cuando el MOMENTUM apunta a la MISMA direccion
# en 5m Y 15m = confluencia de continuacion (distinta a la reversion 'CASI SEGURO'). Momentum = EMA9 vs EMA21 + precio del
# lado + pendiente EMA9, con vol-expansion en 5m (espeja la tesis del brazo mom, que es +EV). Edge-triggered + cooldown.
MOM_TFS = ("5m", "15m")
MOM_STATE = os.path.join(HERE, "sott_mom_state.json")
MOM_COOLDOWN = 45 * 60      # 45 min por simbolo+lado
MOM_VOLX = 1.5              # el momentum debe venir CON volumen (vela 5m >= 1.5x avg20). MEDIDO: subir a 2.5x NO ayuda.
# UNIVERSO momentum (2026-08-25, medido 3452 señales: follow-through a +1% >=60%). Fuera AVAX/LINK/SOL/DOGE/ARB (<=57%).
MOM_UNIVERSE = ["ENAUSDT", "XRPUSDT", "GALAUSDT", "ORDIUSDT", "APTUSDT", "TAOUSDT", "LDOUSDT",
                "WLDUSDT", "OPUSDT", "SUIUSDT", "NEARUSDT", "INJUSDT"]


def _mom_universe():
    """Universo de momentum: lee mom_universe.json (recalibrado c/3 días por mom_universe_calib.py); fallback a la lista fija."""
    try:
        d = json.load(open(os.path.join(HERE, "mom_universe.json")))
        u = d.get("universe") or []
        return u if len(u) >= 4 else MOM_UNIVERSE
    except Exception:
        return MOM_UNIVERSE


def _ema(xs, n):
    k = 2.0 / (n + 1.0); e = xs[0]; out = [e]
    for x in xs[1:]:
        e = x * k + e * (1.0 - k); out.append(e)
    return out


def _mom_dir(K):
    """(dir, volx): momentum del TF = EMA9 vs EMA21 + precio del lado + pendiente EMA9. dir None si mixto/sin data."""
    if not K or len(K) < 40:
        return (None, 0.0)
    Kc = K[:-1]
    c = [b[4] for b in Kc]; v = [b[5] for b in Kc]
    ef = _ema(c, 9); es = _ema(c, 21); i = len(c) - 1
    avgv = sum(v[i - 20:i]) / 20 if i >= 20 else 0
    volx = v[i] / avgv if avgv > 0 else 0.0
    up = ef[i] > es[i] and c[i] > es[i] and ef[i] > ef[i - 1]
    dn = ef[i] < es[i] and c[i] < es[i] and ef[i] < ef[i - 1]
    return ("LONG" if up else ("SHORT" if dn else None), round(volx, 1))


def _emit_momentum(sym, side, volx, aligned, mkt="zona SOTT neutra"):
    if not _HUB_OK("sottmom", side, sym): return
    now = time.time(); sp = _spoken(sym); word = "long" if side == "LONG" else "short"
    dirw = "alcista" if side == "LONG" else "bajista"
    ORDER = ["5m", "15m", "1h"]; aligned = [t for t in ORDER if t in aligned]
    multitf = len(aligned) == 3
    tfs = " + ".join(aligned)
    _vozmap = {"5m": "5 minutos", "15m": "15 minutos", "1h": "1 hora"}
    tfvoz = " y ".join(_vozmap[t] for t in aligned)
    star = " ⭐ 3-TF" if multitf else " (2 de 3)"
    _sync_lv = ""; _sync_dt = ""
    try:
        import sync_check as _SC
        _sync_lv, _sync_dt = _SC.line(sym, side)   # veredicto del bot: IMAN de liquidez + REGIMEN + flujo (lo que Pine no ve)
    except Exception:
        _sync_lv = ""; _sync_dt = ""
    _voz_sync = (", sincronía %s" % _sync_lv.lower()) if _sync_lv else ""
    _voz_niv = _nivel_voz(sym, side)
    try:
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "sottmom-%s-%s-%d" % (sym, side, int(now)), "asset": sym, "side": side,
                       "text": "%s en %s, momentum %s alineado en %s, con volumen, mercado en %s%s%s" % (word, sp, dirw, tfvoz, mkt, _voz_sync, _voz_niv),
                       "ts": int(now)})
        alerts = alerts[-30:]
        json.dump(alerts, open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    if TG is not None:
        try:
            emo = "\U0001f7e2 LONG" if side == "LONG" else "\U0001f534 SHORT"
            _tg_sync = ("\U0001f517 SYNC: <b>%s</b> — %s\n" % (_sync_lv, _sync_dt)) if _sync_lv else ""
            _tg_niv = _nivel_tg(sym, side)
            TG._send("\U0001f680 <b>%s %s — MOMENTUM MTF%s</b>\n"
                     "momentum <b>%s</b> alineado en <b>%s</b> · vol %.1fx\n"
                     "\U0001f9ed estado: <b>%s</b>\n"
                     "%s%s"
                     "<i>continuación en curso (no reversión) — gatillo momentum + estado SOTT</i>"
                     % (sp, emo, star, dirw, tfs, volx, mkt, _tg_sync, _tg_niv), force=True, zalert=True)
        except Exception:
            pass


def scan_momentum(dry=False, verbose=False):
    """Momentum en >=2 de {5m,15m,1h} + vol 5m, con el 1h OBLIGATORIO (medido: 1h a favor=66% llega a +1% vs 55%), solo en
    el UNIVERSO de movers con follow-through >=60%. 3-de-3 = ⭐. (Filtros 2026-08-25 para subir prob de +1% ~62%->70%.)"""
    st = IMM._load(MOM_STATE, {}); now = time.time(); fired = 0; rows = []
    for sym in _mom_universe():
        try:
            d5, vx5 = _mom_dir(IMM._klines(sym, "5m", 300))
            d15, _v15 = _mom_dir(IMM._klines(sym, "15m", 300))
            d1h, _v1h = _mom_dir(IMM._klines(sym, "1h", 300))
            # MAYORIA 2-de-3: al menos 2 de {5m,15m,1h} en la misma direccion. 3-de-3 = grado ⭐.
            _dirs = {"5m": d5, "15m": d15, "1h": d1h}
            longs = [tf for tf, d in _dirs.items() if d == "LONG"]
            shorts = [tf for tf, d in _dirs.items() if d == "SHORT"]
            if len(longs) >= 2:
                side = "LONG"; aligned = longs
            elif len(shorts) >= 2:
                side = "SHORT"; aligned = shorts
            else:
                side = None; aligned = []
            # FILTRO 1h OBLIGATORIO (medido 3452 señales): la mayoría DEBE incluir el 1h -> mata las "5m+15m contra el 1h"
            # (las de peor follow-through). El 1h a favor sube la prob de +1% de 55% a 66%.
            if side is not None and "1h" not in aligned:
                side = None
            # ESTADO SOTT (momentum=GATILLO, SOTT=ESTADO). (2026-08-26) NO se descarta la señal por el estado: MEDIDO sobre
            # 456 trades que momentum HACIA el extremo (FAST_OPP) es el MEJOR bucket (E+1.46) = tendencia caliente, no
            # agotamiento. El estado (5m/15m/1h/4h/1d) va SOLO como CONTEXTO en el mensaje, no filtra.
            _mkt = "zona SOTT neutra"
            if side is not None:
                _sv = [_sott_last(sym, t) for t in ("5m", "15m", "1h", "4h", "1d")]   # TODAS las TFs SOTT (solo info)
                _hi = lambda v: (v is not None and v > 1080)   # SOTT en techo (sobrecompra)
                _lo = lambda v: (v is not None and v < 1020)   # SOTT en piso (sobreventa)
                if side == "LONG" and any(_lo(v) for v in _sv):
                    _mkt = "rebote desde piso SOTT"          # arranca desde sobreventa (fresco)
                elif side == "SHORT" and any(_hi(v) for v in _sv):
                    _mkt = "caída desde techo SOTT"          # arranca desde sobrecompra (fresco)
                elif side == "LONG" and any(_hi(v) for v in _sv):
                    _mkt = "tendencia caliente (SOTT en techo)"   # surfeando fuerza (mejor E histórico)
                elif side == "SHORT" and any(_lo(v) for v in _sv):
                    _mkt = "tendencia caliente (SOTT en piso)"
            rows.append((sym, d5, d15, d1h, vx5, side, len(aligned)))
            if dry or side is None:
                continue
            if vx5 < MOM_VOLX:                          # momentum debe venir con volumen (vela 5m)
                continue
            s = st.get(sym, {})
            if s.get("side") == side and (now - s.get("ts", 0)) < MOM_COOLDOWN:
                continue
            if not _toward_center_ok(sym, side):     # NOISE-GATE: momentum solo si va HACIA el POC/VWAP (filtra continuación a la extensión)
                continue
            _emit_momentum(sym, side, vx5, aligned, _mkt)
            st[sym] = {"side": side, "ts": now}; fired += 1
        except Exception:
            continue
    if not dry:
        try:
            json.dump(st, open(MOM_STATE, "w"))
        except Exception:
            pass
    if verbose or dry:
        print("=== MOMENTUM MTF (2-de-3 con 1h OBLIGATORIO + vol; universo FT>=60%; 3-de-3=⭐) ===")
        print("%-10s %6s %6s %6s %6s  %s" % ("sym", "5m", "15m", "1h", "vol5m", "senal"))
        for sym, d5, d15, d1h, vx5, side, cnt in rows:
            tag = (side + ("⭐" if cnt == 3 else "(2)")) if side else "-"
            print("%-10s %6s %6s %6s %6.1f  %s" % (sym, d5 or "-", d15 or "-", d1h or "-", vx5, tag))
    return fired


# ============================================================================
# ALERTA 'CONFLUENCIA SOTT MULTI-TF' (pedido usuario 2026-08-25): mismas señales de la sección del dashboard = SOTT nuevo
# coincide en 5m+15m (base) + superiores 1h/2h/4h/1d (hasta 6 TFs, más = mejor) + REQUISITO imán de liquidación a favor
# >=1%. Lee /api/sott_confluence (ya computado por el dashboard c/60s) -> coincide EXACTO y es liviano. Voz+telegram.
SCONF_STATE = os.path.join(HERE, "sott_conf_state.json")
SCONF_COOLDOWN = 60 * 60      # 60 min por símbolo+lado (re-alerta si SUBE de grado)
SCONF_MIN_TF = 2              # avisar desde x2 (5m+15m). Subir a 3 = solo triples+


def _emit_sconf(sym, side, n_tf, dist, tfs):
    if not _HUB_OK("sottconf", side, sym): return
    now = time.time(); sp = _spoken(sym); word = "long" if side == "LONG" else "short"
    zona = "piso" if side == "LONG" else "techo"
    star = " ⭐" if (n_tf or 0) >= 4 else ""
    fuerte = ", fuerte" if (n_tf or 0) >= 4 else ""
    liqtxt = ("\n\U0001f4b0 liquidación a favor @%+.2f%%" % dist) if dist is not None else ""
    try:
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "sottconf-%s-%s-%d" % (sym, side, int(now)), "asset": sym, "side": side,
                       "text": "%s en %s, confluencia SOTT en %d temporalidades%s, con liquidación a favor" % (word, sp, n_tf or 0, fuerte),
                       "ts": int(now)})
        alerts = alerts[-30:]
        json.dump(alerts, open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    if TG is not None:
        try:
            emo = "\U0001f7e2 LONG" if side == "LONG" else "\U0001f534 SHORT"
            TG._send("\U0001f4ca <b>%s %s — CONFLUENCIA SOTT x%d%s</b>\n"
                     "SOTT en zona %s: <b>%s</b>%s"
                     % (sp, emo, n_tf or 0, star, zona, tfs or "", liqtxt), force=True, zalert=True)
        except Exception:
            pass


def scan_sott_confluence(dry=False, verbose=False):
    """Lee /api/sott_confluence del dashboard (SOTT nuevo 5m+15m + superiores + imán liquidación >=1%) y alerta las nuevas."""
    st = IMM._load(SCONF_STATE, {}); now = time.time(); fired = 0; rows = []
    try:
        import urllib.request as _u
        rows = (json.load(_u.urlopen("http://localhost:8890/api/sott_confluence", timeout=8)) or {}).get("rows", [])
    except Exception:
        rows = []
    for x in rows:
        try:
            sym = x.get("symbol"); side = x.get("side"); n_tf = x.get("n_tf") or 2
            if not sym or side not in ("LONG", "SHORT") or n_tf < SCONF_MIN_TF:
                continue
            if not x.get("liq_ok"):
                continue                      # FIX 2026-08-26: el dashboard ahora MUESTRA también las confluencias con la
                #                              liquidez EN CONTRA (marcadas, para que el panel no quede vacío). La ALERTA solo
                #                              debe disparar cuando la liquidez va REALMENTE A FAVOR ≥1% (liq_ok). MU SHORT con
                #                              imán LONG @+0.90% = en contra -> ya NO alerta ("no tiene sentido que esté arriba").
            if dry:
                continue
            s_ = st.get(sym, {})
            if s_.get("side") == side and s_.get("n_tf", 0) >= n_tf and (now - s_.get("ts", 0)) < SCONF_COOLDOWN:
                continue                      # ya avisada (mismo lado, no subió de grado) dentro del cooldown
            if not _toward_center_ok(sym, side):     # NOISE-GATE: solo externo->centro
                continue
            _emit_sconf(sym, side, n_tf, x.get("liq_dist"), x.get("tfs_str"))
            st[sym] = {"side": side, "n_tf": n_tf, "ts": now}; fired += 1
        except Exception:
            continue
    if not dry:
        try:
            json.dump(st, open(SCONF_STATE, "w"))
        except Exception:
            pass
    if verbose or dry:
        print("=== CONFLUENCIA SOTT MULTI-TF (del dashboard) ===")
        for x in rows:
            print("  %-10s %-5s x%s TFs[%s] liq@%s%%" % (x.get("symbol"), x.get("side"), x.get("n_tf"), x.get("tfs_str"), x.get("liq_dist")))
    return fired


# ============================================================================
# ALERTA 'IGNICIÓN' (pedido usuario 2026-08-26 "avisá CUANDO INICIAN los movimientos, no tarde"): tier RÁPIDO de ARRANQUE.
# A diferencia de las de CONFIRMACIÓN (esperan varias velas CERRADAS -> hasta ~1h tarde), esta mira la vela 5m EN FORMACIÓN
# y dispara apenas hay VOLUMEN + MOVIMIENTO en una dirección = el movimiento ARRANCANDO. Temprano = algo más de falsas.
IGN_STATE = os.path.join(HERE, "sott_ign_state.json")
IGN_COOLDOWN = 25 * 60      # 25 min por símbolo+lado
IGN_VOLX = 1.6              # volumen de la vela en curso >= 1.6x el promedio de las 20 cerradas
IGN_MOVE = 0.45             # y el precio ya se movió >= 0.45% en la vela (cuerpo)


def _emit_ignition(sym, side, volx, move):
    if not _HUB_OK("ign", side, sym): return
    now = time.time(); sp = _spoken(sym); word = "long" if side == "LONG" else "short"
    dirw = "subiendo" if side == "LONG" else "cayendo"
    try:
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "ign-%s-%s-%d" % (sym, side, int(now)), "asset": sym, "side": side,
                       "text": "%s en %s, ignición, %s con volumen, %.1f por ciento" % (word, sp, dirw, abs(move)),
                       "ts": int(now)})
        alerts = alerts[-30:]
        json.dump(alerts, open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    if TG is not None:
        try:
            emo = "\U0001f7e2 LONG" if side == "LONG" else "\U0001f534 SHORT"
            TG._send("\U0001f525 <b>%s %s — IGNICIÓN (arrancando)</b>\n"
                     "vela 5m en curso: <b>%+.2f%%</b> · vol <b>%.1fx</b>\n"
                     "<i>aviso TEMPRANO del arranque — NO confirmado (puede revertir), esperá cruce/confluencia para peso</i>"
                     % (sp, emo, move, volx), force=True, zalert=True)
        except Exception:
            pass


def scan_ignition(dry=False, verbose=False):
    """Vela 5m EN FORMACIÓN con volumen >=IGN_VOLX y cuerpo >=IGN_MOVE% -> el movimiento ARRANCANDO (aviso temprano)."""
    st = IMM._load(IGN_STATE, {}); now = time.time(); fired = 0; rows = []
    universe = list(dict.fromkeys(list(MOM_UNIVERSE) + list(BASKET)))
    for sym in universe:
        try:
            K = IMM._klines(sym, "5m", 40)
            if not K or len(K) < 25:
                continue
            b = K[-1]                                          # vela EN FORMACIÓN
            o, c, v = b[1], b[4], b[5]
            avgv = sum(x[5] for x in K[-21:-1]) / 20
            if avgv <= 0 or not o:
                continue
            volx = v / avgv
            move = (c - o) / o * 100
            side = "LONG" if move >= IGN_MOVE else ("SHORT" if move <= -IGN_MOVE else None)
            if side is None or volx < IGN_VOLX:
                continue
            rows.append((sym, side, volx, move))
            if dry:
                continue
            s = st.get(sym, {})
            if s.get("side") == side and (now - s.get("ts", 0)) < IGN_COOLDOWN:
                continue
            if not _toward_center_ok(sym, side):     # NOISE-GATE: ignición solo si arranca HACIA el POC/VWAP
                continue
            _emit_ignition(sym, side, volx, move)
            st[sym] = {"side": side, "ts": now}; fired += 1
        except Exception:
            continue
    if not dry:
        try:
            json.dump(st, open(IGN_STATE, "w"))
        except Exception:
            pass
    if verbose or dry:
        print("=== IGNICIÓN (vela 5m en formación: vol>=%.1fx + cuerpo>=%.2f%%) ===" % (IGN_VOLX, IGN_MOVE))
        for sym, side, volx, move in rows:
            print("  %-10s %-5s vol %.1fx  move %+.2f%%" % (sym, side, volx, move))
    return fired


# ============================================================================
# ALERTA 'FADE VWAP 2.5σ' (pedido usuario 2026-08-27, "le tengo fe al 2.5σ"): fade del extremo hacia el VWAP/POC.
# MEDIDO (bt_vwap_fade.py, 18 activos 25d): el 2.5σ SOLO con TP fijo = E≈0; pero con SALIDA TRAILING (runner) da +E que
# aguanta OOS (base +0.44/WR58 n216; + RÁPIDO/GIRO sube más). El extremo da MFE 4-5% -> hay que DEJAR CORRER, no TP chico.
# Trigger: |VWAP σ|>=2.5 en 5m + (movió RÁPIDO >=1.5%/6velas hacia el extremo O GIRO = última vela con delta en contra).
# Es HEADS-UP MANUAL (+ brazo paper vwap_fade_paper que lo mide en vivo); NO gatea las estrategias.
REVP_STATE = os.path.join(HERE, "sott_revpoc_state.json")
REVP_COOLDOWN = 45 * 60      # 45 min por símbolo+lado


def _emit_revpoc(sym, side, r):
    if not _HUB_OK("revpoc", side, sym): return
    now = time.time(); sp = _spoken(sym); word = "long" if side == "LONG" else "short"
    zona = "arriba, estirado en v-wap" if side == "SHORT" else "abajo, estirado en v-wap"
    _isdelta = r.get("delta")
    conf = ("delta %s en el extremo" % ("vendedor" if side == "SHORT" else "comprador")) if _isdelta else (
        "movimiento rápido" if r["fast"] else "giro de flujo")
    try:
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "revpoc-%s-%s-%d" % (sym, side, int(now)), "asset": sym, "side": side,
                       "text": "%s en %s, fade del extremo, %s, %s, dejá correr al v-wap" % (word, sp, zona, conf),
                       "ts": int(now)})
        alerts = alerts[-30:]
        json.dump(alerts, open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    if TG is not None:
        try:
            emo = "\U0001f7e2 LONG" if side == "LONG" else "\U0001f534 SHORT"
            conftag = ("DELTA %s en el extremo" % ("vendedor" if side == "SHORT" else "comprador")) if _isdelta else (
                ("RÁPIDO %+.1f%%/6v" % r["vel6"]) + (" + GIRO" if r["giro"] else "") if r["fast"] else "GIRO de flujo")
            TG._send("\U0001f3af <b>%s %s — FADE VWAP %.1fσ</b>\n"
                     "\U0001f4cd <b>LUGAR</b>: %s (VWAP <b>%+.1fσ</b>)\n"
                     "\U0001f30a <b>FLUJO</b>: %s\n"
                     "\U0001f3af <b>OBJETIVO</b>: VWAP/POC %s (%+.2f%%) — <b>salida TRAILING</b> (dejá correr)\n"
                     "<i>fade de extremo (WR~60, alto MFE) — MANUAL, SL ~1.5%%, no gatea estrategias</i>"
                     % (sp, emo, abs(r["sig_pos"]), zona, r["sig_pos"], conftag, _fmt(r["target"]), r["dist_target"]),
                     force=True, zalert=True)
        except Exception:
            pass


def scan_revpoc(dry=False, verbose=False):
    """|VWAP σ|>=2.5 en 5m + (RÁPIDO o GIRO) -> heads-up de fade del extremo (salida trailing). Trigger validado."""
    st = IMM._load(REVP_STATE, {}); now = time.time(); fired = 0; rows = []
    try:
        import vwap_profile as _VP
    except Exception:
        return 0
    universe = list(dict.fromkeys(list(MOM_UNIVERSE) + list(BASKET)))
    for sym in universe:
        try:
            r = _VP.fade_signal(sym)                        # |σ|>=2.5 + (rápido o giro), ya validado
            if not r:
                r = _VP.extreme_delta_signal(sym)           # camino DELTA (usuario): extremo + delta del nivel confirmando
            if not r:
                continue
            rows.append((sym, r["side"], r["sig_pos"], r["dist_target"], r["fast"], r["giro"]))
            if dry:
                continue
            s = st.get(sym, {})
            if s.get("side") == r["side"] and (now - s.get("ts", 0)) < REVP_COOLDOWN:
                continue
            _emit_revpoc(sym, r["side"], r)
            st[sym] = {"side": r["side"], "ts": now}; fired += 1
        except Exception:
            continue
    if not dry:
        try:
            json.dump(st, open(REVP_STATE, "w"))
        except Exception:
            pass
    if verbose or dry:
        print("=== FADE VWAP 2.5σ (|σ|>=2.5 + rápido/giro, salida trailing) ===")
        for sym, side, sp, dist, fast, giro in rows:
            print("  %-10s %-5s VWAP %+.1fσ  ->obj %+.2f%%  %s" % (
                sym, side, sp, dist, "RÁPIDO" if fast else "GIRO"))
    return fired


SWEEP_STATE = os.path.join(HERE, "sott_sweep_state.json")
SWEEP_COOLDOWN = 60 * 60      # 60 min por símbolo+lado (re-alerta si SUBE de grado B->A)


def _emit_sweep(sym, r, vwap_conf):
    if not _HUB_OK("sweep", r["side"], sym): return
    now = time.time(); sp = _spoken(sym); word = "long" if r["side"] == "LONG" else "short"
    both = r["both_swept"]; lado = "mínimo" if r["side"] == "LONG" else "máximo"
    gtxt = "grado A, doble barrido" if both else "grado B"
    try:
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "sweep-%s-%s-%d" % (sym, r["side"], int(now)), "asset": sym, "side": r["side"],
                       "text": "%s en %s, barrido del %s de londres y reclamo, %s, objetivo la mitad del rango, dejá correr" % (
                           word, sp, lado, gtxt),
                       "ts": int(now)})
        json.dump(alerts[-30:], open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    if TG is not None:
        try:
            emo = "\U0001f7e2 LONG" if r["side"] == "LONG" else "\U0001f534 SHORT"
            star = "⭐ A DOBLE-GRAB" if both else "B"
            conf = "  +\U0001f3af EXTREMO VWAP" if vwap_conf else ""
            det = "doble grab (ambos extremos) = el mejor setup" if both else "un solo extremo, confirmado por extremo VWAP"
            TG._send("\U0001f52a <b>%s %s — SWEEP-FADE NY←Londres [%s]%s</b>\n"
                     "\U0001f4cd NY barrió el %s del rango de Londres y <b>reclamó</b>\n"
                     "\U0001f3af <b>OBJETIVO</b>: mid %+.2f%% / opuesto %+.2f%% — <b>salida TRAILING</b> (dejá correr)\n"
                     "\U0001f6d1 <b>stop</b> %s (riesgo %.2f%%)\n"
                     "<i>%s — A=82%%/17%% OOS. MANUAL, no gatea estrategias</i>"
                     % (sp, emo, star, conf, lado, r["dist_mid"], r["dist_opp"], _fmt(r["stop"]), r["risk"], det),
                     force=True, zalert=True)
        except Exception:
            pass


def scan_sweep_fade(dry=False, verbose=False):
    """SWEEP-FADE NY<-Londres (VALIDADO bt_sweep_transition.py): grado A (doble barrido) SIEMPRE avisa; grado B SÓLO si
    lo confirma el extremo VWAP (toward-center). Objetivo = mid/opuesto del rango de Londres, salida TRAILING."""
    st = IMM._load(SWEEP_STATE, {}); now = time.time(); fired = 0; rows = []
    try:
        import session_sweep as _SS
    except Exception:
        return 0
    # UNIVERSO AMPLIO (pedido usuario 2026-08-28): roster = TAO, TQQQ, cripto mayor, ACCIONES y COMMODITIES (los movers
    # de NY). Validado en cripto; en stock/commodity la mecánica (barrido del rango pre-market/sesión al abrir NY +
    # reclaim) es plausible pero SIN validar OOS, y la data fuera de hora puede faltar (no dispara = degrada bien).
    try:
        universe = list(dict.fromkeys(SZ.roster()))
    except Exception:
        universe = list(dict.fromkeys(list(MOM_UNIVERSE) + list(BASKET)))
    for sym in universe:
        try:
            r = _SS.sweep_fade_now(sym)
            if not r:
                continue
            vwap_conf, _ = _toward_center_ok(sym, r["side"])   # ¿además en extremo VWAP hacia el centro?
            rows.append((sym, r["side"], r["grade"], r["dist_mid"], r["risk"], r["both_swept"], vwap_conf))
            if dry:
                continue
            if r["grade"] == "B" and not vwap_conf:
                continue                                       # grado B solo si el extremo VWAP lo confirma
            s = st.get(sym, {})
            if s.get("side") == r["side"] and s.get("grade") == r["grade"] and (now - s.get("ts", 0)) < SWEEP_COOLDOWN:
                continue
            _emit_sweep(sym, r, vwap_conf)
            st[sym] = {"side": r["side"], "grade": r["grade"], "ts": now}; fired += 1
        except Exception:
            continue
    if not dry:
        try:
            json.dump(st, open(SWEEP_STATE, "w"))
        except Exception:
            pass
    if verbose or dry:
        print("=== SWEEP-FADE NY<-Londres (A=doble grab siempre / B=confirmado por extremo VWAP) ===")
        for sym, side, g, dm, rk, both, vc in rows:
            print("  %-10s [%s] %-5s mid %+.2f%% riesgo %.2f%%%s%s" % (
                sym, g, side, dm, rk, "  ⭐DOBLE-GRAB" if both else "", "  +VWAP-EXT" if vc else ""))
    return fired


# ════════════════════════════════════════════════════════════════════════════════════════════════════════
#  ALERTA UNIFICADA (pedido usuario 2026-08-28): UNA sola alerta con TODO el conocimiento validado de los
#  indicadores que venimos trabajando. Integra por convicción:
#    • VWAP EXTREMO 2.5σ + delta del nivel a favor (reversión al centro) — base validada.
#    • CONFLUENCIA DE FLUJO (flow2/CVD 5m + 1h) = conv X/5 (del Pine cvd_divergence_flujo_ok_SYNC).
#    • SWEEP-FADE NY←Londres grado A (doble-grab: mitad 82% / stop 17% OOS) = el peso más fuerte.
#    • SYNC (régimen vivo, ATR% follow-through).
#  OBJETIVO = la MITAD del recorrido hacia el centro (POC/VWAP), que se cumple ~80% (bt_target_probs.py);
#  el extremo completo solo como runner. Salida TRAILING. Solo externo→centro (noise gate). 1 alerta/símbolo+lado.
# ════════════════════════════════════════════════════════════════════════════════════════════════════════
UNI_STATE = os.path.join(HERE, "sott_unified_state.json")
UNI_COOLDOWN = 60 * 60       # 60 min por símbolo+lado (re-alerta si SUBE de convicción)
UNI_MINCONV = 2.0            # piso de convicción (de ~6 máx)
# ★ FILTRO MÁXIMA CONFIANZA (usuario 2026-09-03, minado de label_outcomes.db LIMPIAS vs BASURA): solo avisar el perfil
# GANADOR-LIMPIO (tocó el target <3h SIN volver a la entrada). Discriminadores: target_reach>=80 (84% en limpias vs 55% en
# basura) + ADX>=25 (tendencia, 27 vs 21). La basura se iba 1.85% en contra y no tocaba target. Env HIGH_CONF_ALERT=0 = off.
_HIGH_CONF_ALERT = os.getenv("HIGH_CONF_ALERT", "1") == "1"
HC_TGT_REACH = float(os.getenv("HC_TGT_REACH", "80"))
HC_ADX = float(os.getenv("HC_ADX", "25"))
HC_FADE_ADX_MAX = float(os.getenv("HC_FADE_ADX_MAX", "30"))   # ★ /loop 2026-09-03: no alertar FADE con ADX>=30 (el peor leak)

# ★★ UNIFICAR ALERTA = PANEL (usuario 2026-09-03: "el panel del dashboard debe ser EXACTAMENTE lo mismo que la alerta; si me
# alerta short, el activo debe bajar al target"). La alerta SÓLO suena si el MOTOR DEL PANEL (que usa TODA la info: SOTT
# confluencia, contexto BTC/dominancia, coherencia de dirección, liquidez, target_reach, secuencia ③) dice OPERAR/ACOMPAÑAR
# hacia el target para ESE símbolo+lado. Si el panel dice ESPERAR/EVITAR = el activo se quedaría quieto → NO suena. Antes la
# alerta tenía su lógica separada → sonaba en el SETUP (extremo) pero sin el GATILLO del panel → el activo no se movía.
_ALERT_PANEL_GATE = os.getenv("ALERT_PANEL_GATE", "1") == "1"
_ALERT_PANEL_MIN_CONF = float(os.getenv("ALERT_PANEL_MIN_CONF", "55"))
_PANEL_CACHE = {"rows": None, "ts": 0.0}


def _panel_verdict(sym):
    """Fila del panel EXTREMOS (/api/vp_extremes) = el veredicto del motor unificado. Cache 30s. FAIL-OPEN (None si no responde)."""
    import time as _t, json as _j, urllib.request as _u
    now = _t.time()
    if _PANEL_CACHE["rows"] is None or now - _PANEL_CACHE["ts"] > 30:
        try:
            d = _j.load(_u.urlopen("http://127.0.0.1:8890/api/vp_extremes", timeout=8))
            _PANEL_CACHE["rows"] = {r.get("symbol"): r for r in (d.get("rows") or [])}
            _PANEL_CACHE["ts"] = now
        except Exception:
            pass
    return (_PANEL_CACHE["rows"] or {}).get(sym)
CONT_STATE = os.path.join(HERE, "sott_cont_state.json")   # latch de la alerta de MEJOR CONTINUACIÓN
CONT_COOLDOWN = 45 * 60      # 45 min; re-alerta si CAMBIA el activo/lado o pasa el cooldown


def _emit_continuation(sym, side, score, r):
    """Alerta de MEJOR CONTINUACIÓN (pedido usuario 2026-09-02): la #1 para operar A FAVOR de la tendencia
    (best_cont del panel EXTREMOS) hasta que el CVD de fondo (Hist) gire. Voz + Telegram. Emoji surfista."""
    if not _HUB_OK("cont", side, sym): return
    now = time.time(); sp = _spoken(sym); word = "long" if side == "LONG" else "short"
    dir_txt = "sigue subiendo" if side == "LONG" else "sigue cayendo"
    try:
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "cont-%s-%s-%d" % (sym, side, int(now)), "asset": sym, "side": side,
                       "text": "operá a favor de la tendencia, %s en %s, mejor continuación, %d de 6, %s con el flujo de fondo alineado, salí cuando el flujo se dé vuelta" % (
                           word, sp, score, dir_txt),
                       "ts": int(now)})
        json.dump(alerts[-30:], open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    if TG is not None:
        try:
            emo = "\U0001f7e2 LONG" if side == "LONG" else "\U0001f534 SHORT"
            TG._send("\U0001f3c4 <b>%s %s — MEJOR CONTINUACIÓN · operá A FAVOR de la tendencia (%d/6)</b>\n"
                     "\U0001f4c8 Flujo de fondo ALINEADO: Hist (CVD acumulado) + CVD 5m + flow2 + CVD 1h + SYNC vivo\n"
                     "\U0001f3af Surfeá la tendencia · <b>SALÍ/revertí cuando el CVD de fondo (Hist) gire</b> contra el movimiento\n"
                     "<i>MANUAL, a favor del movimiento (NO es fade). El panel la marca 🏆.</i>"
                     % (sp, emo, score),
                     force=True, zalert=True)
        except Exception:
            pass


def _emit_cont_exit(sym, side, reversed_, r):
    """CERRAR la continuación (pedido usuario 2026-09-02 "cuándo cerrar el long y entrar la reversión"): el flujo de
    fondo (Hist) dejó de acompañar. reversed_=True -> ya giró (cerrar + entrar la reversión); False -> se está agotando
    (ajustar/cerrar antes de devolver). Voz + Telegram."""
    if not _HUB_OK("contexit", side, sym): return
    now = time.time(); sp = _spoken(sym); word = "long" if side == "LONG" else "short"
    opp = "short" if side == "LONG" else "long"
    if reversed_:
        vtext = "cerrá el %s en %s, el flujo de fondo se dio vuelta, ahora entrá la reversión %s" % (word, sp, opp)
        tmsg = "\U0001f504 <b>%s — CERRÁ %s + ENTRÁ REVERSIÓN %s</b>\nEl CVD de fondo (Hist) GIRÓ contra la tendencia = fin de la continuación. Cerrá el %s y entrá la reversión %s (fade al POC/VWAP)." % (sp, word.upper(), opp.upper(), word, opp)
    else:
        vtext = "ajustá o cerrá el %s en %s, la continuación se está debilitando, el flujo de fondo dejó de acompañar" % (word, sp)
        tmsg = "⚠️ <b>%s — AJUSTÁ/CERRÁ %s (continuación debilitándose)</b>\nTomó liquidez y el CVD de fondo (Hist) dejó de subir = la tendencia se agota. Ajustá stop / tomá ganancia. Si el Hist cruza, sale la reversión %s." % (sp, word.upper(), opp)
    try:
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "contexit-%s-%d" % (sym, int(now)), "asset": sym, "side": side, "text": vtext, "ts": int(now)})
        json.dump(alerts[-30:], open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    if TG is not None:
        try:
            TG._send(tmsg, force=True, zalert=True)
        except Exception:
            pass


def _emit_unified(sym, side, conv, why, r):
    if not _HUB_OK("uni", side, sym): return
    now = time.time(); sp = _spoken(sym); word = "long" if side == "LONG" else "short"
    zona = "arriba, estirado en v-wap" if side == "SHORT" else "abajo, estirado en v-wap"
    grado = "MÁXIMA" if conv >= 4.5 else ("ALTA" if conv >= 3.5 else "MEDIA")
    tgt_kind = r.get("target_kind", "VWAP"); dist = r.get("dist_target", 0.0)
    try:
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "uni-%s-%s-%d" % (sym, side, int(now)), "asset": sym, "side": side,
                       "text": "%s en %s, %s, convicción %s, %s, objetivo la mitad del recorrido al %s, dejá correr" % (
                           word, sp, zona, grado.lower(), ", ".join(why[:3]).lower(), tgt_kind.lower()),
                       "ts": int(now)})
        json.dump(alerts[-30:], open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    if TG is not None:
        try:
            emo = "\U0001f7e2 LONG" if side == "LONG" else "\U0001f534 SHORT"
            mid_pct = round(dist / 2.0, 2)          # ~mitad del recorrido hacia el centro
            sfx = ""
            if r.get("sweep_fade") == "A":
                sfx = "\n⚔️ <b>SWEEP-FADE grado A</b> (doble-grab · mitad 82% / stop 17% OOS)"
            TG._send("\U0001f3af <b>%s %s — ALERTA UNIFICADA · convicción %s (%.1f)</b>\n"
                     "\U0001f4cd <b>LUGAR</b>: %s · VWAP <b>%+.1fσ</b> · %s\n"
                     "\U0001f30a <b>FLUJO</b>: %s\n"
                     "\U0001f3af <b>OBJETIVO</b>: %s (%+.2f%%) — <b>TP1 ~mitad %+.2f%% (se cumple ~80%%)</b>, extremo=runner · salida TRAILING%s\n"
                     "<i>MANUAL, SL más allá del extremo (~1.5%%). No gatea estrategias.</i>"
                     % (sp, emo, grado, conv, r.get("tier", "-"), r.get("sig_pos", 0.0), zona,
                        " · ".join(why), tgt_kind, dist, mid_pct, sfx),
                     force=True, zalert=True)
        except Exception:
            pass


def scan_unified(dry=False, verbose=False):
    """UNA alerta que combina todos los edges validados por convicción. Objetivo = mitad del recorrido (~80%)."""
    st = IMM._load(UNI_STATE, {}); now = time.time(); fired = 0; rows = []
    try:
        import vwap_profile as _VP
    except Exception:
        return 0
    universe = list(dict.fromkeys(list(MOM_UNIVERSE) + list(BASKET)))
    try:
        ext = _VP.vp_extremes(universe)          # solo los que están en extremo (fuera de VA / ≥2.5σ)
    except Exception:
        return 0
    for r in ext:
        sym = r["symbol"]; side = r.get("signal")
        if side is None:                         # extremo sin delta que confirme la reversión → no operable
            continue
        conv = 1.0; why = ["VWAP %.1fσ+delta" % abs(r["sig_pos"])]
        if r.get("prime"):
            conv += 1.0; why.append("PRIME≥2σ")
        if r.get("conv", 0) >= 3:
            conv += 1.0; why.append("conv %d/5" % r["conv"])
        elif r.get("conv", 0) >= 2:
            conv += 0.5; why.append("conv %d/5" % r["conv"])
        if r.get("sweep_fade") == "A":
            conv += 2.0; why.append("⚔ SWEEP-A")
        elif r.get("sweep_fade") == "B":
            conv += 0.5; why.append("sweep-B")
        if r.get("sync"):
            conv += 0.5; why.append("SYNC")
        # HORA/DIA de mayor edge (backtest confianza horario 2026-09-01: NY 13-21 / London 6-10 UTC / Jue-Vie mejor;
        # finde + horas muertas 0-5/11-12/16-18/23 peor). Dispara mas en el MEJOR momento y frena en el peor.
        _u = time.gmtime(now); _hh = _u.tm_hour; _wd = _u.tm_wday
        _goodH = (6 <= _hh <= 10) or (13 <= _hh <= 15) or (20 <= _hh <= 22)
        _badH = (_hh <= 5) or _hh in (11, 12, 16, 17, 18, 23)
        if _goodH and _wd < 5:
            conv += 0.5; why.append("hora ALTA")
        elif _badH or _wd >= 5:
            conv -= 0.5; why.append("hora BAJA")
        # PRIORIDAD SOTT MULTI-TF (pedido usuario 2026-09-02): confluencia de extremos SOTT anclada en 5m
        # (5m+15m / 5m+1h / 5m+1d) DESTACA el activo como operable; 3+ TFs = MAXIMA. Solo si el lado SOTT coincide
        # con el fade. Es el driver de mayor prioridad de la alerta (va primero en el "por qué").
        _sott_conf = False
        try:
            from direction_engine import sott_zone as _SZc
            _mc = _SZc.sott_mtf_confluence(sym)
            if _mc and _mc["side"] == side:
                _sott_conf = True
                conv += 2.0 if _mc["n"] >= 3 else 1.0
                why.insert(0, "SOTT x%d %s (%s)" % (_mc["n"], _mc["grade"], "+".join(_mc["tfs"])))
        except Exception:
            _sott_conf = False
        rows.append((sym, side, round(conv, 1), r.get("tier"), r.get("conv"), r.get("sweep_fade")))
        if dry:
            continue
        # DISPARA en las "mejores operaciones" del panel EXTREMOS: PRIME (≥2σ) O flujo fuerte (conv≥3) O sweep-fade.
        # (filtra los extremos "leve" con flujo flojo = ruido). Convicción sube el GRADO del mensaje.
        operable = bool(r.get("prime") or r.get("conv", 0) >= 3 or r.get("sweep_fade") or _sott_conf)
        if not operable or conv < UNI_MINCONV:
            continue
        # ★ MÁXIMA CONFIANZA: sólo el perfil ganador-limpio (target_reach>=80 + ADX>=25). Limpia las alertas basura.
        if _HIGH_CONF_ALERT and not ((r.get("target_reach") or 0) >= HC_TGT_REACH and (r.get("adx") or 0) >= HC_ADX):
            continue
        # ★ NO FADEAR TENDENCIA FUERTE (calibración /loop 2026-09-03, label_outcomes): FADE con ADX>=30 = WR19% E−2.44 en el
        # set de alertas; bloquearlo sube el E del stream de +0.06 a +0.84. El ADX>=25 del gate high-conf es correcto para
        # TENDENCIA pero AL REVÉS para FADE (fadear una tendencia fuerte no revierte). Modo-aware.
        if r.get("modo_reco") == "FADE" and (r.get("adx") or 0) >= HC_FADE_ADX_MAX:
            continue
        # ★ CONTEXTO DE MERCADO (matriz dominancia BTC / índice tech, usuario 2026-09-03): una alerta de MÁXIMA confianza no
        # debe PELEAR la rotación macro (LONG en una alt mientras la dominancia sube = las alts sangran). Contra un contexto
        # CLARO → no avisar (no es alta probabilidad). A favor → +convicción. FAIL-OPEN (si el módulo falla, no bloquea).
        try:
            import market_regime as _mkr
            _mb, _mnote = _mkr.bias_for(sym)
            if _mb and _mb != side:
                continue
            if _mb == side:
                conv += 0.5; why.append("contexto macro a favor (%s)" % _mnote)
        except Exception:
            pass
        # ★★ UNIFICAR CON EL PANEL (usuario 2026-09-03): la alerta = el veredicto del panel. Solo suena si el motor del panel
        # dice OPERAR/ACOMPAÑAR ESTE lado hacia el target (usa TODA la info). Si dice ESPERAR/EVITAR → el activo se queda
        # quieto → NO suena. FAIL-OPEN (si el panel no responde, la alerta sigue con sus filtros propios).
        if _ALERT_PANEL_GATE:
            _pr = _panel_verdict(sym)
            if _pr is not None:
                _pv = _pr.get("verdict") or {}
                if _pv.get("action") not in ("OPERAR", "ACOMPAÑAR") or _pv.get("side") != side or (_pv.get("conf") or 0) < _ALERT_PANEL_MIN_CONF:
                    continue
                # usar el TARGET del panel (confluencia de niveles) para que la alerta y el panel apunten al MISMO objetivo
                if _pr.get("target_reach") is not None:
                    r["target_reach"] = _pr.get("target_reach")
                if _pr.get("target"):
                    r["target"] = _pr.get("target"); r["target_kind"] = _pr.get("target_kind", r.get("target_kind"))
                    r["dist_target"] = _pr.get("dist_target", r.get("dist_target"))
        ok, _ = _VP.alert_toward_center(sym, side)
        if not ok:
            continue
        s = st.get(sym, {})
        if s.get("side") == side and (now - s.get("ts", 0)) < UNI_COOLDOWN and s.get("conv", 0) >= conv:
            continue
        _emit_unified(sym, side, conv, why, r)
        # ★ PUENTE ALERTA→TRADE (usuario 2026-09-03): la alerta ahora ABRE un trade paper trackeado (arm='uni_alert') + real
        # si el flag está armado. Antes sólo notificaba ("no gatea estrategias"). Entra AL INICIO (precio estirado), no persiguiendo.
        try:
            import uni_alert_bridge as _uab
            _uab.on_alert(sym, side, r, conv)
        except Exception:
            pass
        st[sym] = {"side": side, "ts": now, "conv": conv}; fired += 1
    if not dry:
        try:
            json.dump(st, open(UNI_STATE, "w"))
        except Exception:
            pass
    # ALERTA MEJOR CONTINUACIÓN (pedido usuario 2026-09-02 "que la mejor para operar a favor avise por voz+telegram"):
    # el best_cont del panel = la #1 para surfear la tendencia. Latch por activo+lado, cooldown propio (no spamea).
    if not dry:
        try:
            _cst = IMM._load(CONT_STATE, {})
            _prev = _cst.get("last", {})
            # (a) SALIDA: si el activo avisado como mejor continuación se DIO VUELTA (signal) o se DEBILITÓ (cont_weak) -> avisar CERRAR
            if _prev.get("sym") and not _prev.get("exited"):
                _prow = next((r for r in ext if r["symbol"] == _prev["sym"]), None)
                if _prow and (_prow.get("signal") or _prow.get("cont_weak")):
                    _emit_cont_exit(_prev["sym"], _prev.get("side"), bool(_prow.get("signal")), _prow)
                    _prev["exited"] = True; _cst["last"] = _prev
                    json.dump(_cst, open(CONT_STATE, "w")); fired += 1
            # (b) ENTRADA: la MEJOR continuación actual (nueva o pasó el cooldown)
            _best = next((r for r in ext if r.get("best_cont")), None)
            if _best and _best.get("trend"):
                _bsym = _best["symbol"]; _bside = _best["trend"]; _bsc = _best.get("cont_score", 0)
                if (_prev.get("sym") != _bsym) or (_prev.get("side") != _bside) or (now - _prev.get("ts", 0)) >= CONT_COOLDOWN:
                    _emit_continuation(_bsym, _bside, _bsc, _best)
                    _cst["last"] = {"sym": _bsym, "side": _bside, "ts": now, "exited": False}
                    json.dump(_cst, open(CONT_STATE, "w")); fired += 1
        except Exception:
            pass
    if verbose or dry:
        print("=== ALERTA UNIFICADA (convicción ≥ %.1f = avisa · integra VWAP-ext + flujo + sweep-fade + SYNC) ===" % UNI_MINCONV)
        for sym, side, cv, tier, cn, sf in sorted(rows, key=lambda x: -x[2]):
            print("  %-10s %-5s conv %.1f  %-8s conv-flujo %s/5%s" % (
                sym, side, cv, tier, cn, "  ⚔SWEEP-%s" % sf if sf else ""))
    return fired


# --------------------------------------------------------------------------------------------------
# CIRCULO AMARILLO A+ (pedido usuario 2026-08-31 "avisame por voz+telegram+TV cada vez que sale el circulo
# amarillo, en la mayor cantidad de activos, priorizando TQQQ y TAO + movers/marketcap"). Replica EXACTA de la
# senal AMARILLA del indicador Pine "Z-Score Multi": z ENTRA al extremo (crossunder -thr) + confirmacion de
# ESTRUCTURA (sweep de liquidez trapDn O climax de volumen O MFI oversold). SOLO LONG (el amarillo del Pine es
# alta-conviccion LONG). Universo = TQQQ/TAO primero + roster operable (acciones/commodities/top20 cripto).
YC_TF     = os.getenv("YELLOW_TF", "1h")   # TF del scan (el edge del z-extremo esta validado en 1h; cambiar con YELLOW_TF=5m)
YC_LB     = 75      # lookback del z (= Pine)
YC_THR    = 2.5     # umbral del extremo (= Pine)
YC_CLIMAX = 2.5     # climax de volumen x avg20 (= Pine)
YC_MFI    = 25.0    # MFI oversold (= Pine)
YC_SWEEPL = 12      # ventana del sweep de liquidez (= Pine)
YC_WICK   = 0.45    # mecha de rechazo minima (= Pine)
YC_STATE  = os.path.join(HERE, "yellow_circle_state.json")
YC_FAST_TFS   = ["15m", "5m"]                          # REQUERIDAS (movimientos rapidos, pedido usuario 2026-08-31): ambas deben tener amarillo. 15m primero = gate mas barato.
YC_SLOW_TF    = "1h"                                   # OPCIONAL: si TAMBIEN alinea -> sube el grado a MAXIMA (no bloquea la alerta rapida)
YC_WINDOW_MIN = int(os.getenv("YELLOW_WINDOW_MIN", "45"))  # ventana (min) de la confluencia 5m+15m (mas corta = mas simultaneo/rapido)
_TF_MIN       = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
YC_MOVERS_N   = int(os.getenv("YELLOW_MOVERS_N", "20"))              # cuantos TOP MOVERS dinamicos sumar (pedido usuario: "los que mas % se mueven por dia")
YC_MOVERS_RNG = float(os.getenv("YELLOW_MOVERS_MINRANGE", "3.0"))    # rango 24h minimo % para contar como "mover" (con liquidez alta)


def _mfi(K, n=14):
    """Money Flow Index de la ultima barra (typical price hlc3 x volumen)."""
    if len(K) < n + 1:
        return 50.0
    pos = neg = 0.0
    for j in range(len(K) - n, len(K)):
        tp = (K[j][2] + K[j][3] + K[j][4]) / 3.0
        tp0 = (K[j - 1][2] + K[j - 1][3] + K[j - 1][4]) / 3.0
        mf = tp * K[j][5]
        if tp > tp0:
            pos += mf
        elif tp < tp0:
            neg += mf
    if neg <= 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + pos / neg)


def _yellow_circle_long(K):
    """True si en la ULTIMA vela CERRADA aparece el CIRCULO AMARILLO A+ LONG (= indicador Pine).
    Devuelve (aplus, extra). K debe venir SIN la vela en formacion (evita repintado)."""
    n = len(K)
    if n < YC_LB + 25:
        return False, {}
    i = n - 1
    c = [b[4] for b in K]; o = [b[1] for b in K]; h = [b[2] for b in K]; l = [b[3] for b in K]; v = [b[5] for b in K]
    z_i = IMM._z(c, YC_LB, i)
    z_prev = IMM._z(c, YC_LB, i - 1)
    # RECUPERACION (validado backtest 2026-09-01: E+0.45 WR78% > extremo; evita el cuchillo): z VUELVE cruzando -thr hacia ARRIBA.
    ext_long = (z_prev <= -YC_THR) and (z_i > -YC_THR)          # crossover -thr (sale del piso)
    if not ext_long:
        return False, {"z": z_i}
    # estructura en las ULTIMAS 2 velas (el piso/oversold pudo ser 1 vela ANTES de la recuperacion)
    vol_clim = mfi_os = trap_dn = False; volx = 0.0; mfi = 50.0
    for k in (0, 1):
        j = i - k
        if j < 20:
            continue
        av = sum(v[j - 20:j]) / 20.0
        vx = v[j] / av if av > 0 else 0.0
        volx = max(volx, vx)
        if vx >= YC_CLIMAX:
            vol_clim = True
        mj = _mfi(K[:j + 1], 14); mfi = min(mfi, mj)
        if mj < YC_MFI:
            mfi_os = True
        swl = min(l[j - YC_SWEEPL:j]) if j >= YC_SWEEPL else min(l[:j] or [l[j]])
        rng = max(h[j] - l[j], 1e-9); dw = (min(o[j], c[j]) - l[j]) / rng
        if l[j] < swl and c[j] > swl and dw >= YC_WICK:
            trap_dn = True
    struct = vol_clim or mfi_os or trap_dn
    if not struct:
        return False, {"z": z_i}
    razones = []
    if trap_dn:
        razones.append("sweep de liquidez")
    if vol_clim:
        razones.append("climax de volumen")
    if mfi_os:
        razones.append("MFI oversold")
    return True, {"z": z_i, "volx": volx, "mfi": mfi, "px": c[i], "razones": razones}


def _emit_yellow_conf(sym, per_tf, has_slow):
    """Aviso del CIRCULO AMARILLO por confluencia RAPIDA (5m+15m). has_slow=True si el 1h tambien alineo (grado MAXIMA)."""
    if not _HUB_OK("yellowx", "LONG", sym): return
    now = time.time(); sp = _spoken(sym)
    fast = "5m + 15m"
    tfs_all = ["5m", "15m"] + ([YC_SLOW_TF] if has_slow else [])
    grade = "x3 MAXIMA (+1h)" if has_slow else "x2 rapida (5m+15m)"
    px = next((per_tf[t]["px"] for t in tfs_all if per_tf.get(t, {}).get("px")), 0.0)
    razs = sorted({r for t in tfs_all for r in per_tf.get(t, {}).get("razones", [])})
    razones = " + ".join(razs) or "estructura"
    # VOZ (arranca con 'long en' para pasar el whitelist del dashboard)
    try:
        vgrade = "en 5, 15 y 60 minutos" if has_slow else "en 5 y 15 minutos"
        alerts = IMM._load(VOICE, [])
        alerts.append({"id": "yellowx-%s-%d" % (sym, int(now)), "asset": sym, "side": "LONG",
                       "text": "long en %s, circulo amarillo %s alineados, alta conviccion" % (sp, vgrade),
                       "ts": int(now)})
        alerts = alerts[-30:]
        json.dump(alerts, open(VOICE, "w"), ensure_ascii=False)
    except Exception:
        pass
    # TELEGRAM
    if TG is not None:
        try:
            zline = " · ".join("%s z%+.2f" % (t, per_tf.get(t, {}).get("z", 0.0)) for t in tfs_all)
            dot = "\U0001f7e1\U0001f7e1\U0001f7e1" if has_slow else "\U0001f7e1\U0001f7e1"
            extra1h = " + 1h confirma" if has_slow else ""
            TG._send("%s <b>%s — CIRCULO AMARILLO %s (A+ LONG)</b>\n"
                     "\U0001f4cd rapidas ALINEADAS (%s)%s en ventana %d min\n"
                     "\U0001f4ca %s\n"
                     "\U0001f30a estructura: %s\n"
                     "precio $%s"
                     % (dot, sp, grade, fast, extra1h, YC_WINDOW_MIN, zline, razones, _fmt(px)),
                     force=True, zalert=True)
        except Exception:
            pass


def _yc_roster():
    """Universo del circulo amarillo (pedido usuario): TQQQ/TAO PRIMERO + MAYOR MARKETCAP (top20 cripto + acciones/
    commodities) + los que MAS % SE MUEVEN HOY (bybit_movers dinamico, rango 24h >= YC_MOVERS_RNG con liquidez alta)."""
    base = ["TQQQUSDT", "TAOUSDT"]
    # 1) MARKETCAP fijo (top20 cripto + acciones/commodities grandes)
    try:
        for s in SZ.roster():
            if s not in base:
                base.append(s)
    except Exception:
        for s in IMM.UNIVERSE:
            if s not in base:
                base.append(s)
    # 2) TOP MOVERS dinamicos = los que MAS se mueven por dia (rango 24h alto) + liquidez/mcap alta, ordenados por volumen
    try:
        for s in (SZ.bybit_movers(min_range=YC_MOVERS_RNG, min_turn=1e7) or [])[:YC_MOVERS_N]:
            if s not in base:
                base.append(s)
    except Exception:
        pass
    return base


def _yellow_recent(sym, tf, window_min):
    """(True, extra) si el CIRCULO AMARILLO A+ aparecio en alguna vela CERRADA dentro de window_min en ese TF."""
    K = IMM._klines(sym, tf, 400)
    if not K or len(K) < YC_LB + 26:
        return False, {}
    tf_min = _TF_MIN.get(tf, 60)
    bars = max(1, int(round(window_min / tf_min)))
    Kc = K[:-1]                                            # velas cerradas (descarta la vela en formacion)
    n = len(Kc)
    hit = None
    for j in range(max(YC_LB + 1, n - bars), n):
        ap, extra = _yellow_circle_long(Kc[:j + 1])
        if ap:
            hit = extra                                    # se queda con el mas reciente dentro de la ventana
    return (hit is not None), (hit or {})


def scan_yellow_circle(dry=False, verbose=False):
    """Avisa cuando el CIRCULO AMARILLO A+ coincide en las RAPIDAS 5m+15m (REQUERIDAS) dentro de YC_WINDOW_MIN.
    El 1h es OPCIONAL: si tambien alinea sube el grado a x3 MAXIMA, pero NO bloquea (prioriza movimientos rapidos)."""
    st = IMM._load(YC_STATE, {})
    now = time.time()
    rows = []
    fired = 0
    for sym in _yc_roster():
        try:
            ok15, ex15 = _yellow_recent(sym, "15m", YC_WINDOW_MIN)      # gate por 15m (la rapida mas rara) -> ahorra el fetch de 5m
            if not ok15:
                continue
            ok5, ex5 = _yellow_recent(sym, "5m", YC_WINDOW_MIN)
            if not ok5:
                continue
            per_tf = {"15m": ex15, "5m": ex5}
            has_slow, ex1h = _yellow_recent(sym, YC_SLOW_TF, YC_WINDOW_MIN)   # 1h OPCIONAL (solo sube el grado)
            if has_slow:
                per_tf[YC_SLOW_TF] = ex1h
            rows.append((sym, has_slow, ex15.get("z", 0.0)))
            if dry:
                continue
            s = st.get(sym, {})
            if (now - s.get("ts", 0)) < COOLDOWN:          # no repetir la misma confluencia dentro de 90 min
                continue
            _emit_yellow_conf(sym, per_tf, has_slow)
            st[sym] = {"ts": now, "slow": has_slow}
            fired += 1
        except Exception:
            continue
    if not dry:
        try:
            json.dump(st, open(YC_STATE, "w"))
        except Exception:
            pass
    if verbose or dry:
        print("=== CIRCULO AMARILLO: RAPIDAS 5m+15m (req) + 1h opcional, ventana %d min ===" % YC_WINDOW_MIN)
        for sym, slow, z in sorted(rows, key=lambda x: (not x[1], x[2])):
            print("  %-12s %s  z15m=%+.2f" % (sym, "x3 MAXIMA (+1h)" if slow else "x2 rapida (5m+15m)", z))
    return fired


if __name__ == "__main__":
    import sys as _s
    dry = ("--dry" in _s.argv)
    if "--yellow" in _s.argv:
        y = scan_yellow_circle(dry=dry, verbose=True)
        print("\nCIRCULO AMARILLO disparadas: %d" % y)
        _s.exit(0)
    u = scan_unified(dry=dry, verbose=True)
    print("\nALERTA UNIFICADA disparadas: %d | telegram=%s" % (u, "ON" if (TG and TG.ENABLED) else "OFF"))
    if "--all" in _s.argv:      # ver las separadas viejas (ya no se usan en el host)
        for f, lbl in [(scan, "CASI SEGURO"), (scan_mtf, "SOTT MTF"), (scan_momentum, "MOMENTUM"),
                       (scan_sott_confluence, "CONFLUENCIA"), (scan_ignition, "IGNICIÓN"),
                       (scan_revpoc, "REVPOC"), (scan_sweep_fade, "SWEEP-FADE")]:
            print("\n[%s] disparadas: %d" % (lbl, f(dry=dry, verbose=True)))
