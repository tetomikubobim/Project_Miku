"""클라이언트 연결을 지켜보다가, 마지막 클라이언트가 끊기면 잠시 뒤 서버를 끝낸다.

- 한 번도 접속한 적이 없으면 아무것도 하지 않는다 (서버를 먼저 켜 두고 클라이언트를 나중에 켜는 경우를 위해).
- 끊기고 grace 초 안에 다시 접속하면 취소한다 (클라이언트 재시작/재접속, 절전 복귀 등).
- 여러 클라이언트가 붙어 있으면 모두 끊겼을 때만 센다.
config.yaml:  shutdown_with_client: true   /   shutdown_grace_seconds: 15
"""
import asyncio

DEFAULT_GRACE = 15.0


def grace_from(cfg):
    try:
        g = float(cfg.get("shutdown_grace_seconds", DEFAULT_GRACE))
    except (TypeError, ValueError):
        return DEFAULT_GRACE
    return g if g >= 0 else DEFAULT_GRACE


def enabled(cfg):
    """생략하면 켜짐. 명시적으로 false 일 때만 끈다."""
    return cfg.get("shutdown_with_client", True) is not False


class ClientWatcher:
    def __init__(self, grace, on_idle, log=print):
        self.grace = grace
        self.on_idle = on_idle
        self.log = log
        self.count = 0
        self.ever = False
        self.fired = False
        self._handle = None

    def _cancel(self):
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def connected(self):
        self.count += 1
        self.ever = True
        if self._handle is not None:
            self.log("[server] 클라이언트가 다시 접속했어요. 종료를 취소해요.")
        self._cancel()

    def disconnected(self):
        """이벤트 루프 안에서 불러야 한다 (call_later 사용)."""
        self.count = max(0, self.count - 1)
        if self.count or not self.ever or self.fired:
            return
        self.log(f"[server] 클라이언트가 모두 끊겼어요. {self.grace:g}초 안에 다시 접속하지 않으면 서버를 종료해요.")
        self._cancel()
        self._handle = asyncio.get_running_loop().call_later(self.grace, self._fire)

    def _fire(self):
        self._handle = None
        if self.count == 0 and not self.fired:
            self.fired = True
            self.log("[server] 서버를 종료해요.")
            self.on_idle()
