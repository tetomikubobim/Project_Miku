"""런처가 켜는 프로세스(서버, 클라) 하나를 다룬다: 창 없이 켜고, 출력을 한 줄씩 로그로 넘기고, 정상 종료를 먼저 시도하고, 안 되면 하위 프로세스까지 강제 종료."""
import os
import subprocess
import sys
import threading
import time

import psutil

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

STOPPED, STARTING, RUNNING, STOPPING, CRASHED = "stopped", "starting", "running", "stopping", "crashed"


class StartError(Exception):
    """켤 수 없는 이유 (사용자에게 그대로 보여 준다)."""


def kill_tree(pid, timeout=5.0):
    """pid 와 그 하위 프로세스를 모두 끈다."""
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = [root]
    try:
        procs += root.children(recursive=True)
    except psutil.Error:
        pass
    for p in reversed(procs):
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=timeout)


def request_close(proc):
    """정상 종료를 부탁한다. Windows: taskkill(강제 아님) 이 창에 닫기 메시지를 보낸다. 그 밖: SIGTERM."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T"], capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        else:
            proc.terminate()
    except Exception:  # noqa: BLE001
        pass


class ManagedProcess:
    def __init__(self, name, build, sink, job=None, graceful=None, on_exit=None):
        """build() -> (argv 리스트, 작업 폴더, 추가 환경변수 dict). 켤 수 없으면 StartError.
        sink(name, line): 출력 한 줄. graceful(mp) -> 정상 종료 요청을 보냈으면 True (없으면 request_close)."""
        self.name, self.build, self.sink, self.job = name, build, sink, job
        self.graceful, self.on_exit = graceful, on_exit
        self.proc = None
        self.state = STOPPED
        self.exit_code = None
        self.started_at = None
        self.stop_requested = False
        self._lock = threading.RLock()

    @property
    def pid(self):
        return self.proc.pid if self.proc is not None else None

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        """켰으면 pid. 못 켜면 StartError."""
        with self._lock:
            if self.alive:
                raise StartError("이미 실행 중이에요")
            argv, cwd, extra_env = self.build()
            env = dict(os.environ)
            env.pop("ELECTRON_RUN_AS_NODE", None)   # 이게 있으면 electron 이 앱이 아니라 node 로 동작한다
            env.update(extra_env or {})
            kwargs = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if os.name == "nt":
                kwargs["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP   # cmd 창이 뜨지 않게
            else:
                kwargs["start_new_session"] = True
            try:
                proc = subprocess.Popen(argv, **kwargs)
            except OSError as e:
                self.state = CRASHED
                raise StartError(f"실행하지 못했어요: {e}") from None
            if self.job is not None:
                self.job.attach(proc.pid)   # 런처가 강제로 죽어도 OS 가 같이 끈다
            self.proc, self.state, self.exit_code = proc, STARTING, None
            self.started_at, self.stop_requested = time.time(), False
            pump = threading.Thread(target=self._pump, args=(proc,), name=f"pump-{self.name}", daemon=True)
            pump.start()
            threading.Thread(target=self._watch, args=(proc, pump), name=f"watch-{self.name}", daemon=True).start()
            return proc.pid

    def _pump(self, proc):
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line:
                    self.sink(self.name, line)
        except (OSError, ValueError):
            pass

    def _watch(self, proc, pump):
        """프로세스가 끝나면 상태를 정리한다. 출력 파이프가 닫히길 기다리지 않는다:
        하위 프로세스(electron 의 GPU/렌더러 등)가 파이프를 쥔 채 남으면 출력 읽기가 끝나지 않아, 상태가 '끄는 중'에서 멈추기 때문."""
        code = proc.wait()
        pump.join(1.0)   # 마지막 출력 줄을 로그에 먼저 내보낸다
        with self._lock:
            if self.proc is not proc:
                return   # 그 사이 다시 켜졌다
            self.exit_code = code
            self.state = STOPPED if (self.stop_requested or code == 0) else CRASHED
        if self.on_exit:
            try:
                self.on_exit(self)
            except Exception:  # noqa: BLE001
                pass

    def mark_running(self):
        with self._lock:
            if self.state == STARTING and self.alive:
                self.state = RUNNING

    def stop(self, wait=15.0):
        """정상 종료를 먼저 부탁하고 wait 초 안에 안 꺼지면 하위 프로세스까지 강제 종료. 이미 꺼져 있으면 False."""
        with self._lock:
            if not self.alive:
                return False
            self.stop_requested = True
            self.state = STOPPING
            proc = self.proc
        try:
            descendants = psutil.Process(proc.pid).children(recursive=True)   # 정상 종료 요청 전에 하위 프로세스를 적어 둔다 (부모가 먼저 죽으면 찾을 수 없다)
        except psutil.Error:
            descendants = []
        asked = False
        if self.graceful:
            try:
                asked = bool(self.graceful(self))
            except Exception as e:  # noqa: BLE001
                self.sink("launcher", f"{self.name} 정상 종료 요청에 실패했어요: {e!r}")
        if not asked:
            request_close(proc)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.1)
        if proc.poll() is None:
            self.sink("launcher", f"{self.name} 가 {wait:g}초 안에 꺼지지 않아 강제로 끌게요")
            kill_tree(proc.pid)
        # 앞쪽 프로세스만 꺼지고 하위 프로세스가 남는 경우를 정리한다 (예: npm -> electron)
        survivors = []
        for c in descendants:
            try:
                if c.is_running():
                    c.kill()
                    survivors.append(c)
            except psutil.Error:
                pass
        if survivors:
            psutil.wait_procs(survivors, timeout=3)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return True


SERVER_MODULES = ("uvicorn", "fastapi", "yaml", "requests")
_dep_cache = {}


def _windowed_python(exe):
    """창 없는 pythonw 는 출력 파이프에 쓸 수 없어서 같은 폴더의 python 으로 바꾼다."""
    if os.path.basename(exe).lower() == "pythonw.exe":
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(cand):
            return cand
    return exe


def missing_modules(py, modules=SERVER_MODULES):
    """py 로 modules 를 불러올 수 있는지 확인한다. 못 불러오는 모듈 이름 목록 (실행 자체가 안 되면 ['실행 불가'])."""
    key = (py, tuple(modules))
    if key in _dep_cache:
        return _dep_cache[key]
    code = "import importlib.util as u,sys; print(','.join(m for m in sys.argv[1:] if u.find_spec(m) is None))"
    kwargs = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        r = subprocess.run([py, "-c", code, *modules], capture_output=True, text=True, timeout=30, **kwargs)
        out = ["실행 불가"] if r.returncode != 0 else [m for m in r.stdout.strip().split(",") if m]
    except (OSError, subprocess.SubprocessError):
        out = ["실행 불가"]
    if not out:   # 문제없는 결과만 기억한다 (패키지를 설치한 뒤 다시 켜면 바로 반영되게)
        _dep_cache[key] = out
    return out


def _pythons_on_path():
    """PATH 에 있는 python 전부 (shutil.which 는 첫 번째만 줘서, 앞에 스토어 안내용 가짜 python 이 있으면 진짜를 놓친다)."""
    names = ("python.exe",) if os.name == "nt" else ("python3", "python")
    out = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip().strip('"')
        if not d:
            continue
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p) and "WindowsApps" not in p:
                out.append(p)
                break
    return out


def _launcher_base_python():
    """런처가 venv 안에서 돌고 있으면 그 venv 를 만든 원래 파이썬 (보통 서버 패키지를 깔아 둔 시스템 파이썬)."""
    base = getattr(sys, "base_prefix", sys.prefix)
    if os.path.normcase(os.path.abspath(base)) == os.path.normcase(os.path.abspath(sys.prefix)):
        return None
    p = os.path.join(base, "python.exe") if os.name == "nt" else os.path.join(base, "bin", "python3")
    return p if os.path.isfile(p) else None


def server_python_candidates(configured, server_dir):
    """[(파이썬 경로, 어디서 찾았는지)] 우선순위 순. 설정한 값 > 서버 폴더의 venv > PATH 의 python (예전 server_run.bat 이 쓰던 것) > 런처 venv 의 원본 파이썬 > 런처의 파이썬."""
    if configured:
        return [(configured, "'런처' 탭에서 지정한 파이썬")]
    out = []
    for rel in ("venv/Scripts/python.exe", ".venv/Scripts/python.exe", "venv/bin/python", ".venv/bin/python"):
        p = os.path.join(server_dir, rel)
        if os.path.isfile(p):
            out.append((p, "서버 폴더의 venv"))
    for p in _pythons_on_path():
        out.append((_windowed_python(p), "PATH 의 python"))
    base = _launcher_base_python()
    if base:
        out.append((base, "런처 venv 를 만든 원본 파이썬"))
    if not getattr(sys, "frozen", False):   # exe 로 묶였으면 sys.executable 은 런처 자신이라 서버 파이썬이 될 수 없다
        out.append((_windowed_python(sys.executable), "런처의 파이썬"))
    seen, uniq = set(), []
    for p, why in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append((p, why))
    return uniq


def python_for_server(configured, server_dir, modules=SERVER_MODULES):
    """서버를 돌릴 파이썬을 고른다: 서버 의존성(uvicorn 등)을 실제로 불러올 수 있는 첫 번째. 하나도 없으면 StartError (이유와 해결 방법 포함).
    직접 지정한 파이썬은 의존성이 없어도 다른 걸로 바꾸지 않고 그대로 알려 준다."""
    cands = server_python_candidates(configured, server_dir)
    tried = []
    for py, why in cands:
        miss = missing_modules(py, modules)
        if not miss:
            return py, why
        tried.append(f"  - {py} ({why}): 없는 모듈 {', '.join(miss)}")
    hint = ("\n해결: 서버를 평소 돌리던 파이썬을 '런처' 탭의 '서버 파이썬'에 지정하거나, 그 파이썬에서 "
            "`pip install -r requirements.txt` (서버 폴더) 를 실행하세요.")
    raise StartError("서버를 돌릴 파이썬을 찾지 못했어요. 확인한 곳:\n" + "\n".join(tried) + hint)


def find_electron(configured, client_dir):
    """(argv 앞부분, 설명). electron 실행 파일 > .bin 의 shim > npm start 순서로 찾는다. 없으면 StartError."""
    import shutil
    if configured:
        if not os.path.isfile(configured):
            raise StartError(f"설정한 electron 파일이 없어요: {configured}")
        return [configured, "."], configured
    win = os.name == "nt"
    cands = [os.path.join(client_dir, "node_modules", "electron", "dist", "electron.exe" if win else "electron"),
             os.path.join(client_dir, "node_modules", ".bin", "electron.cmd" if win else "electron")]
    for c in cands:
        if os.path.isfile(c):
            return [c, "."], c
    npm = shutil.which("npm.cmd" if win else "npm") or shutil.which("npm")
    if npm and os.path.isfile(os.path.join(client_dir, "package.json")):
        return [npm, "start"], f"{npm} start"
    raise StartError("electron 을 찾을 수 없어요. 클라 폴더에서 `npm install` 을 먼저 실행하세요")
