"""Source provenance and strict, timestamped Pine confluences; no probabilities."""
from __future__ import annotations

import math
import re

from direction_engine.confluence_zones import cluster_levels


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def source_fields(state, reference, scaled=False):
    """Export source time, never pretend request/publication time is source time."""
    if state is None:
        return []
    ref = re.sub(r'[^A-Za-z0-9_.-]', '', str(reference or 'UNKNOWN'))
    out = ['GSRC:' + ('S' if scaled else 'P'), 'GREF:' + ref,
           'GTTL:' + ('300' if ref in ('BTC', 'ETH') else '600')]
    for tag, key in (('GTS', 'ts'), ('GEX', 'net_gex_musd'), ('GEXP', 'proximo_vto_ts')):
        value = number(getattr(state, key, None))
        if value is not None and (tag == 'GEX' or value > 0):
            out.append(f'{tag}:{value:.9f}'.rstrip('0').rstrip('.'))
    share = number(getattr(state, 'proximo_vto_share_abs', None))
    if (getattr(state, 'expiry_share_convention', None) == 'weighted_abs_gex_v1'
            and getattr(state, 'expiry_share_version', None) == 1
            and number(getattr(state, 'expiry_share_ramp_days', None)) is not None
            and share is not None and 0 <= share <= 1):
        out.append(f'GSH:{share:.6g}')
    return out


_OPTIONS = {
    'FL': 'gamma_flip', 'CW': 'call_wall', 'PW': 'put_wall',
    'GMX': 'gamma_max', 'GVW': 'vanna_wall', 'GVF': 'vanna_flip',
    'GCW': 'charm_wall', 'GDW': 'delta_wall', 'GDZ': 'delta_zero',
    'GVT': 'vol_trigger', 'OMAXPAIN': 'max_pain',
    'OCALLW2': 'call_wall_2', 'OPUTW2': 'put_wall_2',
    'P1': 'gex_P1', 'P2': 'gex_P2', 'N1': 'gex_N1',
    'N2': 'gex_N2', 'A1': 'max_abs_A1', 'A2': 'max_abs_A2',
}


def _fields(fields):
    return {p[0]: p[1:] for text in fields[1:] if len(p := text.split(':')) > 1}


def verified_confluences(fields, profile_row, now, band=.3, minimum=2):
    """Only provenance-attested current members enter the multi-asset watchlist.

    CF count is independent families, not aliases. Projected leader levels,
    un-timestamped targets/liquidation estimates and un-timestamped VP are not
    represented as validated confluence. This verifies geometry, not an edge.
    """
    meta = _fields(fields)
    def val(tag):
        return number((meta.get(tag) or [None])[0])
    def valid(ts, ttl):
        return ts is not None and ts > 0 and ttl is not None and ttl > 0 and 0 <= now-ts <= ttl
    gts, gttl, expiry = val('GTS'), val('GTTL'), val('GEXP')
    bts, bttl = val('BTS'), val('BTTL')
    own = meta.get('GSRC') == ['P']
    gamma_ok = own and valid(gts, gttl) and ('GEXP' not in meta or expiry is not None and expiry > now)
    blocks_ok = valid(bts, bttl)
    members = []
    def add(px, name, family, ts, ttl, until):
        px = number(px)
        if px is not None and px > 0:
            members.append(dict(price=px, name=name, family=family,
                                source='pine_source_snapshot', ts=ts, ttl_sec=ttl,
                                valid_until=until, eligible=True))
    ob_index = 0
    for text in fields[1:]:
        p = text.split(':'); tag = p[0]
        if tag in _OPTIONS and gamma_ok and len(p) >= 2:
            add(p[1], _OPTIONS[tag], 'options', gts, gttl,
                min(gts+gttl, expiry) if expiry is not None else gts+gttl)
        elif tag == 'OB' and blocks_ok and len(p) >= 4:
            top, bottom = number(p[1]), number(p[2])
            if top is not None and bottom is not None and 0 < bottom <= top and p[3] in ('L','S'):
                ob_index += 1
                add((top+bottom)/2, f'ob_{p[3]}{ob_index}', 'SMC', bts, bttl, bts+bttl)
    for field in ('poc','vah','val','vwap'):
        ts = number(profile_row.get(field+'_ts', profile_row.get('vp_ts', profile_row.get('profile_ts'))))
        ttl = 360.
        if valid(ts, ttl):
            add(profile_row.get(field), field, 'VWAP' if field == 'vwap' else 'volume_profile', ts, ttl, ts+ttl)
    zones = cluster_levels(members, None, threshold_pct=band, now=now)
    out = []
    for zone in zones:
        if zone['eligible'] and zone['family_count'] >= minimum:
            out.append(dict(centro=zone['anchor'], n=zone['family_count'],
                            nombres=zone['names'], grupos=set(zone['families']),
                            lo=zone['low'], hi=zone['high'], members=len(zone['members']),
                            valid_until=min(m['valid_until'] for m in zone['members']),
                            peso=zone['family_count']))
    return sorted(out, key=lambda z:(-z['n'], z['centro']))


def strict_legacy_zones(levels, band=.3, minimum=2):
    """Geometry-only compatibility helper; unit times are NOT source provenance."""
    members = []
    for px, name, group, _weight in levels:
        family = ('options' if group in ('FLIP','MURO','GMAX','VANNA','CHARM','DELTA','VOLTRG','OPTIONS') or str(group).startswith('g')
                  else 'volume_profile' if group in ('POC','VA','NPOC')
                  else 'SMC' if group in ('OB','FVG','IFVG','BPR','SMC') else group)
        aliases = {'GM':'gamma_max','VW':'vanna_wall','VF':'vanna_flip','CH':'charm_wall','DW':'delta_wall','D0':'delta_zero','VT':'vol_trigger'}
        canonical = _OPTIONS.get(name, aliases.get(name, name) if family == 'options' else name)
        members.append(dict(price=px, name=canonical, family=family, ts=1, ttl_sec=1))
    result = cluster_levels(members, None, threshold_pct=band, now=1)
    return [dict(centro=z['anchor'], n=z['family_count'], nombres=z['names'],
                 grupos=set(z['families']), lo=z['low'], hi=z['high'],
                 members=len(z['members']), peso=z['family_count'])
            for z in sorted(result,key=lambda z:(-z['family_count'],z['anchor']))
            if z['family_count']>=minimum]
