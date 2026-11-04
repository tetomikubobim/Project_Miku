"""서버의 GET /health 를 주기적으로 불러서 마지막 응답을 들고 있는다 (서버/STT/TTS 상태의 출처)."""
import threading
import time

import requests


class HealthPoller:
    def __init__(self, url_fn, interval=2.0, timeout=1.5, get=requests.get):
        self.url_fn, self.interval, self.timeout, self._get = url_fn, interval, timeout, get
        self.last = None            # 마지막으로 성공한 응답 (dict) 또는 None
        self.last_ok_at = None
        self.fail_count = 0
        self.ever_ok = False        # 이번에 켠 서버가 한 번이라도 응답했는가
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._t = None

    def start(self):
        self._t = threading.Thread(target=self._run, name="health-poller", daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def poll_now(self):
        self._wake.set()

    def reset(self):
        """서버를 새로 켤 때: 지난 상태를 버린다."""
        with self._lock:
            self.last, self.fail_count, self.ever_ok = None, 0, False

    def _run(self):
        while not self._stop.is_set():
            self.check()
            self._wake.wait(self.interval)
            self._wake.clear()

    def check(self):
        try:
            r = self._get(self.url_fn() + "/health", timeout=self.timeout)
            body = r.json() if r.status_code == 200 else None
            if not isinstance(body, dict) or not body.get("ok"):
                body = None
        except (requests.RequestException, ValueError):
            body = None
        with self._lock:
            if body is None:
                self.last, self.fail_count = None, self.fail_count + 1
            else:
                self.last, self.fail_count, self.ever_ok, self.last_ok_at = body, 0, True, time.time()
        return body
