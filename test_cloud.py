import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import cloud_server as server
import cloud_worker as worker
import network_guard as guard
import publish_remote as publisher
from storage import atomic_bytes


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.public = Path(self.temp.name)
        self.patch = patch.object(server, 'PUBLIC', self.public)
        self.patch.start()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:%d' % self.http.server_port

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.patch.stop()
        self.temp.cleanup()

    def publish(self, age=0, digest=None):
        body = b'//@version=6\nindicator("test")\n'
        atomic_bytes(self.public/'AUTOMATON_ACTUAL.pine', body)
        atomic_bytes(self.public/'estado.json', json.dumps({
            'generated_at_utc': (dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=age)).isoformat(),
            'sha256': digest or hashlib.sha256(body).hexdigest()}).encode())
        return body

    def test_download_requires_fresh_valid_generation(self):
        for age, digest in [(0, None), (1000, None), (0, 'bad')]:
            body = self.publish(age, digest)
            if age or digest:
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(self.url+'/AUTOMATON_ACTUAL.pine')
                self.assertEqual(raised.exception.code, 503)
            else:
                with urllib.request.urlopen(self.url+'/AUTOMATON_ACTUAL.pine') as response:
                    self.assertEqual(response.read(), body)
                    self.assertIn('attachment', response.headers['Content-Disposition'])

    def test_empty_service_does_not_claim_generated(self):
        with urllib.request.urlopen(self.url+'/status') as response:
            result = json.load(response)
        self.assertTrue(result['stale'])
        self.assertEqual(result['last_generation'], {})

    def test_respects_retry_after_and_ban_deadline(self):
        now = time.time()
        deadline = worker.deadline_for({'binance': {'retry_after': '70000'}}, now)
        self.assertGreaterEqual(deadline, now + 70060)
        deadline = worker.deadline_for({'binance': {'ban_until_utc': dt.datetime.fromtimestamp(now+80000, dt.timezone.utc).isoformat()}}, now)
        self.assertAlmostEqual(deadline, now + 80060, delta=0.001)

    def test_guard_blocks_repeat_requests_and_private_routes(self):
        with patch.dict(guard._until, {}, clear=True), patch.object(guard, '_failures', set()):
            guard.record('https://fapi.binance.com/test', 418, {'Retry-After': '70000'})
            with self.assertRaises(RuntimeError):
                guard.before('https://fapi.binance.com/other', 'GET')
            with self.assertRaises(RuntimeError):
                guard.before('http://localhost:8890/api/tv_pine_full', 'GET')
            with self.assertRaises(RuntimeError):
                guard.before('https://api.bybit.com/v5/order/create', 'POST')

    def test_mirror_rejects_stale_or_changed_downloads(self):
        now = dt.datetime.now(dt.timezone.utc)
        body = b'//@version=6\n' + b'/' * 10000
        status = {'generator_deployed': True, 'stale': False, 'last_generation': {
            'generated_at_utc': now.isoformat(), 'sha256': hashlib.sha256(body).hexdigest()}}
        self.assertEqual(publisher.verify(status, body, now), status['last_generation'])
        with self.assertRaises(ValueError):
            publisher.verify(status, body+b'changed', now)
        with self.assertRaises(ValueError):
            publisher.verify(status, body, now+dt.timedelta(seconds=901))


if __name__ == '__main__':
    unittest.main()
