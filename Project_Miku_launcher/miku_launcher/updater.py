"""GitHub 릴리스 기반 업데이트 (Qt 와 무관).

흐름: 최신 릴리스 확인 → 소스 zip 내려받기 → 임시 폴더에 풀기 → 바뀔 파일 계산 → 백업하고 덮어쓰기 → VERSION 기록.
- 받는 것은 릴리스 태그 시점의 소스 zip(zipball)이다. 릴리스에 `MikuLauncher*.zip` 이 같이 올라와 있으면 exe 런처도 갱신한다.
- 사용자 파일(config.yaml, config.json, 대화 기록, DB, 모델, persona.txt, venv, node_modules, 프리셋, 로그 ...)은 절대 덮어쓰지 않는다.
- 덮어쓰는 파일은 먼저 updates/backups/ 에 복사해 둔다. 하나라도 실패하면 이미 바꾼 파일을 되돌린다.
- 저장소에서 지워진 파일은 지우지 않는다 (남아 있어도 해롭지 않고, 사용자 파일을 잘못 지우는 사고를 막는다).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile

import requests

DEFAULT_REPO = "tetomikubobim/Project_Miku"   # 기본 업데이트 저장소 (런처 탭에서 바꿀 수 있고, 비우면 이 값을 쓴다)
API = "https://api.github.com/repos/{repo}/releases/latest"
UA = {"User-Agent": "ProjectMiku-Launcher", "Accept": "application/vnd.github+json"}
CHECK_TIMEOUT = 10
FROZEN = bool(getattr(sys, "frozen", False))

SERVER_NAMES = ("Project_Miku_server", "Project_Miku_Server", "miku_server", "server")
CLIENT_NAMES = ("Project_Miku_client", "Project_Miku", "miku_client", "client")
LAUNCHER_NAMES = ("Project_Miku_launcher", "miku_launcher_app", "launcher")
ROOT_FILES = ("README.md", "LICENSE.md", "THIRD_PARTY_NOTICES.md", "VERSION")
LAUNCHER_ASSET_RE = re.compile(r"^MikuLauncher.*\.zip$", re.I)
KEEP_BACKUPS = 3
TEXT_EXT = {".py", ".pyw", ".js", ".json", ".md", ".txt", ".yaml", ".yml", ".html", ".css", ".bat", ".spec", ".mjs", ".cjs", ".example", ""}

# 어디에 있든 건드리지 않는 폴더/파일
SKIP_DIRS = {"venv", ".venv", "__pycache__", "node_modules", "logs", ".git", ".github"}
SKIP_SUFFIX = (".pyc", ".bak", ".log", ".tmp", ".miku-new")
# 종류별로 (폴더 맨 위 기준) 건드리지 않는 것. 폴더는 끝에 '/'
PROTECTED = {
    "server": ("config.yaml", "config.local.json", "config.local.json.migrated.bak", "persona.txt", "chat_log/", "uploads/", "audio/",
               "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3"),
    "client": ("config.json", "schedules.json", "lyrics-cache/", "models/", "voice-models/"),
    "launcher": ("launcher.json", "presets/", "updates/", "tools/", "logs/"),
    "root": (),
}
# 런처 exe 를 바꿀 때 robocopy 가 건드리지 않는 것
SWAP_EXCLUDE_FILES = ("launcher.json",)
SWAP_EXCLUDE_DIRS = ("presets", "logs", "updates", "venv", "tools")


class UpdateError(Exception):
    pass


# ---------- 버전 / 저장소 ----------
def parse_version(text):
    """'v1.2.3', '1.2.3-beta' -> (1, 2, 3). 숫자가 없으면 None."""
    m = re.search(r"(\d+(?:\.\d+)*)", str(text or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current) or (0,)
    if a is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def parse_repo(text):
    """'owner/name' 또는 'https://github.com/owner/name(.git)' -> 'owner/name'. 못 알아보면 ''."""
    t = str(text or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?$", t, re.I)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return t if re.match(r"^[\w.-]+/[\w.-]+$", t) else ""


# ---------- 릴리스 확인 ----------
class Release:
    def __init__(self, data):
        self.tag = str(data.get("tag_name") or "")
        self.version = ".".join(str(x) for x in (parse_version(self.tag) or ())) or self.tag
        self.name = str(data.get("name") or self.tag)
        self.body = str(data.get("body") or "").strip()
        self.url = str(data.get("html_url") or "")
        self.zipball = str(data.get("zipball_url") or "")
        self.launcher_asset = None
        for a in data.get("assets") or []:
            if LAUNCHER_ASSET_RE.match(str(a.get("name") or "")) and a.get("browser_download_url"):
                self.launcher_asset = {"name": a["name"], "url": a["browser_download_url"], "size": int(a.get("size") or 0)}
                break


class UpdateInfo:
    def __init__(self, repo, current, release=None, error=""):
        self.repo, self.current, self.release, self.error = repo, current, release, error
        self.checked_at = time.time()

    @property
    def available(self):
        return bool(self.release and not self.error and is_newer(self.release.version, self.current))


def fetch_latest(repo, current, get=None):
    """GitHub 의 최신 릴리스(정식, 초안/사전배포 제외)를 읽는다. 실패는 UpdateInfo.error 로 돌려준다 (예외로 올리지 않는다)."""
    get = get or requests.get
    if not repo:
        return UpdateInfo(repo, current, error="GitHub 저장소(owner/name)가 비어 있어요")
    try:
        r = get(API.format(repo=repo), headers=UA, timeout=CHECK_TIMEOUT)
    except requests.RequestException as e:
        return UpdateInfo(repo, current, error=f"GitHub 에 닿지 않았어요: {e.__class__.__name__}")
    if r.status_code == 404:
        return UpdateInfo(repo, current, error="릴리스를 찾지 못했어요 (저장소 이름이 맞는지, 릴리스를 하나 이상 올렸는지 확인하세요)")
    if r.status_code == 403:
        return UpdateInfo(repo, current, error="GitHub 요청 한도에 걸렸어요. 잠시 뒤에 다시 확인하세요")
    if r.status_code != 200:
        return UpdateInfo(repo, current, error=f"GitHub 가 HTTP {r.status_code} 로 답했어요")
    try:
        data = r.json()
    except ValueError:
        return UpdateInfo(repo, current, error="GitHub 의 응답을 읽지 못했어요")
    rel = Release(data)
    if not rel.zipball or parse_version(rel.tag) is None:
        return UpdateInfo(repo, current, error=f"릴리스 태그 '{rel.tag}' 에서 버전 숫자를 찾지 못했어요 (v1.0.1 처럼 붙이세요)")
    return UpdateInfo(repo, current, release=rel)


# ---------- 내려받기 / 풀기 ----------
def download(url, dest, progress=None, cancelled=None, get=None):
    """url -> dest (임시 .part 로 받고 끝나면 바꾼다). progress(frac|None, text)."""
    get = get or requests.get
    tmp = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with get(url, stream=True, timeout=30, headers={"User-Agent": UA["User-Agent"]}) as r:
            if r.status_code != 200:
                raise UpdateError(f"내려받기 실패: HTTP {r.status_code}")
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    if cancelled and cancelled():
                        raise UpdateError("취소했어요")
                    f.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got / total if total else None, f"내려받는 중… {got / 1048576:.1f}MB" + (f" / {total / 1048576:.1f}MB" if total else ""))
        os.replace(tmp, dest)
    except (requests.RequestException, OSError) as e:
        raise UpdateError(f"내려받기 실패: {e}") from e
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    if not zipfile.is_zipfile(dest):
        raise UpdateError("내려받은 파일이 zip 이 아니에요")


def safe_extract(zip_path, dest):
    """zip 을 dest 에 푼다. 폴더 밖으로 나가는 경로(zip-slip)나 절대 경로가 있으면 거부한다."""
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    real = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            target = os.path.realpath(os.path.join(dest, name))
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or not (target == real or target.startswith(real + os.sep)):
                raise UpdateError(f"zip 안에 안전하지 않은 경로가 있어요: {info.filename}")
        z.extractall(dest)
    return dest


def _find_dir(root, names, depth=3):
    for cur, dirs, _ in _walk(root, depth):
        for n in names:
            if n in dirs:
                return cur
    return ""


def _walk(root, depth):
    root = os.path.abspath(root)
    base = root.count(os.sep)
    for cur, dirs, files in os.walk(root):
        if cur.count(os.sep) - base >= depth:
            dirs[:] = []
        yield cur, list(dirs), files


def find_payload_root(extracted):
    """풀린 폴더 안에서 Project_Miku_server / client / launcher 폴더가 들어 있는 곳을 찾는다 (GitHub zipball 은 한 겹 더 싸여 있다)."""
    return _find_dir(extracted, ("Project_Miku_server", "Project_Miku_Server", "Project_Miku_client", "Project_Miku_launcher"))


def _first_dir(root, names):
    for n in names:
        p = os.path.join(root, n)
        if os.path.isdir(p):
            return p
    return ""


# ---------- 바뀔 파일 계산 ----------
def is_ignored(rel):
    """어디에 있든 업데이트가 다루지 않는 파일 (venv, node_modules, 로그, 백업, 컴파일 결과 ...)."""
    parts = rel.replace("\\", "/").split("/")
    return any(p in SKIP_DIRS for p in parts[:-1]) or parts[-1].lower().endswith(SKIP_SUFFIX)


def is_user_file(kind, rel):
    """사용자가 만들거나 고치는 파일 (설정, 대화 기록, DB, 모델, persona.txt ...). 이미 있으면 덮어쓰지 않는다."""
    low = rel.replace("\\", "/").lower()
    for rule in PROTECTED.get(kind, ()):
        if rule.endswith("/"):
            if low.startswith(rule):
                return True
        elif rule.startswith("*."):
            if "/" not in low and low.endswith(rule[1:]):
                return True
        elif low == rule:
            return True
    return False


def _same(a, b):
    try:
        if os.path.getsize(a) == os.path.getsize(b):
            with open(a, "rb") as fa, open(b, "rb") as fb:
                if fa.read() == fb.read():
                    return True
        if os.path.splitext(a)[1].lower() in TEXT_EXT:   # 줄 끝(CRLF/LF)만 다른 건 같은 파일로 본다
            with open(a, "rb") as fa, open(b, "rb") as fb:
                return fa.read().replace(b"\r\n", b"\n") == fb.read().replace(b"\r\n", b"\n")
    except OSError:
        pass
    return False


class Item:
    def __init__(self, kind, rel, src, dst, action):
        self.kind, self.rel, self.src, self.dst, self.action = kind, rel, src, dst, action   # action: add | update | same | protected


def build_plan(targets):
    """targets: [(kind, src_dir, dst_dir, only_files|None)]. 파일을 건드리지 않고 Item 목록만 만든다."""
    items = []
    for kind, src, dst, only in targets:
        if not src or not dst:
            continue
        for cur, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS] if only is None else []
            for fn in files:
                s = os.path.join(cur, fn)
                rel = os.path.relpath(s, src).replace(os.sep, "/")
                if only is not None and rel not in only:
                    continue
                d = os.path.join(dst, *rel.split("/"))
                if is_ignored(rel) or (is_user_file(kind, rel) and os.path.exists(d)):
                    items.append(Item(kind, rel, s, d, "protected"))
                elif not os.path.exists(d):
                    items.append(Item(kind, rel, s, d, "add"))
                elif _same(s, d):
                    items.append(Item(kind, rel, s, d, "same"))
                else:
                    items.append(Item(kind, rel, s, d, "update"))
    return items


def file_hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return ""


def apply_plan(items, backup_dir):
    """add/update 항목만 반영한다. 덮어쓰는 파일은 backup_dir 에 먼저 복사. 하나라도 실패하면 이미 바꾼 것을 되돌리고 UpdateError.
    돌려주는 값: manifest(dict) — rollback() 이 쓴다."""
    todo = [i for i in items if i.action in ("add", "update")]
    done = []
    cur = None
    try:
        for it in todo:
            cur = it
            entry = {"dst": it.dst, "kind": it.kind, "rel": it.rel, "existed": it.action == "update"}
            if entry["existed"]:
                b = os.path.join(backup_dir, it.kind, *it.rel.split("/"))
                os.makedirs(os.path.dirname(b), exist_ok=True)
                shutil.copy2(it.dst, b)
            os.makedirs(os.path.dirname(it.dst), exist_ok=True)
            tmp = it.dst + ".miku-new"
            shutil.copy2(it.src, tmp)
            os.replace(tmp, it.dst)
            done.append(entry)
    except OSError as e:
        _undo(done, backup_dir)
        if cur is not None and os.path.exists(cur.dst + ".miku-new"):
            try:
                os.remove(cur.dst + ".miku-new")
            except OSError:
                pass
        raise UpdateError(f"파일을 바꾸지 못했어요 ({e.filename or ''}: {e.strerror or e}). 이미 바꾼 파일은 되돌렸어요. 서버/클라/백신이 파일을 잡고 있는지 확인하세요") from e
    return {"files": done}


def _undo(entries, backup_dir):
    for e in reversed(entries):
        try:
            if e["existed"]:
                shutil.copy2(os.path.join(backup_dir, e["kind"], *e["rel"].split("/")), e["dst"])
            elif os.path.isfile(e["dst"]):
                os.remove(e["dst"])
        except OSError:
            pass
        try:
            if os.path.exists(e["dst"] + ".miku-new"):
                os.remove(e["dst"] + ".miku-new")
        except OSError:
            pass


def rollback(backup_dir):
    """apply_plan 이 남긴 manifest.json 대로 되돌린다. 되돌린 파일 수를 돌려준다."""
    with open(os.path.join(backup_dir, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    _undo(m.get("files", []), backup_dir)
    return len(m.get("files", []))


# ---------- 런처(exe) 교체 ----------
def swap_script(staged_dir, base_dir, pid, restart=True, exe_name="MikuLauncher.exe"):
    """런처가 끝난 뒤 새 exe 폴더를 덮어쓰고 (restart 이면) 다시 켜는 배치 파일 내용."""
    staged_dir, base_dir = os.path.normpath(staged_dir), os.path.normpath(base_dir)
    xf = " ".join(f'"{n}"' for n in SWAP_EXCLUDE_FILES)
    xd = " ".join(f'"{n}"' for n in SWAP_EXCLUDE_DIRS)
    fail = os.path.join(base_dir, "updates", "swap_failed.txt")
    return "\r\n".join([
        "@echo off", "chcp 65001 >nul", "setlocal",
        ":wait", f'tasklist /FI "PID eq {pid}" 2>nul | find " {pid} " >nul',
        "if not errorlevel 1 (", "  timeout /t 1 /nobreak >nul", "  goto wait", ")",
        f'robocopy "{staged_dir}" "{base_dir}" /E /XF {xf} /XD {xd} /R:5 /W:2 /NFL /NDL /NJH /NJS >nul',
        f'if errorlevel 8 echo robocopy failed > "{fail}"',
        *([f'start "" "{os.path.join(base_dir, exe_name)}"'] if restart else []),
        'del "%~f0"', "",
    ])


# ---------- 서비스 ----------
class UpdateService:
    """core(LauncherCore) 를 빌려 쓰는 업데이트 진행자. check()/apply()/rollback_last() 는 오래 걸리니 스레드에서 부른다."""

    def __init__(self, core, get=None):
        self.core = core
        self.get = get or requests.get
        self.info = None            # 마지막 확인 결과 (UpdateInfo)
        self.state = {"busy": False, "text": "", "frac": None}
        self.pending_swap = ""      # 런처가 끝날 때 덮어쓸 새 exe 폴더 (exe 교체 예약)
        self.restart_after_swap = False   # 교체 뒤 런처를 다시 켤까 ('런처 다시 시작' 버튼을 눌렀을 때만)
        self.needs_restart = False  # 런처 코드가 바뀌어서 런처를 다시 켜야 하는가 (소스 실행일 때)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    # --- 경로 / 설정 ---
    @property
    def base_dir(self):
        return self.core.base_dir

    @property
    def root_dir(self):
        return os.path.dirname(self.base_dir)

    @property
    def updates_dir(self):
        return os.path.join(self.base_dir, "updates")

    @property
    def version_file(self):
        return os.path.join(self.root_dir, "VERSION")

    def repo(self):
        return parse_repo(self.core.settings["update_repo"]) or parse_repo(DEFAULT_REPO)

    def current_version(self):
        try:
            with open(self.version_file, encoding="utf-8-sig") as f:
                v = f.read().strip()
            return v if parse_version(v) else ""
        except OSError:
            return ""

    def log(self, text):
        self.core.log(f"[업데이트] {text}")

    def _set(self, text, frac=None):
        self.state["text"], self.state["frac"] = text, frac

    def _state_path(self):
        return os.path.join(self.updates_dir, "state.json")

    def _last_check(self):
        try:
            with open(self._state_path(), encoding="utf-8") as f:
                return float(json.load(f).get("last_check") or 0)
        except (OSError, ValueError, AttributeError):
            return 0.0

    def _save_last_check(self):
        try:
            os.makedirs(self.updates_dir, exist_ok=True)
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump({"last_check": time.time()}, f)
        except OSError:
            pass

    # --- 화면용 ---
    def skipped(self):
        i = self.info
        return bool(i and i.release and str(self.core.settings["update_skip_version"] or "") == i.release.version)

    def warning(self):
        """메인 창 위쪽 경고줄에 띄울 글 (새 버전이 있고 건너뛰지 않았을 때)."""
        i = self.info
        if i and i.available and not self.skipped():
            return f"새 버전 {i.release.version} 이(가) 나왔어요 (지금 {self.current_version() or '알 수 없음'}). '런처' 탭의 업데이트에서 설치하세요"
        if self.needs_restart or self.pending_swap:
            return "업데이트가 적용됐어요. 런처를 다시 켜야 런처 자신의 변경이 반영돼요 ('런처' 탭 > 업데이트)"
        return ""

    # --- 확인 ---
    def check(self):
        """새 버전을 확인한다. UpdateInfo 를 돌려주고 self.info 에 저장한다."""
        cur = self.current_version()
        self._set("확인하는 중…")
        info = fetch_latest(self.repo(), cur, get=self.get)
        self.info = info
        self._save_last_check()
        if info.error:
            self.log(f"확인 실패: {info.error}")
            self._set(info.error)
        elif info.available:
            self.log(f"새 버전 {info.release.version} 이(가) 있어요 (지금 {cur or '알 수 없음'})")
            self._set(f"새 버전 {info.release.version} 을(를) 설치할 수 있어요")
        else:
            self.log(f"최신 버전이에요 ({cur or info.release.version})")
            self._set(f"최신 버전이에요 ({cur or info.release.version})")
        return info

    # --- 시작할 때 / 주기적으로 ---
    def startup(self):
        """런처를 켠 직후 (서버/클라를 켜기 전에) 부른다. 자동 설치가 켜져 있으면 여기서 설치까지 한다."""
        s = self.core.settings
        if not s["update_check"] or not self.repo():
            return
        if s["update_auto_apply"]:
            try:
                info = self.check()
                if info.available and not self.skipped():
                    self.log("자동 설치가 켜져 있어서 지금 설치해요 (서버/클라는 설치가 끝난 뒤에 켜져요)")
                    self.apply(restart_after=False)
            except Exception as e:  # noqa: BLE001
                self.log(f"자동 업데이트 중 오류: {e!r} (예전 버전 그대로 계속해요)")
        else:
            threading.Thread(target=self._safe_check, name="update-check", daemon=True).start()

    def _safe_check(self):
        try:
            self.check()
        except Exception as e:  # noqa: BLE001
            self.log(f"확인 중 오류: {e!r}")

    def start_background(self):
        """켜 두는 동안 update_interval_hours 마다 다시 확인한다 (알려 주기만 한다. 설치는 시작할 때나 버튼으로)."""
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="update-loop", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(600):   # 10분마다 '확인할 때가 됐나' 만 본다
            s = self.core.settings
            hours = max(1.0, float(s["update_interval_hours"] or 12))
            if s["update_check"] and self.repo() and not self.state["busy"] and time.time() - self._last_check() >= hours * 3600:
                self._safe_check()

    def stop(self):
        self._stop.set()

    # --- 설치 ---
    def _fail(self, msg):
        self.log(f"✖ {msg}")
        self._set(msg)
        return False

    def apply(self, restart_after=True):
        """새 버전을 내려받아 설치한다. 성공하면 True. 서버/클라가 켜져 있으면 끄고, 끝나면 (restart_after 일 때) 다시 켠다."""
        if not self._lock.acquire(blocking=False):
            return self._fail("이미 업데이트 중이에요")
        core = self.core
        self.state["busy"] = True
        was_server = was_client = False
        try:
            info = self.info if (self.info and self.info.available) else self.check()
            if not info.available:
                return self._fail(info.error or "설치할 새 버전이 없어요")
            rel = info.release
            if os.path.isdir(os.path.join(self.root_dir, ".git")):
                raise UpdateError("git 으로 받은 폴더라서 런처가 파일을 바꾸지 않아요. git pull 로 업데이트하세요")
            was_server = bool(core.server.alive or core.poller.last)
            was_client = bool(core.client.alive)
            self._stop_apps(was_server, was_client)
            return self._apply(rel, info.current)
        except UpdateError as e:
            return self._fail(str(e))
        except Exception as e:  # noqa: BLE001
            return self._fail(f"업데이트 중 오류: {e!r}")
        finally:
            self.state["busy"] = False
            self._lock.release()
            if restart_after:
                self._start_apps(was_server, was_client)

    def _stop_apps(self, server, client):
        core = self.core
        if client:
            self.log("클라를 끄는 중…")
            core.stop_client()
        if server:
            self.log("서버를 끄는 중…")
            core.stop_server()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and core.poller.check():
                time.sleep(0.3)
            if core.poller.check():
                raise UpdateError("서버가 꺼지지 않아요. 서버를 직접 끈 뒤에 다시 시도하세요")

    def _start_apps(self, server, client):
        core = self.core
        try:
            if server and not core._closing:
                core.start_server()
            if client and not core._closing:
                if server:   # 서버가 응답할 때까지 잠깐 기다렸다가 켠다
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline and not core.poller.last and core.server.alive:
                        time.sleep(0.5)
                core.start_client()
        except Exception as e:  # noqa: BLE001
            self.log(f"업데이트 뒤에 다시 켜지 못했어요: {e}")

    def _targets(self, proot):
        s = self.core.settings
        t = [("root", proot, self.root_dir, set(ROOT_FILES))]
        srv, cli = _first_dir(proot, SERVER_NAMES), _first_dir(proot, CLIENT_NAMES)
        if srv and s.server_dir:
            t.append(("server", srv, s.server_dir, None))
        elif srv:
            self.log("서버 폴더를 찾지 못해서 서버 파일은 건너뛰어요 ('런처' 탭에서 지정)")
        if cli and s.client_dir:
            t.append(("client", cli, s.client_dir, None))
        elif cli:
            self.log("클라 폴더를 찾지 못해서 클라 파일은 건너뛰어요 ('런처' 탭에서 지정)")
        return t

    def _apply(self, rel, current):
        core, s = self.core, self.core.settings
        stamp = time.strftime("%Y%m%d-%H%M%S")
        work = os.path.join(self.updates_dir, "work")
        os.makedirs(work, exist_ok=True)
        # 1) 설정 프리셋 백업 (설정 파일 형식이 바뀌어도 값을 되살릴 수 있게)
        try:
            core._auto_backup_preset()
        except Exception as e:  # noqa: BLE001
            self.log(f"설정 백업 프리셋을 만들지 못했어요 (계속 진행): {e}")
        # 2) 내려받기 + 풀기
        zpath = os.path.join(work, f"source-{rel.tag}.zip".replace("/", "_"))
        self.log(f"{rel.version} 을(를) 내려받아요")
        download(rel.zipball, zpath, progress=self._set, cancelled=lambda: core._closing, get=self.get)
        self._set("압축을 푸는 중…")
        extracted = safe_extract(zpath, os.path.join(work, "src"))
        proot = find_payload_root(extracted)
        if not proot:
            raise UpdateError("내려받은 파일에서 Project_Miku_server / Project_Miku_client 폴더를 찾지 못했어요 (릴리스 태그가 가리키는 소스를 확인하세요)")
        # 3) 바뀔 파일 계산 (의존성 변경 감지를 위해 바꾸기 전 해시를 적어 둔다)
        deps_before = self._deps_hashes()
        launcher_items = self._launcher_items(proot)
        items = build_plan(self._targets(proot)) + launcher_items
        add = sum(1 for i in items if i.action == "add")
        upd = sum(1 for i in items if i.action == "update")
        prot = [i for i in items if i.action == "protected"]
        self.log(f"바꿀 파일: 새 파일 {add}개, 바뀐 파일 {upd}개 (같은 파일 {sum(1 for i in items if i.action == 'same')}개, 내 설정/데이터라 건드리지 않는 파일 {len(prot)}개)")
        # 4) 백업하고 덮어쓰기
        backup = os.path.join(self.updates_dir, "backups", f"{stamp}_{current or 'unknown'}-to-{rel.version}")
        self._set("파일을 바꾸는 중…")
        manifest = apply_plan(items, backup)
        manifest.update(src_version=current, to_version=rel.version, time=stamp)
        os.makedirs(backup, exist_ok=True)
        with open(os.path.join(backup, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        self._prune_backups()
        with open(self.version_file, "w", encoding="utf-8") as f:
            f.write(rel.version + "\n")
        self.log(f"✔ {rel.version} 파일을 바꿨어요 (백업: {backup})")
        # 5) 런처(exe) 교체 예약
        self._plan_launcher_swap(rel, work, launcher_items)
        shutil.rmtree(work, ignore_errors=True)   # 내려받은 zip 과 풀어 둔 소스는 더 필요 없다
        # 6) 의존성이 바뀌었으면 설치 탭의 항목을 다시 설치
        self._reinstall_deps(deps_before)
        s_changed = [i for i in items if i.action == "update" and i.rel == "config.example.yaml"]
        if s_changed:
            self.log("config.example.yaml 이 바뀌었어요. 새 항목은 서버 설정 탭에서 확인하세요 (기존 config.yaml 값은 그대로예요)")
        self.info = UpdateInfo(self.repo(), rel.version, release=rel)
        self._set(f"{rel.version} 으로 업데이트했어요")
        return True

    # --- 런처 파일 ---
    def _launcher_items(self, proot):
        """소스로 실행 중이면 런처 .py 도 바꾼다. exe 로 실행 중이면 소스를 바꿔도 소용없으므로 건너뛰고(아래 swap 참고) 알린다."""
        src = _first_dir(proot, LAUNCHER_NAMES)
        if not src:
            return []
        if FROZEN:
            return []
        items = build_plan([("launcher", src, self.base_dir, None)])
        for i in items:
            if i.action in ("add", "update") and (i.rel.endswith(".exe") or i.rel.startswith("_internal/")):
                i.action = "protected"   # 소스 실행 환경에서는 exe 를 건드리지 않는다
        if any(i.action in ("add", "update") and i.rel.endswith(".py") for i in items):
            self.needs_restart = True
            self.log("런처 코드도 바뀌었어요. 런처를 다시 켜면 적용돼요")
        return items

    def _plan_launcher_swap(self, rel, work, launcher_items):
        if not FROZEN:
            return
        if not rel.launcher_asset:
            self.log("exe 런처는 소스 업데이트로 바뀌지 않아요. 런처 자체가 바뀐 릴리스라면 Releases 에서 새 MikuLauncher 를 받으세요 (이번 릴리스에는 MikuLauncher*.zip 이 없어요)")
            return
        a = rel.launcher_asset
        self.log(f"런처 {a['name']} 을(를) 내려받아요")
        zpath = os.path.join(work, a["name"])
        download(a["url"], zpath, progress=self._set, cancelled=lambda: self.core._closing, get=self.get)
        stage = safe_extract(zpath, os.path.join(work, "launcher"))
        exe_dir = ""
        for cur, _, files in _walk(stage, 3):
            if "MikuLauncher.exe" in files:
                exe_dir = cur
                break
        if not exe_dir or not os.path.isfile(os.path.join(exe_dir, "MikuLauncher.exe")):
            raise UpdateError("MikuLauncher zip 안에 MikuLauncher.exe 가 없어요")
        keep = os.path.join(self.updates_dir, "launcher-new")   # work 폴더는 정리되니 따로 둔다
        shutil.rmtree(keep, ignore_errors=True)
        shutil.move(exe_dir, keep)
        self.pending_swap = keep
        self.log("런처 새 버전을 준비했어요. 런처를 끌 때 교체해요 ('런처 다시 시작' 버튼을 누르면 교체 뒤 바로 다시 켜요)")

    def launch_pending_swap(self):
        """런처가 끝나기 직전에 부른다. 예약된 교체가 있으면 배치 파일을 따로 띄운다 (런처가 끝난 뒤 실행된다)."""
        if not self.pending_swap or os.name != "nt" or not os.path.isfile(os.path.join(self.pending_swap, "MikuLauncher.exe")):
            return False
        bat = os.path.join(self.updates_dir, "swap.bat")
        with open(bat, "w", encoding="utf-8", newline="") as f:
            f.write(swap_script(self.pending_swap, self.base_dir, os.getpid(), restart=self.restart_after_swap))
        flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True, cwd=self.updates_dir)
        return True

    # --- 의존성 ---
    def _deps_hashes(self):
        s = self.core.settings
        out = {}
        for key, base, names in (("server", s.server_dir, ("requirements.txt",)), ("client", s.client_dir, ("package.json", "package-lock.json"))):
            out[key] = tuple(file_hash(os.path.join(base, n)) for n in names) if base else ()
        return out

    def _reinstall_deps(self, before):
        core = self.core
        after = self._deps_hashes()
        ids = []
        for mid in ("server", "client"):
            if before.get(mid) != after.get(mid):
                core.installer.refresh(wait=True)
                if core.installer.status.get(mid, ("", ""))[0] == "ok":
                    ids.append(mid)   # 이미 설치해 둔 항목만 다시 설치한다 (안 쓰던 걸 새로 깔지는 않는다)
        if not ids:
            return
        self.log("필요한 패키지 목록이 바뀌어서 설치 탭의 항목을 다시 설치해요: " + ", ".join(ids))
        self._set("패키지를 다시 설치하는 중… (설치 탭에서 진행 상황을 볼 수 있어요)")
        why = core.installer.start(ids)
        if why:
            self.log(f"패키지 재설치를 시작하지 못했어요: {why} — 설치 탭에서 직접 설치하세요")
            return
        while core.installer.running and not core._closing:
            time.sleep(1.0)
        self.log("패키지 재설치가 끝났어요 (결과는 설치 탭 / 로그에서 확인)")

    # --- 되돌리기 ---
    def backups(self):
        d = os.path.join(self.updates_dir, "backups")
        try:
            names = [n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n, "manifest.json"))]
        except OSError:
            return []
        return [os.path.join(d, n) for n in sorted(names, reverse=True)]

    def _prune_backups(self):
        for old in self.backups()[KEEP_BACKUPS:]:
            shutil.rmtree(old, ignore_errors=True)

    def rollback_last(self):
        """마지막 업데이트를 되돌린다 (바뀐 파일을 백업으로 복원, 새로 생긴 파일은 삭제). 성공하면 True."""
        if not self._lock.acquire(blocking=False):
            return self._fail("이미 업데이트 중이에요")
        core = self.core
        self.state["busy"] = True
        was_server = was_client = False
        try:
            bk = self.backups()
            if not bk:
                return self._fail("되돌릴 백업이 없어요")
            was_server, was_client = bool(core.server.alive or core.poller.last), bool(core.client.alive)
            self._stop_apps(was_server, was_client)
            with open(os.path.join(bk[0], "manifest.json"), encoding="utf-8") as f:
                m = json.load(f)
            n = rollback(bk[0])
            if m.get("src_version"):
                with open(self.version_file, "w", encoding="utf-8") as f:
                    f.write(str(m["src_version"]) + "\n")
            shutil.rmtree(bk[0], ignore_errors=True)
            self.info = None
            self.log(f"✔ 마지막 업데이트를 되돌렸어요 ({n}개 파일, {m.get('to_version')} → {m.get('src_version') or '이전'})")
            self._set("이전 버전으로 되돌렸어요")
            return True
        except Exception as e:  # noqa: BLE001
            return self._fail(f"되돌리지 못했어요: {e!r}")
        finally:
            self.state["busy"] = False
            self._lock.release()
            self._start_apps(was_server, was_client)
