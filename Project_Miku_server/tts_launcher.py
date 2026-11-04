"""GPT-SoVITS(api_v2) 자동 실행. 서버를 켤 때 같이 켜고, 서버가 꺼질 때 같이 끈다.

켜지는 조건 (모두 만족해야 함):
  - tts.enabled 가 true 이고
  - tts.provider 가 gptsovits 이고
  - tts.launch.enabled 가 true (생략하면 true)
이미 tts.url 에서 응답하는 서버가 있으면 새로 켜지 않고 그대로 쓴다 (그 경우 끌 때도 건드리지 않는다).

config.yaml 예:
  tts:
    provider: gptsovits
    url: "http://127.0.0.1:9880"
    launch:
      enabled: true
      dir: ""   # GPT-SoVITS 폴더 (api_v2.py 가 있는 곳)
      # command 를 생략하면 아래 기본값을 쓴다 (url 의 호스트/포트가 -a/-p 로 들어감)
      # command: ["runtime/python.exe", "api_v2.py", "-a", "127.0.0.1", "-p", "9880", "-c", "GPT_SoVITS/configs/tts_infer.yaml"]
      wait_seconds: 180      # 모델 로딩을 기다리는 최대 시간 (기다리는 동안에도 서버는 바로 쓸 수 있고, 그동안은 글자만 나감)
      log_file: gptsovits.log
"""
import os
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

import requests

import killjob

DEFAULT_CONFIG_REL = "GPT_SoVITS/configs/tts_infer.yaml"


def should_launch(tts_cfg):
    """provider 가 gptsovits 일 때만 True."""
    tts_cfg = tts_cfg or {}
    if not tts_cfg.get("enabled", True):
        return False
    provider = str(tts_cfg.get("provider") or "gptsovits").strip().lower()
    if provider != "gptsovits":
        return False
    return (tts_cfg.get("launch") or {}).get("enabled", True) is not False


def is_up(url, timeout=1.5):
    """아무 HTTP 응답이든 오면 켜져 있는 것으로 본다 (api_v2 는 / 에 404 를 돌려줌). 연결이 안 되면 False."""
    try:
        requests.get(url, timeout=timeout)
        return True
    except requests.RequestException:
        return False


def host_port(url):
    u = urlparse(url or "http://127.0.0.1:9880")
    return u.hostname or "127.0.0.1", u.port or 9880


def _python_in(directory):
    """GPT-SoVITS 통합 패키지는 runtime 폴더에 자기 파이썬이 있다. 없으면 서버가 쓰는 파이썬."""
    for rel in ("runtime/python.exe", "runtime/python", "venv/Scripts/python.exe", "venv/bin/python"):
        p = os.path.join(directory, rel)
        if os.path.isfile(p):
            return p
    return sys.executable


def build_command(tts_cfg):
    """실행할 명령(리스트)과 작업 폴더를 돌려준다. 설정이 잘못됐으면 ValueError (메시지는 사용자에게 보여 줌)."""
    launch = tts_cfg.get("launch") or {}
    directory = launch.get("dir") or ""
    if not directory:
        raise ValueError("tts.launch.dir 가 비어 있어요 (GPT-SoVITS 폴더 경로를 적어 주세요)")
    directory = os.path.normpath(directory)
    if not os.path.isdir(directory):
        raise ValueError(f"tts.launch.dir 폴더가 없어요: {directory}")

    cmd = launch.get("command")
    if cmd:
        if isinstance(cmd, str):
            raise ValueError("tts.launch.command 는 문자열이 아니라 목록이어야 해요 (예: [\"runtime/python.exe\", \"api_v2.py\"])")
        cmd = [str(c) for c in cmd]
    else:
        host, port = host_port(tts_cfg.get("url"))
        if not os.path.isfile(os.path.join(directory, "api_v2.py")):
            raise ValueError(f"api_v2.py 를 찾을 수 없어요: {directory}")
        cmd = [_python_in(directory), "api_v2.py", "-a", host, "-p", str(port), "-c", DEFAULT_CONFIG_REL]

    # 첫 항목이 작업 폴더 기준 상대 경로이고 실제로 있으면 절대 경로로 바꾼다
    first = os.path.join(directory, cmd[0])
    if not os.path.isabs(cmd[0]) and os.path.isfile(first):
        cmd[0] = first
    return cmd, directory


def _kill_tree(proc):
    """자식 프로세스까지 같이 끈다 (api_v2 가 하위 프로세스를 만들 수 있다)."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


class Spec:
    """무엇을 어떻게 켤지. 기본은 GPT-SoVITS 이고, rvc.py 가 RVC 용을 만든다."""

    def __init__(self, label, name, should, url, launch, build, default_wait=180.0, default_log="gptsovits.log"):
        self.label = label                # 콘솔 앞머리 [sovits]
        self.name = name                  # 사람이 읽는 이름
        self.should = should              # (tts_cfg) -> bool
        self.url = url                    # (tts_cfg) -> 준비됐는지 확인할 주소
        self.launch = launch              # (tts_cfg) -> launch 설정 dict
        self.build = build                # (tts_cfg) -> (명령 리스트, 작업 폴더). 설정이 잘못되면 ValueError
        self.default_wait = default_wait
        self.default_log = default_log


SOVITS_SPEC = Spec(
    "sovits", "GPT-SoVITS", should_launch,
    lambda c: c.get("url") or "http://127.0.0.1:9880",
    lambda c: c.get("launch") or {},
    build_command,
)


class Launcher:
    """start() 는 바로 돌아온다 (모델 로딩은 백그라운드에서 기다리며 로그만 남긴다). stop() 은 우리가 켠 것만 끈다.
    on_ready: 서비스가 응답하기 시작하면(이미 켜져 있던 경우 포함) 한 번 부르는 함수 (백그라운드 스레드에서 실행)."""

    def __init__(self, tts_cfg, base_dir=".", popen=subprocess.Popen, is_up_fn=is_up, sleep=time.sleep, spec=None, on_ready=None):
        self.cfg = tts_cfg or {}
        self.base_dir = base_dir
        self.popen = popen
        self.is_up = is_up_fn
        self.sleep = sleep
        self.spec = spec or SOVITS_SPEC
        self.on_ready = on_ready
        self.proc = None
        self.log = None
        self.ready = threading.Event()
        self._stop = threading.Event()

    @property
    def owned(self):
        return self.proc is not None

    def _tag(self):
        return f"[{self.spec.label}]"

    def _fire_ready(self):
        self.ready.set()
        if self.on_ready:
            try:
                self.on_ready()
            except Exception as e:  # noqa: BLE001  준비 후 설정이 실패해도 서버는 계속
                print(f"{self._tag()} 준비 후 설정에 실패했어요: {e!r}")

    def start(self):
        """켰으면 True, 켜지 않았거나(조건 불충족/이미 켜져 있음) 실패했으면 False. 실패해도 예외는 내지 않는다 (대화는 글자로 계속)."""
        sp = self.spec
        if not sp.should(self.cfg):
            return False
        url = sp.url(self.cfg)
        if self.is_up(url):
            print(f"{self._tag()} 이미 켜져 있어서 그대로 써요: {url}")
            threading.Thread(target=self._fire_ready, daemon=True).start()
            return False
        try:
            cmd, cwd = sp.build(self.cfg)
        except ValueError as e:
            print(f"{self._tag()} 자동 실행을 건너뛰어요: {e}")
            return False

        launch = sp.launch(self.cfg)
        log_path = os.path.join(self.base_dir, launch.get("log_file") or sp.default_log)
        kwargs = {"cwd": cwd, "stdin": subprocess.DEVNULL}
        try:
            self.log = open(log_path, "ab")
            kwargs.update(stdout=self.log, stderr=subprocess.STDOUT)
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            self.proc = self.popen(cmd, env=env, **kwargs)
            killjob.attach(self.proc.pid)  # 서버가 강제로 죽어도 OS 가 이 프로세스(와 하위 프로세스)를 같이 끈다
        except OSError as e:
            print(f"{self._tag()} 실행하지 못했어요: {e}")
            self._close_log()
            self.proc = None
            return False

        print(f"{self._tag()} {sp.name} 를 켰어요 (pid {self.proc.pid}). 준비되는 동안(모델 불러오기)은 기본 동작으로 대답해요. 로그: {log_path}")
        threading.Thread(target=self._wait_ready, args=(url, float(launch.get("wait_seconds", sp.default_wait))), daemon=True).start()
        return True

    def _wait_ready(self, url, wait_seconds):
        deadline = time.monotonic() + wait_seconds
        while not self._stop.is_set() and time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                print(f"{self._tag()} {self.spec.name} 가 바로 종료됐어요 (코드 {self.proc.returncode}). 로그를 확인하세요.")
                return
            if self.is_up(url):
                print(f"{self._tag()} {self.spec.name} 준비 완료")
                self._fire_ready()
                return
            self.sleep(1.0)
        if not self._stop.is_set():
            print(f"{self._tag()} {wait_seconds:.0f}초 안에 응답이 없어요. 로그를 확인하세요 (서버는 계속 동작해요).")

    def _close_log(self):
        if self.log:
            try:
                self.log.close()
            except OSError:
                pass
            self.log = None

    def stop(self):
        self._stop.set()
        if self.proc is not None:
            print(f"{self._tag()} {self.spec.name} 를 끄는 중…")
            _kill_tree(self.proc)
            self.proc = None
        self._close_log()
