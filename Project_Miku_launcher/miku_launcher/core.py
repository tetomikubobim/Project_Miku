"""런처의 두뇌 (Qt 와 무관): 서버/클라 프로세스, 상태 확인, 로그, 리소스, 서버 설정(config.yaml)을 한데 묶는다.
UI 는 snapshot() 을 주기적으로 읽고, 버튼은 start_*/stop_*/restart_server 를 부른다 (모두 백그라운드 스레드에서 돈다)."""
import json
import os
import threading
import time
from urllib.parse import urlparse

import requests

from .installer import Env as InstallEnv, InstallManager
from .jobobject import JobObject
from . import presets
from .logs import FileTail, LogHub
from .poller import HealthPoller
from .procs import CRASHED, STOPPING, ManagedProcess, StartError, find_electron, python_for_server
from .resources import ResourceMonitor
from .server_config import ServerConfig
from .settings_store import LauncherSettings
from .updater import UpdateService

SERVER_START_GRACE = 150.0     # 서버가 응답하기까지 기다려 주는 시간 (모델 로딩 포함)
CLIENT_STOP_WAIT = 2.5         # 클라(Electron)는 정상 종료 요청(taskkill)에 거의 응답하지 않아서, 오래 기다려도 결국 강제 종료가 된다
CLIENT_CONNECT_GRACE = 25.0    # 클라를 켠 뒤 서버에 접속하기까지 기다려 주는 시간
LOCAL_NAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


class LauncherCore:
    def __init__(self, base_dir, settings=None):
        self.base_dir = os.path.abspath(base_dir)
        self.settings = settings or LauncherSettings.load(self.base_dir)
        self.hub = LogHub(os.path.join(self.base_dir, "logs"))
        self.job = JobObject(log=lambda m: self.hub.add("launcher", m))
        self.config = ServerConfig(self.settings.server_dir, lambda: self.settings.client_dir)
        self.presets = presets.PresetStore(os.path.join(self.base_dir, "presets"))   # 설정 프리셋 (런처 폴더의 presets/)
        self.server = ManagedProcess("server", self._build_server, self._sink, self.job, graceful=self._graceful_server, on_exit=self._on_exit)
        self.client = ManagedProcess("client", self._build_client, self._sink, self.job, on_exit=self._on_exit)
        self.poller = HealthPoller(self.base_url, float(self.settings["poll_seconds"] or 2.0))
        self.monitor = ResourceMonitor(self._pids, float(self.settings["poll_seconds"] or 2.0))
        self.tails = [FileTail(self.hub, "sovits", lambda: self.config.log_files()["sovits"] if self.config.dir else ""),
                      FileTail(self.hub, "rvc", lambda: self.config.log_files()["rvc"] if self.config.dir else "")]
        self.installer = InstallManager(InstallEnv(self.base_dir, self.settings, server_config=lambda: self.config,
                                                   set_config=lambda path, v: self.save_server_config({path: v})), busy_check=self._install_blocker)
        self.updates = UpdateService(self)   # GitHub 릴리스 업데이트 (확인/설치/되돌리기)
        self._busy = {"server": False, "client": False}
        self._closing = False
        self._client_json = (None, None)   # (mtime, serverUrl)

    # ---------- 기본 ----------
    def _sink(self, name, line):
        self.hub.add(name, line)

    def log(self, text):
        self.hub.add("launcher", text)

    def base_url(self):
        host, port = self.config.host_port()
        return f"http://{host}:{port}"

    def _pids(self):
        spid = self.server.pid if self.server.alive else ((self.poller.last or {}).get("pid"))
        return {"server": spid, "client": self.client.pid if self.client.alive else None}

    def reconfigure_paths(self):
        """런처 설정에서 폴더를 바꾼 뒤 부른다 (이미 켠 프로세스는 그대로)."""
        self.config = ServerConfig(self.settings.server_dir, lambda: self.settings.client_dir)
        self.poller.reset()
        self.installer.refresh()

    def _install_blocker(self, module, removing=False):
        """설치/삭제하려는 모듈이 쓰는 프로그램이 켜져 있으면 (파일이 잠겨서 실패하므로) 그 이유를 돌려준다."""
        touches = (module.remove_touches if removing and module.remove_touches is not None else module.touches)
        verb = "지우려면" if removing else "설치하려면"
        if touches == "server" and (self.server.alive or self.poller.last):
            return f"'{module.title}' 을(를) {verb} 서버를 먼저 끄세요 (파일이 사용 중이라 실패해요)"
        if touches == "client" and self.client.alive:
            return f"'{module.title}' 을(를) {verb} 클라이언트를 먼저 끄세요"
        return ""

    # ---------- 시작/종료 ----------
    def start(self):
        self.poller.start()
        self.monitor.start()
        for t in self.tails:
            t.start()
        if self.config.legacy_local_exists():
            self.log("예전 설정 파일(config.local.json)이 있어요. '서버 설정' 탭의 [설정 병합]으로 config.yaml 에 합치세요 (지금은 config.yaml 값만 적용돼요).")
        self.updates.start_background()
        self.run_async("autostart", self._autostart)

    def _autostart(self):
        self.updates.startup()   # 새 버전 확인 (자동 설치를 켰다면 서버/클라를 켜기 전에 설치까지)
        self.poller.check()
        if self.settings["auto_start_server"]:
            if self.poller.last:
                self.log("서버가 이미 실행 중이에요. 그대로 써요 (런처가 켠 게 아니라서 런처를 꺼도 서버는 그대로 남아요).")
            else:
                self.start_server()
        if self.settings["auto_start_client"] and not self._closing:
            if self.server.alive and not self.poller.last:   # 서버가 응답할 때까지 기다렸다가 켠다 (연결 실패 말풍선을 피한다)
                deadline = time.monotonic() + SERVER_START_GRACE
                while not self._closing and not self.poller.last and self.server.alive and time.monotonic() < deadline:
                    time.sleep(0.5)
            if not self._closing and not self.client.alive:
                self.start_client()

    def shutdown(self):
        """런처를 끌 때: 런처가 켠 클라와 서버를 끈다 (런처 밖에서 켠 서버는 그대로 둔다).
        어디서 멈추는지 알 수 있게 단계마다 로그를 남긴다."""
        self._closing = True
        t0 = time.monotonic()
        self.log("런처 종료: 정리를 시작해요")
        threads = []
        for mp, wait in ((self.client, CLIENT_STOP_WAIT), (self.server, 25.0)):
            if mp.alive:
                def work(mp=mp, wait=wait):
                    mp.stop(wait=wait)
                    self.log(f"런처 종료: {mp.name} 정리 끝 ({time.monotonic() - t0:.1f}초)")
                t = threading.Thread(target=work, name=f"stop-{mp.name}", daemon=True)
                t.start()
                threads.append((mp.name, t))
        for name, t in threads:
            t.join(40)
            if t.is_alive():
                self.log(f"런처 종료: {name} 정리가 40초 안에 끝나지 않았어요. 그대로 넘어가요")
        self.installer.cancel()
        self.updates.stop()
        self.poller.stop()
        self.monitor.stop()
        for t in self.tails:
            t.stop()
        self.log(f"런처 종료: 정리 끝 ({time.monotonic() - t0:.1f}초)")
        self.hub.close()

    # ---------- 서버 ----------
    def _build_server(self):
        sd = self.settings.server_dir
        if not sd or not os.path.isdir(sd):
            raise StartError("서버 폴더를 찾을 수 없어요. '런처' 탭에서 서버 폴더를 지정하세요")
        if not os.path.isfile(os.path.join(sd, "server.py")):
            raise StartError(f"서버 폴더에 server.py 가 없어요: {sd}")
        if not self.config.exists():
            raise StartError("서버 폴더에 config.yaml 이 없어요 (config.example.yaml 을 복사해서 만드세요)")
        py, why = python_for_server(str(self.settings["server_python"] or "").strip(), sd)
        self.log(f"서버 실행: {py} server.py  ({why})")
        return [py, "-u", "server.py"], sd, {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "MIKU_LAUNCHER": "1"}

    def _graceful_server(self, mp):
        try:
            r = requests.post(self.base_url() + "/admin/shutdown", timeout=3)
            return r.status_code == 200
        except requests.RequestException:
            return False   # 아직 안 떴으면 그냥 종료 요청(프로세스 종료)으로 넘어간다

    def start_server(self):
        if self.poller.last:
            raise StartError("이미 서버가 실행 중이에요 (런처 밖에서 켠 서버). 그 서버를 끈 뒤에 켜세요")
        self.poller.reset()
        pid = self.server.start()
        self.log(f"서버를 켰어요 (pid {pid})")
        self.poller.poll_now()

    def stop_server(self):
        if self.server.alive:
            self.log("서버를 끄는 중…")
            self.server.stop(wait=25.0)
        elif self.poller.last:   # 런처 밖에서 켠 서버: 종료 요청만 보낸다
            self.log("런처 밖에서 켠 서버에 종료 요청을 보내요")
            if not self._graceful_server(None):
                raise StartError("서버가 종료 요청을 받지 않아요 (예전 버전이면 직접 끄세요)")
        else:
            return
        self.poller.reset()
        self.poller.poll_now()

    def restart_server(self):
        self.stop_server()
        # 포트가 풀릴 때까지 잠깐 기다린다
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.poller.check():
            time.sleep(0.3)
        self.start_server()

    # ---------- 클라 ----------
    def _build_client(self):
        cd = self.settings.client_dir
        if not cd or not os.path.isdir(cd):
            raise StartError("클라 폴더를 찾을 수 없어요. '런처' 탭에서 클라 폴더를 지정하세요")
        argv, desc = find_electron(str(self.settings["electron"] or "").strip(), cd)
        self.log(f"클라 실행: {desc}")
        return argv, cd, {}

    def start_client(self):
        pid = self.client.start()
        self.log(f"클라를 켰어요 (pid {pid})")

    def stop_client(self):
        if self.client.alive:
            self.log("클라를 끄는 중…")
            self.client.stop(wait=CLIENT_STOP_WAIT)

    def _on_exit(self, mp):
        if self._closing:
            return
        label = {"server": "서버", "client": "클라"}[mp.name]
        if mp.state == CRASHED:
            self.log(f"{label}가 비정상 종료됐어요 (코드 {mp.exit_code}). 위 로그를 확인하세요")
        else:
            self.log(f"{label}가 종료됐어요 (코드 {mp.exit_code})")
        if mp.name == "server":
            self.poller.reset()

    # ---------- 백그라운드 실행 (UI 가 멈추지 않게) ----------
    def run_async(self, kind, fn):
        """kind: 'server' | 'client' | 그 밖. server/client 는 같은 종류가 동시에 두 번 돌지 않는다. 시작했으면 True."""
        if kind in self._busy:
            if self._busy[kind]:
                return False
            self._busy[kind] = True

        def work():
            try:
                fn()
            except StartError as e:
                self.log(str(e))
            except Exception as e:  # noqa: BLE001
                self.log(f"{kind} 작업 중 오류: {e!r}")
            finally:
                if kind in self._busy:
                    self._busy[kind] = False

        threading.Thread(target=work, name=f"act-{kind}", daemon=True).start()
        return True

    # ---------- 전체 기본값 초기화 (런처 + 서버 + 클라) ----------
    SECRET_PATHS = ("llm.gemini.api_key", "llm.openai.api_key", "llm.groq.api_key", "llm.ollama.api_key")

    def reset_all_to_defaults(self):
        """런처 / 서버(config.yaml) / 클라(config.json) 설정을 배포 때의 기본값으로 되돌린다.
        - 서버: 서버 폴더의 config.example.yaml 을 config.yaml 로 복사 (직전 파일은 config.yaml.bak). API 키는 지우지 않고 그대로 둔다.
        - 클라: 클라 폴더의 config.default.json 을 config.json 으로 복사 (직전 파일은 config.json.bak). 클라가 켜져 있으면 껐다가 다시 켠다.
        - 런처: launcher.json 을 기본값으로 (폴더 경로는 비워서 자동 탐색으로 돌아간다).
        결과를 로그에 한 줄씩 남기고, 한 군데가 실패해도 나머지는 계속한다."""
        import shutil
        self.log("기본값 초기화를 시작해요 (런처 / 서버 / 클라)")
        # --- 서버 ---
        sd = self.settings.server_dir
        example = os.path.join(sd, "config.example.yaml") if sd else ""
        if not example or not os.path.isfile(example):
            self.log("  서버: config.example.yaml 이 없어서 건너뛰어요")
        else:
            self._auto_backup_preset()   # 되돌리기 전에 지금 설정을 프리셋으로 남겨 둔다 (나중에 불러와서 되살릴 수 있게)
            try:
                cur = self.config.cfg_or_empty()
                keep = {}
                for p in self.SECRET_PATHS:
                    node = cur
                    for k in p.split("."):
                        node = node.get(k) if isinstance(node, dict) else None
                    if node:
                        keep[p] = node
                target = os.path.join(sd, "config.yaml")
                if os.path.isfile(target):
                    shutil.copy2(target, target + ".bak")
                shutil.copy2(example, target)
                self.config._cache = None
                self.config.loaded_mtime = None   # 파일을 통째로 바꿨으니 '설정 화면이 읽은 시점' 충돌 검사를 건너뛴다
                changes = dict(keep)
                rvc_root = os.path.join(self.base_dir, "tools", "rvc")
                rvc_py = os.path.join(rvc_root, "venv", "Scripts", "python.exe")
                if os.path.isfile(rvc_py):   # 이 PC 에 설치된 RVC 는 계속 쓰도록 경로를 다시 적어 둔다
                    changes["tts.rvc.launch.python"] = rvc_py
                    changes["tts.rvc.launch.dir"] = rvc_root
                if changes:
                    self.save_server_config(changes)
                elif self.poller.last:
                    self.reload_server_config()
                self.log("  서버: config.yaml 을 기본값으로 되돌렸어요" + (f" (API 키 {len(keep)}개는 유지)" if keep else "") + " — 직전 파일은 config.yaml.bak")
            except Exception as e:  # noqa: BLE001
                self.log(f"  서버: 초기화 실패: {e!r}")
        # --- 클라 ---
        cd = self.settings.client_dir
        default = os.path.join(cd, "config.default.json") if cd else ""
        if not default or not os.path.isfile(default):
            self.log("  클라: config.default.json 이 없어서 건너뛰어요")
        else:
            was_running = self.client.alive
            try:
                if was_running:
                    self.log("  클라: 설정을 바꾸려고 잠깐 껐다가 다시 켤게요")
                    self.stop_client()
                target = os.path.join(cd, "config.json")
                if os.path.isfile(target):
                    shutil.copy2(target, target + ".bak")
                shutil.copy2(default, target)
                self.log("  클라: config.json 을 기본값으로 되돌렸어요 — 직전 파일은 config.json.bak")
            except Exception as e:  # noqa: BLE001
                self.log(f"  클라: 초기화 실패: {e!r}")
            finally:
                if was_running and not self._closing:
                    try:
                        self.start_client()
                    except Exception as e:  # noqa: BLE001
                        self.log(f"  클라: 다시 켜지 못했어요: {e}")
        # --- 런처 ---
        try:
            self.settings.reset()
            self.settings.save()
            self.reconfigure_paths()
            self.log("  런처: launcher.json 을 기본값으로 되돌렸어요")
        except Exception as e:  # noqa: BLE001
            self.log(f"  런처: 초기화 실패: {e!r}")
        self.log("기본값 초기화가 끝났어요")

    # ---------- 서버 설정 저장 + 바로 반영 ----------
    def save_server_config(self, changes, raw=None):
        """config.yaml 에 저장하고, 서버가 켜져 있으면 바로 다시 읽게 한다. raw 는 프리셋 적용용 (설정 화면에 없는 값).
        돌려주는 값: {'changed', 'restart', 'reloaded', 'reload_error'}. 검사/저장 오류는 예외로 올린다 (ValueError, PatchError, ConfigUnavailable)."""
        res = self.config.save(changes, raw=raw)
        res.update(reloaded=False, reload_error="")
        if res.get("server_changed", bool(res["changed"])) and self.poller.last:   # 클라이언트 항목만 바뀌었으면 서버를 다시 읽게 할 필요가 없다
            ok, err = self.reload_server_config()
            res.update(reloaded=ok, reload_error=err)
        return res

    def reload_server_config(self):
        try:
            r = requests.post(self.base_url() + "/admin/reload", timeout=5)
        except requests.RequestException as e:
            return False, f"서버에 닿지 않았어요: {e}"
        if r.status_code == 200:
            self.poller.poll_now()
            return True, ""
        try:
            return False, str(r.json().get("error") or r.text[:200])
        except ValueError:
            return False, f"HTTP {r.status_code}"

    # ---------- config.yaml 처음 만들기 (덮어쓰기 금지) ----------
    def create_server_config(self):
        """config.example.yaml 로 config.yaml 을 만든다. 이미 있으면 FileExistsError (절대 덮어쓰지 않는다)."""
        self.config.create_from_example()
        self.log("config.example.yaml 로 config.yaml 을 만들었어요")
        return self.config.path

    # ---------- 설정 프리셋 ----------
    def save_preset(self, name, include_secrets=False, overwrite=False):
        """지금 config.yaml (과 클라이언트 항목) 의 값을 프리셋으로 저장한다. API 키는 include_secrets=True 일 때만 넣는다."""
        values = presets.snapshot(self.config, include_secrets=include_secrets)
        path = self.presets.save(name, values, secrets_included=include_secrets, overwrite=overwrite)
        self.log(f"프리셋 '{presets.safe_name(name)}' 을 저장했어요 ({len(values)}개 항목{', API 키 포함' if include_secrets else ''})")
        return path

    def plan_preset(self, name):
        """프리셋을 지금 설정 형식에 맞춰 보고 적용 계획(presets.Plan)을 돌려준다. 파일은 건드리지 않는다."""
        return presets.plan(self.config, self.presets.load(name)["values"])

    def apply_preset(self, plan):
        """plan_preset 의 결과를 config.yaml 에 적용한다 (주석/순서 그대로, 직전 내용은 config.yaml.bak). save_server_config 와 같은 결과를 돌려준다."""
        res = self.save_server_config(plan.fields, raw=plan.raw) if plan.count else {
            "changed": [], "restart": [], "client_restart": [], "reloaded": False, "reload_error": "", "server_changed": False}
        self.log(f"프리셋을 적용했어요 (적용 {len(res['changed'])}개, 건너뜀 {len(plan.skipped)}개)")
        return res

    def _auto_backup_preset(self):
        """기본값 초기화 같은 큰 변경 전에 지금 설정을 '자동백업-날짜' 프리셋으로 남긴다 (API 키는 넣지 않고, 최근 5개만 유지). 실패해도 본 작업은 계속한다."""
        try:
            name = time.strftime(presets.AUTO_PREFIX + "%Y%m%d-%H%M%S")
            values = presets.snapshot(self.config, include_secrets=False)
            self.presets.save(name, values, secrets_included=False)
            self.presets.prune(presets.AUTO_PREFIX, keep=5)
            self.log(f"  서버: 지금 설정을 프리셋 '{name}' 으로 남겨 뒀어요 (설정 탭에서 불러올 수 있어요)")
        except Exception as e:  # noqa: BLE001
            self.log(f"  서버: 자동 백업 프리셋을 만들지 못했어요 (계속 진행): {e}")

    def migrate_local_config(self):
        res = self.config.migrate()
        self.log(f"config.local.json 의 값 {len(res)}개를 config.yaml 에 합쳤어요 (원본은 .migrated.bak 으로 보관)")
        if res and self.poller.last:
            self.reload_server_config()
        return res

    # ---------- 화면용 상태 ----------
    def _client_server_url(self):
        cd = self.settings.client_dir
        p = os.path.join(cd, "config.json") if cd else ""
        try:
            m = os.stat(p).st_mtime_ns
            if self._client_json[0] != m:
                with open(p, encoding="utf-8-sig") as f:
                    self._client_json = (m, str(json.load(f).get("serverUrl") or ""))
        except (OSError, ValueError):
            self._client_json = (None, "")
        return self._client_json[1]

    def warnings(self):
        out = []
        url = self._client_server_url()
        if url:
            if "://" not in url:
                url = "ws://" + url
            try:
                u = urlparse(url)
                chost, cport = (u.hostname or "").lower(), u.port or (443 if u.scheme in ("wss", "https") else 80)
            except ValueError:
                chost, cport = "", None
            shost, sport = self.config.host_port()
            if chost not in LOCAL_NAMES:
                out.append(f"클라가 연결하려는 주소({url})가 이 컴퓨터가 아니에요. 서버를 이 컴퓨터에서 쓴다면 클라 설정창의 서버 주소를 고치세요")
            elif cport != sport:
                out.append(f"클라의 서버 주소({url})와 서버 포트({sport})가 달라요. 클라 설정창의 서버 주소를 맞추세요")
        if not self.settings.server_dir:
            out.append("서버 폴더를 찾지 못했어요. '런처' 탭에서 지정하세요")
        if not self.settings.client_dir:
            out.append("클라 폴더를 찾지 못했어요. '런처' 탭에서 지정하세요")
        if self.config.dir and not self.config.exists():
            out.append("서버 폴더에 config.yaml 이 없어요")
        if self.config.legacy_local_exists():
            out.append("예전 설정 파일(config.local.json)이 남아 있어요: '서버 설정' 탭에서 [설정 병합]을 실행하세요")
        w = self.updates.warning()
        if w:
            out.append(w)
        return out

    def _server_light(self, h):
        s = self.server
        if s.state == STOPPING:
            return "stopping", "종료하는 중…"
        if h:
            s.mark_running()
            return "ok", ("정상" if s.alive else "정상 (런처 밖에서 실행 중)")
        if s.alive:
            age = time.time() - (s.started_at or time.time())
            if self.poller.ever_ok and self.poller.fail_count >= 3:
                return "error", "응답이 없어요 (로그를 확인하세요)"
            if age > SERVER_START_GRACE:
                return "error", f"{int(age)}초째 응답이 없어요 (로그를 확인하세요)"
            return "starting", "켜는 중…"
        if s.state == CRASHED:
            return "down", f"비정상 종료 (코드 {s.exit_code})"
        return "stopped", "꺼져 있음"

    def _client_light(self, h):
        c = self.client
        if c.state == STOPPING:
            return "stopping", "종료하는 중…"
        if c.alive:
            c.mark_running()
            if h and not h.get("clients") and time.time() - (c.started_at or 0) > CLIENT_CONNECT_GRACE:
                return "warn", "실행 중 · 서버에 접속하지 못했어요"
            if h and h.get("clients"):
                return "ok", "실행 중 · 서버에 접속됨"
            return "ok", "실행 중"
        if c.state == CRASHED:
            return "down", f"비정상 종료 (코드 {c.exit_code})"
        return "stopped", "꺼져 있음"

    @staticmethod
    def _service_light(h, key):
        if not h:
            return "unknown", "서버가 꺼져 있어요"
        s = h.get(key) or {}
        state = s.get("state", "unknown")
        kind = {"ok": "ok", "loading": "loading", "idle": "idle", "off": "off", "down": "down", "error": "down"}.get(state, "unknown")
        if key == "stt":
            text = " ".join(x for x in (s.get("model"), s.get("detail")) if x)
        else:
            text = " · ".join(x for x in ({"gptsovits": "GPT-SoVITS", "edge": "Edge TTS"}.get(s.get("provider"), s.get("provider")), s.get("detail")) if x)
        return kind, text or {"ok": "정상", "off": "꺼짐"}.get(kind, "")

    def snapshot(self):
        h = self.poller.last
        sk, st = self._server_light(h)
        ck, ct = self._client_light(h)
        tk, tt = self._service_light(h, "tts")
        stk, stt = self._service_light(h, "stt")
        llm = (h or {}).get("llm") or {}
        return {
            "server": {"state": sk, "text": st, "pid": self.server.pid if self.server.alive else (h or {}).get("pid"), "managed": self.server.alive,
                       "busy": self._busy["server"], "uptime": (h or {}).get("uptime")},
            "client": {"state": ck, "text": ct, "pid": self.client.pid if self.client.alive else None, "busy": self._busy["client"]},
            "stt": {"state": stk, "text": stt},
            "tts": {"state": tk, "text": tt},
            "llm": {"provider": llm.get("provider", ""), "model": llm.get("model", "")},
            "clients": (h or {}).get("clients"),
            "resources": self.monitor.snapshot(),
            "warnings": self.warnings(),
            "url": self.base_url(),
        }
