"""One remote generation, using the existing indicator and market calculations."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT/'forward_test'), str(ROOT/'direction_engine')]
os.environ['ENGINE_TG'] = '0'
os.environ['AUTOMATON_DASHBOARD_UI_ONLY'] = '1'
# No credentials are needed for market reads; never inherit execution credentials.
for key in list(os.environ):
    if any(part in key for part in ('BYBIT_API', 'TELEGRAM_', 'ANTHROPIC_', 'OPENROUTER_', 'BITUNIX_API')):
        os.environ.pop(key, None)

def stage(name):
    print(json.dumps({'stage': name, 'utc': dt.datetime.now(dt.timezone.utc).isoformat()}), flush=True)

def main():
    started = time.time()
    stage('import')
    from direction_engine import cloud_core as core
    from direction_engine import sott_zone
    from direction_engine.pine_export_payload import project_ob_times
    from storage import atomic_bytes
    import network_guard
    import gex_live
    import confluence_watch
    import stock_liquidity
    stage('gamma')
    gex_live.refresh_once()
    # The single-run job has already initialized gamma. Do not launch endless collectors.
    gex_live._started = True
    stage('confluence')
    core._cw_cache['data'] = confluence_watch.scan(sott_zone.roster())
    core._cw_cache['ts'] = time.time()
    stage('profiles')
    core._vp_ext_cache['ts'] = 0
    profile = core._vp_extremes_data()
    if profile.get('error') or not profile.get('rows'):
        raise RuntimeError('No fresh market profiles: ' + str(profile.get('error')))
    stage('stock_options')
    for symbol in sorted(core._STOCKS):
        try:
            levels = stock_liquidity.liquidity_levels(symbol)
            if levels:
                core._stock_liq_cache[symbol] = levels
        except Exception as exc:
            print(json.dumps({'unavailable': symbol, 'error_type': type(exc).__name__}), flush=True)
    stage('export')
    # The profile scan can outlast the five-minute gamma TTL.
    gex_live.refresh_once()
    with core.app.test_request_context('/api/tv_niveles?json=1'):
        response = core.api_tv_niveles()
        payload = json.loads(response.get_data(as_text=True))
    if payload.get('error') or payload.get('de_cache'):
        raise RuntimeError('Export failed or returned stale fallback')
    if network_guard.failures():
        raise RuntimeError('Blocked market sources: ' + ', '.join(network_guard.failures()))
    data = payload['cadena']
    blocks = {b.split('~')[0]: b for b in data.split(';') if b}
    if len(blocks) < 14 or not {'BTC', 'ETH', 'SOL', 'XAU', 'CL'} <= blocks.keys():
        raise RuntimeError('Incomplete export: ' + ','.join(blocks))
    # Gamma is essential to this indicator. Never publish a successful-looking empty copy.
    for symbol in ('BTC', 'ETH'):
        fields = dict(part.split(':', 1) for part in blocks[symbol].split('~')[1:] if ':' in part)
        if not (0 <= time.time()-float(fields.get('GTS', 0)) <= float(fields.get('GTTL', 300))):
            raise RuntimeError('Missing fresh gamma for ' + symbol)
    template = (ROOT/'templates/AUTOMATON_NIVELES.pine').read_text(encoding='utf-8-sig')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    clean = ''.join(c for c in data if c not in '"\\\n\r')
    pine = template.replace('__TV_DATOS__', clean).replace('__TV_OB_TIMES__', project_ob_times(clean)).replace('__TV_SELLO__', f'{stamp} - {len(blocks)} activos')
    if any(token in pine for token in ('__TV_DATOS__', '__TV_OB_TIMES__', '__TV_SELLO__')) or not pine.startswith('//@version=6'):
        raise RuntimeError('Invalid Pine template')
    output = ROOT/'public'
    output.mkdir(exist_ok=True)
    status = {'generated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'source': 'Render / public market APIs', 'assets': list(blocks),
              'sha256': hashlib.sha256(pine.encode()).hexdigest(),
              'elapsed_seconds': round(time.time()-started, 1),
              'target_interval_seconds': 60, 'status': 'generated',
              'note': 'Interval depends on computation time. Market sources have independent timestamps and TTLs. Local account positions and historical engine databases are unavailable.'}
    atomic_bytes(output/'AUTOMATON_ACTUAL.pine', pine.encode())
    atomic_bytes(output/'estado.json', json.dumps(status, ensure_ascii=False, indent=2).encode())
    stage('complete')
    print(json.dumps(status), flush=True)

if __name__ == '__main__':
    main()
