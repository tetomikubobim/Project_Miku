"""로그 수집: 메모리 링 버퍼 + 파일(회전) + 다른 프로그램이 파일에 쓰는 로그(GPT-SoVITS/RVC) 따라 읽기.
UI 는 이벤트를 받지 않고 since(seq) 로 새 줄만 가져간다 (스레드 걱정 없음)."""
import collections
import os
import re
import threading
import time

LLM_TAG = "[llm]"
MAX_ENTRIES = 20000
FILE_MAX_BYTES = 5 * 1024 * 1024
FILE_KEEP = 3
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class Entry:
    __slots__ = ("seq", "ts", "source", "text")

    def __init__(self, seq, ts, source, text):
        self.seq, self.ts, self.source, self.text = seq, ts, source, text

    @property
    def is_llm(self):
        return LLM_TAG in self.text

    def format(self):
        return f"{time.strftime('%H:%M:%S', time.localtime(self.ts))} [{self.source}] {self.text}"


class _RotatingFile:
    def __init__(self, path):
        self.path = path
        self.f = None

    def write(self, line):
        try:
            if self.f is None:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                self.f = open(self.path, "a", encoding="utf-8", buffering=1)
            if self.f.tell() > FILE_MAX_BYTES:
                self.f.close()
                for i in range(FILE_KEEP - 1, 0, -1):
                    a, b = f"{self.path}.{i}", f"{self.path}.{i + 1}"
                    if os.path.exists(a):
                        os.replace(a, b)
                os.replace(self.path, f"{self.path}.1")
                self.f = open(self.path, "a", encoding="utf-8", buffering=1)
            self.f.write(line + "\n")
        except OSError:
            self.f = None   # 로그 파일을 못 써도 화면 로그는 계속된다

    def close(self):
        if self.f:
            try:
                self.f.close()
            except OSError:
                pass
            self.f = None


class LogHub:
    def __init__(self, log_dir=None):
        self._lock = threading.Lock()
        self._items = collections.deque(maxlen=MAX_ENTRIES)
        self._seq = 0
        self._dir = log_dir
        self._files = {}

    def add(self, source, text):
        text = _ANSI.sub("", str(text)).rstrip("\r\n")
        with self._lock:
            self._seq += 1
            e = Entry(self._seq, time.time(), source, text)
            self._items.append(e)
            if self._dir:
                f = self._files.get(source)
                if f is None:
                    f = self._files[source] = _RotatingFile(os.path.join(self._dir, f"{source}.log"))
                f.write(e.format())
        return e

    def since(self, seq=0):
        """seq 보다 뒤에 쌓인 항목 목록 (오래된 것이 버려졌으면 남은 것부터)."""
        with self._lock:
            if not self._items or self._items[-1].seq <= seq:
                return []
            return [e for e in self._items if e.seq > seq]

    def last_seq(self):
        with self._lock:
            return self._seq

    def close(self):
        with self._lock:
            for f in self._files.values():
                f.close()
            self._files.clear()


class FileTail:
    """다른 프로그램이 파일에 쓰는 로그를 새로 늘어난 만큼만 읽어서 허브에 넣는다 (GPT-SoVITS, RVC).
    시작할 때는 파일 끝에서부터 본다 (지난 기록을 한꺼번에 쏟아내지 않는다). 진행 막대(\\r)는 마지막 상태만 남긴다."""

    def __init__(self, hub, source, path_fn, interval=1.0):
        self.hub, self.source, self.path_fn, self.interval = hub, source, path_fn, interval
        self._pos = None
        self._path = None
        self._buf = b""
        self._stop = threading.Event()
        self._t = None

    def start(self):
        self._t = threading.Thread(target=self._run, name=f"tail-{self.source}", daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.poll()
            except Exception:  # noqa: BLE001
                pass

    def poll(self):
        path = self.path_fn()
        if not path or not os.path.exists(path):
            self._pos = None
            return
        size = os.path.getsize(path)
        if path != self._path or self._pos is None:
            self._path, self._pos, self._buf = path, size, b""   # 처음 보는 파일: 끝에서부터
            return
        if size < self._pos:       # 잘렸거나 새로 만들어짐
            self._pos, self._buf = 0, b""
        if size == self._pos:
            return
        with open(path, "rb") as f:
            f.seek(self._pos)
            data = f.read(min(size - self._pos, 1 << 20))
        self._pos += len(data)
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            text = raw.decode("utf-8", "replace").split("\r")[-1].rstrip()
            if text.strip():
                self.hub.add(self.source, text)
