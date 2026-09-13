"""Fetch from public registries: cached for the run, retried, and accounted for.

Every request is counted against its host, and every failure is kept, because
the file this package writes has to say which lookups it could not make. A page
that silently showed "current" for a dependency whose registry was down would be
claiming something nobody checked.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "codesweep-ai-dashboards-deps (+https://github.com/codesweep-ai/dashboards)"
TIMEOUT = 30
RETRIES = 4
# A host that fails this many requests in a row, each after its retries, is
# down for this run: later requests fail at once, so a build stays in its time limit.
GIVE_UP_AFTER = 5
# Requests in flight at once per host, where a registry rate-limits a burst:
# npm answers a run's thousand lockfile lookups with 429s past about this.
HOST_LIMITS = {"registry.npmjs.org": 4, "api.deps.dev": 8, "mdapi.fedoraproject.org": 6,
               "bodhi.fedoraproject.org": 2, "api.scorecard.dev": 6, "src.fedoraproject.org": 4,
               "api.first.org": 2, "api.clearlydefined.io": 2}


class FetchError(Exception):
    def __init__(self, url, reason, status=None):
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason
        self.status = status


class Client:
    """A thread-safe fetcher. One per run, so its cache is the run's cache."""

    def __init__(self, token=None, opener=None):
        self.token = token
        self._open = opener or urllib.request.urlopen
        self._cache = {}
        self._failed = {}
        self._inflight = {}
        self._lock = threading.Lock()
        self._gates = {host: threading.Semaphore(n) for host, n in HOST_LIMITS.items()}
        self._down = {}
        self.stats = {}

    def _count(self, url, failed, reason=None):
        host = urllib.parse.urlparse(url).netloc
        with self._lock:
            s = self.stats.setdefault(host, {"requests": 0, "failures": 0, "errors": []})
            s["requests"] += 1
            if failed:
                s["failures"] += 1
                if len(s["errors"]) < 5:
                    s["errors"].append(reason)

    def get(self, url, headers=None, data=None, accept_missing=False):
        """Return the body as bytes, or None for a 404 when accept_missing.

        Identical requests made concurrently wait for the first, so a module
        shared by every project is fetched once however many ask for it.
        """
        body = json.dumps(data).encode() if data is not None else None
        key = (url, body, tuple(sorted((headers or {}).items())))
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            if key in self._failed:
                raise self._failed[key]
            event = self._inflight.get(key)
            if event is None:
                self._inflight[key] = threading.Event()
        if event is not None:
            event.wait()
            with self._lock:
                if key in self._cache:
                    return self._cache[key]
                raise self._failed.get(key) or FetchError(url, "failed in another lookup")
        try:
            result = self._fetch(url, headers, body, accept_missing)
            with self._lock:
                self._cache[key] = result
            return result
        except FetchError as exc:
            # Remembered, so a registry that is down costs its retries once
            # rather than once for every project that asks.
            with self._lock:
                self._failed[key] = exc
            raise
        finally:
            with self._lock:
                self._inflight.pop(key).set()

    def _fetch(self, url, headers, body, accept_missing):
        h = {"User-Agent": USER_AGENT}
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        last = None
        host = urllib.parse.urlparse(url).netloc
        if self._down.get(host, 0) >= GIVE_UP_AFTER:
            self._count(url, True, "not tried: the host failed every recent request")
            raise FetchError(url, "not tried: the host failed every recent request")
        gate = self._gates.get(host)
        for attempt in range(RETRIES + 1):
            req = urllib.request.Request(url, data=body, headers=h)
            wait = 1.5 * (attempt + 1)
            try:
                if gate:
                    gate.acquire()
                try:
                    with self._open(req, timeout=TIMEOUT) as resp:
                        payload = resp.read()
                finally:
                    if gate:
                        gate.release()
                self._count(url, False)
                self._down[host] = 0
                return payload
            except urllib.error.HTTPError as exc:
                # 404 is "no such thing"; mdapi says the same with a 400, and a
                # module proxy with a 410.
                if exc.code in (400, 404, 410) and accept_missing:
                    self._count(url, False)
                    return None
                last = FetchError(url, f"HTTP {exc.code}", exc.code)
                # A 4xx other than rate limiting will say the same thing again.
                if exc.code < 500 and exc.code != 429:
                    break
                if exc.code == 429:
                    after = (exc.headers or {}).get("Retry-After")
                    wait = min(int(after), 60) if after and str(after).isdigit() else 5 * (attempt + 1)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last = FetchError(url, str(getattr(exc, "reason", exc)))
            if attempt < RETRIES:
                time.sleep(wait)
        self._count(url, True, last.reason)
        if last.status is None or last.status >= 500 or last.status == 429:
            with self._lock:
                self._down[host] = self._down.get(host, 0) + 1
        raise last

    def json(self, url, headers=None, data=None, accept_missing=False):
        raw = self.get(url, headers=headers, data=data, accept_missing=accept_missing)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise FetchError(url, f"not JSON: {exc}") from exc

    def text(self, url, headers=None, accept_missing=False):
        raw = self.get(url, headers=headers, accept_missing=accept_missing)
        return None if raw is None else raw.decode("utf-8", "replace")

    def report(self):
        """The per-host tally, for the `sources` block of deps.json."""
        with self._lock:
            return [
                {"host": host, "requests": s["requests"], "failures": s["failures"],
                 "errors": list(s["errors"])}
                for host, s in sorted(self.stats.items())
            ]
