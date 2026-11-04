"""CPU / 메모리 / GPU 사용량: 전체와 프로세스별(서버, 클라, GPT-SoVITS, RVC). psutil 은 필수, GPU(NVIDIA)는 nvidia-ml-py 가 있을 때만."""
import collections
import threading
import time

import psutil

try:   # 선택: 없으면 GPU 칸만 비워 둔다
    import pynvml
except Exception:  # noqa: BLE001
    pynvml = None

HISTORY = 90   # 그래프에 쓰는 표본 수 (2초 간격이면 3분)


def classify(cmdline):
    """서버 프로세스 트리 안의 프로세스를 이름으로 나눈다 (GPT-SoVITS / RVC / 그 밖은 서버)."""
    joined = " ".join(cmdline or []).lower()
    if "api_v2.py" in joined:
        return "sovits"
    if "rvc_python" in joined:
        return "rvc"
    return "server"


class Gpu:
    def __init__(self):
        self.handle = None
        self.name = ""
        self.error = "nvidia-ml-py 가 설치돼 있지 않아요" if pynvml is None else ""
        if pynvml is not None:
            try:
                pynvml.nvmlInit()
                self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                n = pynvml.nvmlDeviceGetName(self.handle)
                self.name = n.decode() if isinstance(n, bytes) else str(n)
            except Exception as e:  # noqa: BLE001
                self.handle, self.error = None, f"NVIDIA GPU 를 찾지 못했어요 ({e})"

    def sample(self):
        if self.handle is None:
            return None
        try:
            u = pynvml.nvmlDeviceGetUtilizationRates(self.handle)
            m = pynvml.nvmlDeviceGetMemoryInfo(self.handle)
            return {"name": self.name, "util": float(u.gpu), "vram_used": int(m.used), "vram_total": int(m.total)}
        except Exception:  # noqa: BLE001
            return None


class ResourceMonitor:
    def __init__(self, pids_fn, interval=2.0):
        """pids_fn() -> {'server': pid|None, 'client': pid|None}"""
        self.pids_fn, self.interval = pids_fn, interval
        self.gpu = Gpu()
        self._procs = {}
        self._ncpu = psutil.cpu_count() or 1
        self.history = {k: collections.deque(maxlen=HISTORY) for k in ("cpu", "ram", "gpu", "vram")}
        self.last = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        psutil.cpu_percent(None)

    def start(self):
        threading.Thread(target=self._run, name="resource-monitor", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception:  # noqa: BLE001  모니터링이 죽어도 런처는 계속
                pass
            self._stop.wait(self.interval)

    def _proc(self, pid):
        p = self._procs.get(pid)
        if p is None:
            try:
                p = self._procs[pid] = psutil.Process(pid)
                p.cpu_percent(None)   # 첫 호출은 기준점만 잡는다
            except psutil.Error:
                return None
        return p

    def _tree(self, root_pid):
        root = self._proc(root_pid) if root_pid else None
        if root is None:
            return []
        try:
            return [root] + [c for c in (self._proc(x.pid) for x in root.children(recursive=True)) if c is not None]
        except psutil.Error:
            return [root]

    def sample(self):
        groups = {k: {"cpu": 0.0, "rss": 0, "n": 0} for k in ("server", "client", "sovits", "rvc")}
        seen = set()
        for kind, pid in (self.pids_fn() or {}).items():
            for p in self._tree(pid):
                try:
                    with p.oneshot():
                        key = classify(p.cmdline()) if kind == "server" else kind
                        g = groups[key]
                        g["cpu"] += p.cpu_percent(None) / self._ncpu   # 전체 CPU 에서 차지하는 %
                        g["rss"] += p.memory_info().rss
                        g["n"] += 1
                        seen.add(p.pid)
                except psutil.Error:
                    continue
        for pid in [x for x in self._procs if x not in seen and not psutil.pid_exists(x)]:
            self._procs.pop(pid, None)
        vm = psutil.virtual_memory()
        gpu = self.gpu.sample()
        s = {"t": time.time(), "cpu": psutil.cpu_percent(None), "ram_used": vm.used, "ram_total": vm.total, "procs": groups, "gpu": gpu,
             "gpu_error": "" if gpu else self.gpu.error}
        with self._lock:
            self.last = s
            self.history["cpu"].append(s["cpu"])
            self.history["ram"].append(vm.percent)
            if gpu:
                self.history["gpu"].append(gpu["util"])
                self.history["vram"].append(100.0 * gpu["vram_used"] / max(1, gpu["vram_total"]))
        return s

    def snapshot(self):
        with self._lock:
            return {"last": self.last, "history": {k: list(v) for k, v in self.history.items()}}
