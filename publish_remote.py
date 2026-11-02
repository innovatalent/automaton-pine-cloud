"""GitHub runner: mirror only a fresh verified Pine produced by Render."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import urllib.request

BASE = 'https://automaton-pine-probe.onrender.com'
NOT_BEFORE = dt.datetime(2026, 11, 2, 23, 0, tzinfo=dt.timezone.utc)


def report(message):
    print(message)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(message + '\n\n')


def fetch(path):
    request = urllib.request.Request(BASE+path, headers={'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def verify(status, body, now):
    if status.get('generator_deployed') is not True or status.get('stale', True):
        raise ValueError('Cloud generation unavailable or stale')
    metadata = status['last_generation']
    stamp = dt.datetime.fromisoformat(metadata['generated_at_utc'])
    if not 0 <= (now-stamp).total_seconds() <= 900:
        raise ValueError('Generation timestamp outside freshness window')
    if hashlib.sha256(body).hexdigest() != metadata['sha256']:
        raise ValueError('Generation changed during download; retry next cycle')
    if not body.startswith(b'//@version=6') or len(body) < 10000 or b'__TV_DATOS__' in body:
        raise ValueError('Invalid Pine payload')
    return metadata


def main():
    now = dt.datetime.now(dt.timezone.utc)
    if now < NOT_BEFORE:
        report('Esperando el vencimiento del bloqueo de Binance. No se consulta Render ni se publica un archivo.')
        return
    status = json.loads(fetch('/status'))
    if status.get('generator_deployed') is not True or status.get('stale', True):
        report('Todavia no hay un Pine reciente para publicar. Estado: ' + json.dumps(status.get('worker', {})))
        return
    body = fetch('/AUTOMATON_ACTUAL.pine')
    metadata = verify(status, body, dt.datetime.now(dt.timezone.utc))
    output = Path('remote-output')
    output.mkdir(exist_ok=True)
    (output/'AUTOMATON_ACTUAL.pine').write_bytes(body)
    (output/'estado.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write('ready=true\n')
    report('Pine remoto verificado: ' + metadata['generated_at_utc'])


if __name__ == '__main__':
    main()
