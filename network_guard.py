"""Respect source cooldowns across the legacy collectors in a generation."""
import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

_lock = threading.Lock()
_until = {}
_failures = set()
_installed = False


def host_for(url):
    return (urlparse(str(url)).hostname or '').lower()


def before(url, method):
    host = host_for(url)
    if host in ('localhost', '127.0.0.1', '::1'):
        raise RuntimeError('Cloud generator cannot use local dashboard services')
    if 'bybit' in host or 'binance' in host or 'bitunix' in host:
        if method.upper() not in ('GET', 'HEAD') or any(p in str(url).lower() for p in ('/order/', '/account/', '/position/', '/wallet/')):
            raise RuntimeError('Only public market reads are allowed')
    with _lock:
        if _until.get(host, 0) > time.time():
            _failures.add(host)
            raise RuntimeError('Market source cooldown active: ' + host)


def record(url, code, headers):
    host = host_for(url)
    if code in (403, 418, 429, 451):
        wait = 3600 if code in (418, 429) else 900
        try:
            wait = max(wait, float(headers.get('Retry-After', 0)) + 60)
        except (ValueError, TypeError):
            pass
        with _lock:
            _until[host] = max(_until.get(host, 0), time.time() + wait)
            _failures.add(host)


def reset_failures():
    with _lock:
        _failures.clear()


def failures():
    with _lock:
        return sorted(_failures)


def retry_at():
    with _lock:
        return max(_until.values(), default=0)


def install():
    global _installed
    if _installed:
        return
    _installed = True
    original = urllib.request.urlopen

    def urlopen(request, *args, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else request
        method = request.get_method() if isinstance(request, urllib.request.Request) else 'GET'
        before(url, method)
        try:
            return original(request, *args, **kwargs)
        except urllib.error.HTTPError as error:
            record(url, error.code, error.headers)
            raise
    urllib.request.urlopen = urlopen
    import requests
    request_method = requests.sessions.Session.request

    def request(session, method, url, *args, **kwargs):
        before(url, method)
        response = request_method(session, method, url, *args, **kwargs)
        record(url, response.status_code, response.headers)
        return response
    requests.sessions.Session.request = request
