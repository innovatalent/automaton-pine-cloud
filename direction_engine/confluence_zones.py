"""Deterministic, read-only zones and causal closed-candle context.

None of these descriptions is an order signal or a calibrated forecast.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from statistics import median


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def independent_family(name, supplied=None):
    name = str(name).lower()
    if any(x in name for x in ('gamma', 'gex', 'vanna', 'charm', 'delta_wall', 'delta_zero', 'call_wall', 'put_wall', 'max_abs', 'max_pain', 'vol_trigger', 'muro_')):
        return 'options'
    if any(x in name for x in ('poc', 'vah', 'val')):
        return 'volume_profile'
    if name.startswith(('ob_', 'order_block')):
        return 'order_blocks'
    if supplied:
        return str(supplied)
    for session in ('asia', 'london', 'ny', 'previous_day', 'session'):
        if session in name:
            return 'range_' + session
    return 'other_levels'


def unknown_probability(reason='Exact-zone forward calibration unavailable.'):
    return {'status': 'INSUFFICIENT', 'p_touch_3h': None, 'n': 0,
            'interval': None, 'reason': reason, 'validated': False}


def cluster_levels(members, spot, threshold_pct=0.3, now=None):
    """Sorted complete-link grouping; strict span < threshold, never chaining.

    Input members: name/price/family/source/ts/ttl_sec/eligible/approximate.
    All valid-price levels remain visible. Unverifiable members cannot activate a zone.
    """
    threshold = _num(threshold_pct)
    if threshold is None or threshold <= 0:
        raise ValueError('threshold_pct must be positive')
    spot = _num(spot)
    rows, seen = [], set()
    for raw in members:
        price = _num(raw.get('price'))
        if price is None or price <= 0:
            continue
        row = dict(raw, price=price, name=str(raw.get('name', 'level')))
        row['family'] = independent_family(row['name'], raw.get('family'))
        ts, ttl = _num(raw.get('ts')), _num(raw.get('ttl_sec', 300))
        age = now - ts if now is not None and ts is not None else None
        row['age_sec'] = age
        row['freshness'] = ('UNVERIFIED' if ts is None or ts <= 0 else
                            'FUTURE' if now is not None and ts > now else
                            'STALE' if age is not None and (ttl is None or age > ttl) else 'FRESH')
        row['eligible'] = bool(raw.get('eligible', True) and row['freshness'] == 'FRESH'
                               and not raw.get('leader_context'))
        key = (row['name'], price, row['family'], str(row.get('source')), ts)
        if key not in seen:
            rows.append(row)
            seen.add(key)
    rows.sort(key=lambda r: (r['price'], r['name'], str(r.get('source')), r.get('ts') or 0))
    groups = []
    for row in rows:
        # Decimal avoids accidentally admitting an exact 0.3% boundary through float roundoff.
        fits = bool(groups) and ((Decimal(str(row['price'])) - Decimal(str(groups[-1][0]['price']))) * 100
                                 < Decimal(str(threshold)) * Decimal(str(groups[-1][0]['price'])))
        if fits:
            groups[-1].append(row)
        else:
            groups.append([row])
    result = []
    for group in groups:
        def priority(row):
            name = row['name'].lower()
            return (0 if name == 'gamma_flip' else 1 if name == 'gamma_max' else
                    2 if name.startswith(('call_wall', 'put_wall', 'max_abs')) else 3,
                    row['price'], name)
        options = [r for r in group if r['family'] == 'options' and r['name'] != 'max_pain']
        anchor_member = min(options, key=priority) if options else None
        low, high = group[0]['price'], group[-1]['price']
        anchor = anchor_member['price'] if anchor_member else median(sorted({r['price'] for r in group}))
        families = sorted({r['family'] for r in group})
        names = sorted({r['name'] for r in group})
        identity = json.dumps([(r['name'], r['price'], r['family'], r.get('source')) for r in group], sort_keys=True)
        freshness = next((status for status in ('FUTURE', 'UNVERIFIED', 'STALE')
                          if any(r['freshness'] == status for r in group)), 'FRESH')
        result.append({'id': hashlib.sha256(identity.encode()).hexdigest()[:16],
                       'low': low, 'high': high, 'anchor': anchor,
                       'anchor_name': anchor_member['name'] if anchor_member else 'median',
                       'names': names, 'members': group, 'families': families,
                       'family_count': len(families), 'span_pct': (high-low)/low*100,
                       'confluence_status': 'MULTI_FAMILY' if len(families) >= 2 else 'SINGLE_FAMILY',
                       'distance_pct': (anchor/spot-1)*100 if spot and spot > 0 else None,
                       'edge_distance_pct': ((low/spot-1)*100 if spot < low else (high/spot-1)*100 if spot > high else 0.) if spot and spot > 0 else None,
                       'gamma_led': bool(anchor_member),
                       'eligible': all(r['eligible'] for r in group),
                       'freshness': freshness,
                       'age_sec': max((r['age_sec'] for r in group if r['age_sec'] is not None), default=None),
                       'approximate': any(r.get('approximate', False) for r in group),
                       'profile_approximate': any(r.get('profile_approximate', False) for r in group),
                       'profile_methods': sorted({r.get('source', 'unknown') for r in group if r.get('profile_approximate')}),
                       'known_at': max((r.get('ts') or 0 for r in group), default=0),
                       'probability': unknown_probability()})
    return result


def aggregate_candles(candles, cutoff_ts, source_sec=60, timeframe_sec=300):
    """Only aggregate complete, aligned grids closed by cutoff; do not fill gaps."""
    groups = {}
    for row in candles:
        ts = _num(row.get('ts'))
        if ts is None or ts + source_sec > cutoff_ts or ts % source_sec:
            continue
        bucket = int(ts // timeframe_sec) * timeframe_sec
        groups.setdefault(bucket, {})[ts] = row
    result = []
    for bucket, keyed in sorted(groups.items()):
        expected = list(range(bucket, bucket + timeframe_sec, source_sec))
        if bucket + timeframe_sec > cutoff_ts or sorted(keyed) != expected:
            continue
        rows = [keyed[t] for t in expected]
        result.append({'ts': bucket, 'open': rows[0]['open'], 'high': max(r['high'] for r in rows),
                       'low': min(r['low'] for r in rows), 'close': rows[-1]['close'],
                       'volume': sum(r['volume'] for r in rows)})
        if all(_num(r.get('taker_buy_volume')) is not None for r in rows):
            result[-1]['taker_buy_volume'] = sum(r['taker_buy_volume'] for r in rows)
    return result


def features_from_candles(candles, cutoff_ts, timeframe_sec=300, window=20):
    """20 closed 5m bars by default. HLC3 volume-bin approximation, not tick VP."""
    empty = {'status': 'INSUFFICIENT', 'window': window, 'timeframe_sec': timeframe_sec,
             'aggressor_delta': {'status': 'UNKNOWN', 'value': None}, 'vp20': {}}
    rows = {}
    for raw in candles:
        row = {k: _num(raw.get(k)) for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
        if any(v is None for v in row.values()):
            continue
        if row['ts'] + timeframe_sec > cutoff_ts:
            continue
        if min(row[k] for k in ('open', 'high', 'low', 'close')) <= 0 or row['volume'] < 0:
            continue
        if not row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']:
            continue
        row['taker_buy_volume'] = _num(raw.get('taker_buy_volume'))
        rows[row['ts']] = row
    bars = sorted(rows.values(), key=lambda r: r['ts'])[-window:]
    if len(bars) != window or any(bars[i]['ts']-bars[i-1]['ts'] != timeframe_sec for i in range(1,len(bars))):
        return empty
    last, prior = bars[-1], bars[:-1]
    closed_ts = last['ts'] + timeframe_sec
    total = sum(r['volume'] for r in bars)
    baseline = sum(r['volume'] for r in prior)/len(prior)
    trend_return = (last['close']/bars[0]['open']-1)*100
    delta_known = all(r['taker_buy_volume'] is not None and 0 <= r['taker_buy_volume'] <= r['volume'] for r in bars)
    low, high = min(r['low'] for r in bars), max(r['high'] for r in bars)
    vp = {}
    if total > 0:
        width = (high-low)/24
        bins = [0.] * 24
        for r in bars:
            price = (r['high']+r['low']+r['close'])/3
            index = min(23, max(0, int((price-low)/width))) if width else 0
            bins[index] += r['volume']
        peak = max(range(24), key=lambda i: (bins[i], -i))
        left = right = peak
        included = bins[peak]
        while included < total*.7 and (left > 0 or right < 23):
            if right < 23 and (left == 0 or bins[right+1] > bins[left-1]):
                right += 1
                included += bins[right]
            else:
                left -= 1
                included += bins[left]
        vp = {'poc': low+(peak+.5)*width, 'val': low+left*width, 'vah': low+(right+1)*width,
              'ts': closed_ts, 'method': 'HLC3_VOLUME_BINS_24_70PCT', 'approximate': True,
              'window': window, 'timeframe_sec': timeframe_sec}
    prior_high, prior_low = max(r['high'] for r in prior), min(r['low'] for r in prior)
    return dict(empty, status='OK', last_closed_ts=closed_ts, last_open_ts=last['ts'],
                close=last['close'], previous_close=prior[-1]['close'],
                trend_return_pct=trend_return, trend_side='UP' if trend_return > 0 else 'DOWN' if trend_return < 0 else 'FLAT',
                volume_ratio=last['volume']/baseline if baseline > 0 else None,
                total_volume=total, signed_volume_proxy=sum(r['volume']*(1 if r['close']>r['open'] else -1 if r['close']<r['open'] else 0) for r in bars),
                signed_volume_proxy_label='Candle-sign volume proxy; not aggressor delta',
                aggressor_delta={'status': 'OBSERVED_TAKER_VOLUME' if delta_known else 'UNKNOWN',
                                 'value': sum(2*r['taker_buy_volume']-r['volume'] for r in bars) if delta_known else None},
                price_action={'prior_high': prior_high, 'prior_low': prior_low,
                              'confirmed_range_break': 'UP' if last['close']>prior_high else 'DOWN' if last['close']<prior_low else None,
                              'reference_ends_at': last['ts']}, vp20=vp)


def breakout_scenario(zones, spot, features, now, price_fresh=True):
    """Nearest zone cannot be skipped to make the >1% condition pass."""
    result = {'status': 'WAIT_DATA', 'direction': None, 'source_zone': None, 'next_zone': None,
              'confirmation': {'confirmed': False, 'close': None, 'close_ts': None,
                               'required_level': None, 'source_known_before_bar': False},
              'conditions': [], 'eligibility_gt1': False, 'distance_pct': None,
              'probability': unknown_probability(), 'actionable': False}
    if not price_fresh or not spot or features.get('status') != 'OK' or now-features.get('last_closed_ts',0)>360:
        return result
    direction = features.get('trend_side')
    if direction not in ('UP','DOWN'):
        return result
    sign = 1 if direction == 'UP' else -1
    # Geometric barriers come first. Missing freshness never permits skipping a
    # nearer barrier to manufacture a more distant >1% objective.
    active = sorted([z for z in zones if not z.get('approximate')], key=lambda z: z['anchor'])
    ahead = [z for z in active if (z['low']>spot if sign==1 else z['high']<spot)]
    behind = [z for z in active if (z['high']<=spot if sign==1 else z['low']>=spot)]
    next_zone = (ahead[0] if sign==1 else ahead[-1]) if ahead else None
    source = (behind[-1] if sign==1 else behind[0]) if behind else None
    overlapping = [z for z in active if z['low']<=spot<=z['high']]
    if overlapping:
        source = overlapping[0]
    blocked = next((z for z in (next_zone, source) if z and not z.get('eligible')), None)
    if blocked:
        result.update(direction=direction, source_zone=source, blocking_zone=blocked,
                      conditions=['Zona inmediata antigua o no verificable; esperar datos, sin saltar barreras.'])
        return result
    boundary = (source['high'] if sign==1 else source['low']) if source else None
    known = bool(source and source.get('known_at',now) <= features.get('last_open_ts',0))
    confirmed = bool(known and (features['close']-boundary)*sign>0 and
                     (features['previous_close']-boundary)*sign<=0)
    distance = ((next_zone['low'] if sign==1 else next_zone['high'])/spot-1)*100 if next_zone else None
    result.update(status='CONFIRMED_CLOSE' if confirmed else 'CONDITIONAL' if next_zone else 'NO_ZONE',
                  direction=direction, source_zone=source, next_zone=next_zone, distance_pct=distance,
                  eligibility_gt1=bool(distance is not None and abs(distance)>1+1e-9),
                  confirmation={'confirmed': confirmed, 'close': features['close'],
                                'close_ts': features['last_closed_ts'], 'required_level': boundary,
                                'source_known_before_bar': known},
                  conditions=['Cierre propio de 5m cruza el borde de una zona conocida antes de abrir la vela.',
                              'Volumen relativo de la vela frente a las 19 previas; valor informativo, umbral sin calibrar.',
                              'La siguiente zona es la más cercana; no se omiten barreras para superar 1%.',
                              'Escenario descriptivo sin probabilidad calibrada ni orden.'])
    result['confluence_sufficient'] = bool(source and source['family_count'] >= 2)
    result['volume_ratio'] = features.get('volume_ratio')
    result['volume_confirmation'] = 'UNVALIDATED_THRESHOLD'
    if next_zone:
        result['probability'] = next_zone['probability']
    return result
