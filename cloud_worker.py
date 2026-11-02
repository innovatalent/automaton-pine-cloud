"""Single persistent collector. Never query a blocked source before its deadline."""
import datetime as dt
import json
import time
from pathlib import Path
from email.utils import parsedate_to_datetime
from storage import atomic_bytes

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT/'public'
# Confirmed by the deployed source probe. Preserve across cold starts.
INITIAL_COOLDOWN = dt.datetime(2026, 11, 2, 22, 50, tzinfo=dt.timezone.utc).timestamp()


def deadline_for(results, now):
    deadline = now + 900
    for value in results.values():
        retry = value.get('retry_after')
        if retry:
            try:
                deadline = max(deadline, now + float(retry) + 60)
            except ValueError:
                try:
                    deadline = max(deadline, parsedate_to_datetime(retry).timestamp() + 60)
                except (ValueError, TypeError):
                    pass
        if value.get('ban_until_utc'):
            deadline = max(deadline, dt.datetime.fromisoformat(value['ban_until_utc']).timestamp() + 60)
    return deadline


def write_status(state, **fields):
    data = {'state': state, 'updated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), **fields}
    atomic_bytes(PUBLIC/'worker.json', json.dumps(data).encode())
    print(json.dumps(data), flush=True)


def main():
    import network_guard
    network_guard.install()
    next_attempt = INITIAL_COOLDOWN
    try:
        previous = json.loads((PUBLIC/'worker.json').read_text())
        next_attempt = max(next_attempt, float(previous.get('retry_at_epoch', 0)))
    except (OSError, ValueError):
        pass
    while True:
        now = time.time()
        if now < next_attempt:
            write_status('waiting_for_source', retry_at_epoch=next_attempt,
                         retry_at_utc=dt.datetime.fromtimestamp(next_attempt, dt.timezone.utc).isoformat())
            time.sleep(min(next_attempt-now, 3600))
            continue
        import probe_server
        with probe_server.concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            sources = dict(pool.map(probe_server.check, probe_server.URLS.items()))
        if not all(value['valid_data'] for value in sources.values()):
            next_attempt = deadline_for(sources, time.time())
            write_status('source_unavailable', sources=sources, retry_at_epoch=next_attempt,
                         retry_at_utc=dt.datetime.fromtimestamp(next_attempt, dt.timezone.utc).isoformat())
            continue
        write_status('generating', sources=sources)
        try:
            import generate
            network_guard.reset_failures()
            generate.main()
            write_status('ready', sources=sources)
            next_attempt = time.time() + 60
        except Exception as error:
            # No file replacement on an unsuccessful generation.
            write_status('generation_failed', error_type=type(error).__name__, error=str(error)[:240])
            next_attempt = max(time.time() + 900, network_guard.retry_at())
        time.sleep(max(1, next_attempt-time.time()))


if __name__ == '__main__':
    main()
