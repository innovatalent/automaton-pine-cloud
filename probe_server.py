"""Fixed public-source connectivity probe; contains no trading/account code."""
import concurrent.futures
import datetime
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

URLS = {
    'bybit': 'https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60&limit=2',
    'binance': 'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=2',
    'deribit': 'https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd',
    'yahoo': 'https://query1.finance.yahoo.com/v8/finance/chart/QQQ?interval=1d&range=5d',
}
STATE = {'status': 'checking', 'generator_deployed': False}


def check(item):
    name, url = item
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.load(response)
            valid = {
                'bybit': lambda: data.get('retCode') == 0 and bool(data.get('result', {}).get('list')),
                'binance': lambda: isinstance(data, list) and bool(data),
                'deribit': lambda: bool(data.get('result', {}).get('index_price')),
                'yahoo': lambda: bool(data.get('chart', {}).get('result')),
            }[name]()
            return name, {'http': response.status, 'valid_data': bool(valid)}
    except Exception as error:
        return name, {'http': getattr(error, 'code', None), 'valid_data': False,
                      'error': type(error).__name__}


def run_checks():
    global STATE
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(check, URLS.items()))
    STATE = {'status': 'complete', 'generator_deployed': False,
             'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'sources': results, 'all_sources_ok': all(r['valid_data'] for r in results.values())}
    print(json.dumps(STATE), flush=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?')[0] not in ('/', '/status', '/healthz'):
            self.send_error(404)
            return
        body = json.dumps(STATE, indent=2).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    threading.Thread(target=run_checks, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '10000'))), Handler).serve_forever()
