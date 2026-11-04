"""모듈 설치 매니저 (Qt 와 무관): 필수/선택 모듈이 설치돼 있는지 확인하고, 런처에서 바로 설치한다.

- 필수: 서버 패키지(venv + requirements.txt, ddgs 포함), 클라이언트(npm install)
- 선택 · 마이크: 음성 인식(faster-whisper), VAD/화자 인증 모델, NVIDIA GPU 라이브러리
- 선택 · RVC: 별도 환경(venv) + rvc-python + torch (GPU 가 있으면 CUDA 용, 없으면 CPU 용)
  · 옵션: 츠쿠요미짱 공식 RVC 모델 (.pth/.index 만). RVC 를 설치해도 자동으로 받지 않고, 이 항목의 버튼을 눌렀을 때만 받는다
- 선택 · GPT-SoVITS: 공식 윈도우 통합 패키지(7z, 자체 파이썬 + 기본 사전학습 모델 포함)를 tools/gptsovits 에 푼다. 목소리 모델은 넣지 않는다 (참조 음성은 사용자가 준비)

파이썬은 ① 런처 폴더의 tools/python (동봉) ② 이 컴퓨터의 3.10~3.11 ③ 없으면 포터블 파이썬을 받아서 tools/python 에 푼다.
Node 도 같은 방식 (PATH 의 node ③ 없으면 tools/node 에 받는다). 모든 작업은 백그라운드 스레드에서 돌고, 출력은 lines 에 쌓인다.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile

import requests

from . import procs

WIN = os.name == "nt"
PY_RANGE = ((3, 10), (3, 11))       # 서버(onnxruntime, ctranslate2)와 RVC(torch 2.1.1, fairseq)가 모두 되는 범위
PY_PREFERRED = (3, 11)
NODE_MIN_MAJOR = 20
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
UA = {"User-Agent": "miku-launcher"}
PBS_API = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
NODE_INDEX = "https://nodejs.org/dist/index.json"
TORCH_VERSION = "2.1.1"
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu118"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
SOVITS_REPO = "lj1995/GPT-SoVITS-windows-package"
SOVITS_API = f"https://huggingface.co/api/models/{SOVITS_REPO}"
SOVITS_FILE_URL = f"https://huggingface.co/{SOVITS_REPO}/resolve/main/" + "{name}"
TYC_RVC_URL = "https://tyc.rei-yumesaki.net/files/voice/tyc-rvc.zip"        # 츠쿠요미짱 공식 RVC 모델 (공식 배포처)
TYC_RVC_PAGE = "https://tyc.rei-yumesaki.net/work/software/rvc/"
TYC_RVC_TERMS = "https://tyc.rei-yumesaki.net/work/software/rvc/terms/"
TYC_MODEL_NAME = "tsukuyomi"                                                # rvc_models/<이 이름>/ 에 넣는다
TYC_PREFERRED = ("通常1", "通常_1", "normal1", "normal_1")                   # 여러 모델 중 먼저 고르는 것
TYC_MAX_MEMBER = 2 * 1024 ** 3                                              # 압축 안 파일 하나의 크기 상한
SEVENZR_URL = "https://www.7-zip.org/a/7zr.exe"      # 7z 압축을 풀 수 있는 작은 단독 실행 파일
SOVITS_MIN_FREE_GB = 25                               # 받는 파일(약 8GB) + 푼 결과 + 여유

SERVER_REQUIRED = ("uvicorn", "fastapi", "yaml", "requests", "multipart", "edge_tts", "miniaudio", "ddgs")


class InstallError(Exception):
    pass


class Cancelled(InstallError):
    pass


# ---------------------------------------------------------------- 경로/탐색 도구
def venv_python(venv_dir):
    return os.path.join(venv_dir, "Scripts", "python.exe") if WIN else os.path.join(venv_dir, "bin", "python")


def python_version(py):
    """(major, minor) — 실행이 안 되면 None."""
    try:
        r = subprocess.run([py, "-c", "import sys;print(sys.version_info[0],sys.version_info[1])"], capture_output=True, text=True,
                           timeout=20, creationflags=NO_WINDOW)
        a, b = r.stdout.split()[:2]
        return int(a), int(b)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def version_ok(v):
    return v is not None and PY_RANGE[0] <= tuple(v) <= PY_RANGE[1]


def has_nvidia_gpu():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "-L"], capture_output=True, timeout=15, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def gpu_names():
    """nvidia-smi 로 본 GPU 이름 목록 (없으면 빈 목록)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=15,
                           creationflags=NO_WINDOW)
        return [x.strip() for x in r.stdout.splitlines() if x.strip()] if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


def is_rtx50(names):
    """RTX 50 시리즈(예: RTX 5090)는 통합 패키지가 별도(nvidia50)다."""
    return any(re.search(r"\bRTX\s*50\d\d\b", n, re.I) for n in names)


_SOVITS_NAME = re.compile(r"^GPT-SoVITS-(v\d\w*)-(\d{8})(fix\d*)?(-nvidia50)?\.7z$")


def pick_sovits_asset(filenames, nvidia50=False):
    """HF 저장소 파일 목록에서 가장 새 통합 패키지(.7z) 이름을 고른다. 없으면 None.
    - 'GPT-SoVITS-v2pro-20250604.7z' 처럼 버전과 날짜가 붙은 것만 (beta/실험 브랜치는 제외)
    - nvidia50 이면 '-nvidia50' 파일을 우선, 아니면 그것을 뺀 파일. 원하는 쪽이 하나도 없으면 다른 쪽으로 대신한다."""
    cands = []
    for n in filenames:
        m = _SOVITS_NAME.match(n)
        if m:
            cands.append(((m.group(2), m.group(3) or ""), bool(m.group(4)), n))   # (날짜, fix), nvidia50 여부, 이름
    if not cands:
        return None
    pool = [c for c in cands if c[1] == bool(nvidia50)] or cands
    return max(pool, key=lambda c: c[0])[2]


def find_sovits_root(extracted, max_depth=3):
    """압축을 푼 폴더 안에서 api_v2.py 가 있는 폴더를 찾는다 (패키지가 한 겹 폴더로 감싸져 있어도 됨). 없으면 None."""
    base_depth = extracted.rstrip("\\/").count(os.sep)
    for root, dirs, files in os.walk(extracted):
        if root.count(os.sep) - base_depth > max_depth:
            dirs[:] = []
            continue
        if "api_v2.py" in files:
            return root
    return None


def pick_pbs_asset(release, minor=PY_PREFERRED[1]):
    """python-build-standalone 릴리스(JSON)에서 윈도우 x64 'install_only' 파일 하나를 고른다. 없으면 None. (이름, 주소)"""
    pat = re.compile(rf"^cpython-3\.{minor}\.(\d+)\+\d+-x86_64-pc-windows-msvc-install_only(_stripped)?\.tar\.gz$")
    best = None
    for a in release.get("assets") or []:
        m = pat.match(a.get("name", ""))
        if m:
            key = (int(m.group(1)), not m.group(2))   # 패치 버전이 높은 것, 같으면 stripped 아닌 것
            if best is None or key > best[0]:
                best = (key, a["name"], a["browser_download_url"])
    return (best[1], best[2]) if best else None


def pick_node_release(index, min_major=NODE_MIN_MAJOR):
    """nodejs.org/dist/index.json 에서 LTS 중 가장 새 것의 (버전, zip 주소). 없으면 None."""
    for r in index:
        if r.get("lts") and "win-x64-zip" in (r.get("files") or []):
            major = int(str(r["version"]).lstrip("v").split(".")[0])
            if major >= min_major:
                v = r["version"]
                return v, f"https://nodejs.org/dist/{v}/node-{v}-win-x64.zip"
    return None


# ---------------------------------------------------------------- 환경 (경로와 현재 설정)
class Env:
    """설치에 필요한 경로. server_dir/client_dir 는 매번 설정에서 읽는다 (런처 탭에서 바꿀 수 있어서)."""

    def __init__(self, base_dir, settings, server_config=None, set_config=None):
        self.base_dir = os.path.abspath(base_dir)
        self.settings = settings
        self.server_config = server_config      # callable -> ServerConfig (RVC 파이썬 경로 읽기용)
        self.set_config = set_config            # callable(path, value) — config.yaml 에 저장

    tools = property(lambda self: os.path.join(self.base_dir, "tools"))
    server_dir = property(lambda self: self.settings.server_dir)
    client_dir = property(lambda self: self.settings.client_dir)
    bundled_python = property(lambda self: os.path.join(self.tools, "python", "python.exe" if WIN else "bin/python3"))
    rvc_venv = property(lambda self: os.path.join(self.tools, "rvc", "venv"))
    sovits_dir = property(lambda self: os.path.join(self.tools, "gptsovits"))

    def server_venv(self):
        return os.path.join(self.server_dir, "venv")

    def server_python(self, modules=SERVER_REQUIRED):
        """서버를 돌릴 수 있는 파이썬(필수 모듈이 다 있는 것). 없으면 None."""
        if not self.server_dir:
            return None
        try:
            return procs.python_for_server(str(self.settings["server_python"] or "").strip(), self.server_dir, modules)[0]
        except procs.StartError:
            return None

    def server_target_python(self):
        """선택 모듈을 설치할 서버 파이썬: 직접 지정한 파이썬 > 서버 폴더의 venv > 필수 모듈이 다 있는 파이썬. 없으면 None."""
        configured = str(self.settings["server_python"] or "").strip()
        if configured and os.path.isfile(configured):
            return configured
        vp = venv_python(self.server_venv()) if self.server_dir else ""
        if vp and os.path.isfile(vp):
            return vp
        return self.server_python()

    def rvc_python(self):
        cur = ""
        if self.server_config:
            try:
                cfg = self.server_config().cfg_or_empty()
                cur = str((((cfg.get("tts") or {}).get("rvc") or {}).get("launch") or {}).get("python") or "")
            except Exception:  # noqa: BLE001
                cur = ""
        if cur and os.path.isfile(cur):
            return cur
        return venv_python(self.rvc_venv)

    def rvc_models_dir(self):
        """RVC 모델 폴더(rvc_models). 서버 설정의 RVC 폴더(tts.rvc.launch.dir)가 실제로 있으면 거기, 아니면 tools/rvc."""
        cur = ""
        if self.server_config:
            try:
                cfg = self.server_config().cfg_or_empty()
                cur = str((((cfg.get("tts") or {}).get("rvc") or {}).get("launch") or {}).get("dir") or "")
            except Exception:  # noqa: BLE001
                cur = ""
        root = cur if cur and os.path.isdir(cur) else os.path.join(self.tools, "rvc")
        return os.path.join(root, "rvc_models")

    def find_node(self):
        local = os.path.join(self.tools, "node", "node.exe" if WIN else "bin/node")
        if os.path.isfile(local):
            return local
        found = shutil.which("node")
        return found

    def find_npm(self):
        local = os.path.join(self.tools, "node", "npm.cmd" if WIN else "bin/npm")
        if os.path.isfile(local):
            return local
        return shutil.which("npm.cmd" if WIN else "npm") or shutil.which("npm")


# ---------------------------------------------------------------- 작업 (한 번의 설치 실행 중 쓰는 도구)
class Job:
    def __init__(self, env, say, progress, cancelled):
        self.env, self.say, self.progress, self.cancelled = env, say, progress, cancelled
        self._proc = None

    def check_cancel(self):
        if self.cancelled():
            raise Cancelled("취소했어요")

    def run(self, cmd, cwd=None, extra_env=None):
        """명령을 실행하면서 출력을 그대로 보여 준다. 0 이 아니면 InstallError."""
        self.check_cancel()
        self.say("$ " + " ".join(str(c) for c in cmd))
        env = dict(os.environ, PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONIOENCODING="utf-8")
        env.update(extra_env or {})
        try:
            p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 creationflags=NO_WINDOW)
        except OSError as e:
            raise InstallError(f"실행하지 못했어요: {cmd[0]} ({e})") from None
        self._proc = p
        tail = []
        try:
            for raw in iter(p.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    tail.append(line)
                    del tail[:-12]
                    self.say(line)
                if self.cancelled():
                    procs.kill_tree(p.pid)
                    raise Cancelled("취소했어요")
            code = p.wait()
        finally:
            self._proc = None
            if p.poll() is None:
                procs.kill_tree(p.pid)
            p.stdout.close()
        if code != 0:
            raise InstallError(f"명령이 실패했어요 (코드 {code}): {' '.join(str(c) for c in cmd[:4])}…\n" + "\n".join(tail[-6:]))

    def pip(self, py, *args):
        self.run([py, "-m", "pip", "install", "--progress-bar", "off", *args])

    def pip_uninstall(self, py, *pkgs):
        self.run([py, "-m", "pip", "uninstall", "-y", *pkgs])

    def remove_dir(self, path, allowed_roots, label=""):
        """폴더를 지운다. allowed_roots 안쪽이 아니면(실수로 엉뚱한 곳을 지우는 걸 막으려고) 거부한다."""
        self.check_cancel()
        real = os.path.realpath(path)
        roots = [os.path.realpath(r) for r in allowed_roots if r]
        if not any(real != r and real.startswith(r + os.sep) for r in roots):
            raise InstallError(f"안전하지 않은 경로라 지우지 않았어요: {path}")
        if not os.path.isdir(real):
            self.say(f"이미 없어요: {path}")
            return
        self.say(f"지우는 중: {path}")
        errors = []
        shutil.rmtree(real, onerror=lambda f, p, e: errors.append(p))
        if errors or os.path.exists(real):
            raise InstallError(f"다 지우지 못했어요 (사용 중인 파일이 있을 수 있어요): {path}")
        self.say(f"지웠어요{(' — ' + label) if label else ''}")

    def remove_file(self, path):
        try:
            os.remove(path)
            self.say(f"지웠어요: {path}")
        except FileNotFoundError:
            pass
        except OSError as e:
            raise InstallError(f"파일을 지우지 못했어요: {path} ({e})") from None

    def download(self, url, dest, label, resume=False):
        """url -> dest (.part 로 받고 끝나면 이름 변경). 3번까지 다시 시도한다.
        resume=True 면 .part 가 남아 있을 때 이어서 받는다 (수 GB 파일용: 끊겨도 처음부터 다시 받지 않는다)."""
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".part"
        last = None
        tries = 6 if resume else 3
        for attempt in range(tries):
            self.check_cancel()
            try:
                have = os.path.getsize(tmp) if resume and os.path.isfile(tmp) else 0
                headers = dict(UA)
                if have:
                    headers["Range"] = f"bytes={have}-"
                self.say(f"{label} {'이어서 ' if have else ''}내려받는 중… {url}")
                with requests.get(url, stream=True, timeout=30, headers=headers) as r:
                    if r.status_code == 416 and have:   # 이미 끝까지 받은 상태
                        m = re.search(r"/(\d+)$", r.headers.get("content-range", ""))
                        if m and int(m.group(1)) == have:
                            os.replace(tmp, dest)
                            self.progress(None, "")
                            return dest
                        os.remove(tmp)
                        raise InstallError("이어받기 위치가 맞지 않아 처음부터 다시 받아요")
                    r.raise_for_status()
                    clen = int(r.headers.get("content-length") or 0)
                    if have and r.status_code == 206:
                        mode, got, total = "ab", have, have + clen
                    else:   # 이어받기를 지원하지 않으면 처음부터
                        mode, got, total = "wb", 0, clen
                    big = total > 1e9
                    with open(tmp, mode) as f:
                        for chunk in r.iter_content(256 * 1024):
                            self.check_cancel()
                            f.write(chunk)
                            got += len(chunk)
                            if total:
                                txt = f"{got / 1e9:.2f}/{total / 1e9:.2f}GB" if big else f"{got / 1e6:.0f}/{total / 1e6:.0f}MB"
                                self.progress(got / total, f"{label} {txt}")
                    if total and got != total:
                        raise InstallError("받은 크기가 달라요")
                os.replace(tmp, dest)
                self.progress(None, "")
                return dest
            except (requests.RequestException, OSError, InstallError) as e:
                if isinstance(e, Cancelled):
                    raise
                last = e
                self.say(f"받기 실패 ({e}). {'다시 시도해요' if attempt < tries - 1 else ''}")
                time.sleep(2)
        raise InstallError(f"{label} 을(를) 내려받지 못했어요: {last}\n인터넷 연결을 확인하고 다시 시도하세요." + ("\n받던 파일은 남겨 뒀어요. 다시 설치하면 이어서 받아요." if resume else ""))

    # ----- 파이썬 / Node 준비
    def ensure_base_python(self):
        """venv 를 만들 기준 파이썬 (동봉 > 이 컴퓨터의 3.10~3.11 > 받아서 tools/python)."""
        e = self.env
        if os.path.isfile(e.bundled_python) and version_ok(python_version(e.bundled_python)):
            return e.bundled_python
        for name in ("python", "python3"):
            py = shutil.which(name)
            if py and "WindowsApps" not in py and version_ok(python_version(py)):   # WindowsApps 는 스토어 연결용 가짜 python 인 경우가 많다
                return py
        found = shutil.which("py")
        if found:   # 윈도우 파이썬 런처로 3.11/3.10 찾기
            for v in ("3.11", "3.10"):
                r = subprocess.run([found, f"-{v}", "-c", "import sys;print(sys.executable)"], capture_output=True, text=True, creationflags=NO_WINDOW)
                p = r.stdout.strip()
                if r.returncode == 0 and p and os.path.isfile(p):
                    return p
        if not WIN:
            raise InstallError("이 컴퓨터에서 쓸 수 있는 파이썬(3.10~3.11)을 찾지 못했어요")
        self.say("쓸 수 있는 파이썬(3.10~3.11)이 없어서 포터블 파이썬을 받아요")
        try:
            rel = requests.get(PBS_API, timeout=30, headers=UA).json()
        except (requests.RequestException, ValueError) as ex:
            raise InstallError(f"파이썬 내려받을 주소를 알아내지 못했어요: {ex}") from None
        asset = pick_pbs_asset(rel)
        if not asset:
            raise InstallError("받을 수 있는 파이썬 파일을 찾지 못했어요. python.org 에서 3.11 을 설치한 뒤 다시 시도하세요")
        name, url = asset
        arc = self.download(url, os.path.join(e.tools, "_dl", name), "파이썬")
        self.say("압축 푸는 중…")
        tmp = os.path.join(e.tools, "_py_extract")
        shutil.rmtree(tmp, ignore_errors=True)
        with tarfile.open(arc) as t:
            try:
                t.extractall(tmp, filter="data")
            except TypeError:   # 3.12 미만
                t.extractall(tmp)
        dest = os.path.join(e.tools, "python")
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(os.path.join(tmp, "python"), dest)
        shutil.rmtree(tmp, ignore_errors=True)
        os.remove(arc)
        if not version_ok(python_version(e.bundled_python)):
            raise InstallError("받은 파이썬을 실행하지 못했어요")
        return e.bundled_python

    def ensure_venv(self, venv_dir):
        vp = venv_python(venv_dir)
        if os.path.isfile(vp) and python_version(vp):
            return vp
        base = self.ensure_base_python()
        self.say(f"가상환경 만드는 중: {venv_dir}")
        self.run([base, "-m", "venv", venv_dir])
        self.run([vp, "-m", "pip", "install", "--progress-bar", "off", "--upgrade", "pip"])
        return vp

    def ensure_node(self):
        """(node, npm) 경로. 없으면 tools/node 에 받는다."""
        e = self.env
        node, npm = e.find_node(), e.find_npm()
        if node and npm:
            return node, npm
        if not WIN:
            raise InstallError("Node.js 가 없어요. nodejs.org 에서 설치한 뒤 다시 시도하세요")
        self.say("Node.js 가 없어서 받아요")
        try:
            pick = pick_node_release(requests.get(NODE_INDEX, timeout=30, headers=UA).json())
        except (requests.RequestException, ValueError) as ex:
            raise InstallError(f"Node.js 주소를 알아내지 못했어요: {ex}") from None
        if not pick:
            raise InstallError("받을 수 있는 Node.js 를 찾지 못했어요. nodejs.org 에서 직접 설치하세요")
        ver, url = pick
        arc = self.download(url, os.path.join(e.tools, "_dl", f"node-{ver}.zip"), "Node.js")
        tmp = os.path.join(e.tools, "_node_extract")
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(arc) as z:
            z.extractall(tmp)
        dest = os.path.join(e.tools, "node")
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(os.path.join(tmp, f"node-{ver}-win-x64"), dest)
        shutil.rmtree(tmp, ignore_errors=True)
        os.remove(arc)
        return e.find_node(), e.find_npm()

    def node_env(self, node):
        return {"PATH": os.path.dirname(node) + os.pathsep + os.environ.get("PATH", "")}

    def ensure_7z(self):
        """7z 압축을 풀 수 있는 실행 파일. PATH 의 7z > 기본 설치 위치 > 작은 7zr.exe 를 받는다."""
        for name in ("7z", "7za", "7zr"):
            found = shutil.which(name)
            if found:
                return found
        for p in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
            if os.path.isfile(p):
                return p
        if not WIN:
            raise InstallError("7z 를 찾지 못했어요. 7-Zip(p7zip)을 설치한 뒤 다시 시도하세요")
        dest = os.path.join(self.env.tools, "_dl", "7zr.exe")
        if not os.path.isfile(dest):
            self.say("7z 압축 해제 도구(7zr.exe)를 받아요")
            self.download(SEVENZR_URL, dest, "7zr")
        return dest

    def extract_7z(self, archive, dest):
        sevenz = self.ensure_7z()
        os.makedirs(dest, exist_ok=True)
        self.progress(None, "압축 푸는 중… (수 분 걸릴 수 있어요)")
        self.run([sevenz, "x", archive, f"-o{dest}", "-y", "-bso0", "-bsp0"])
        self.progress(None, "")


# ---------------------------------------------------------------- 모듈 정의
def _import_missing(py, modules):
    return procs.missing_modules(py, modules) if py else list(modules)


def _spec_exists(py, name):
    return not _import_missing(py, (name,))


class Module:
    id = ""
    group = "required"      # required | mic | rvc
    title = ""
    desc = ""
    size = ""
    touches = "server"      # 설치 중 서버/클라를 꺼야 하는 대상: server | client | ''

    def check(self, env):
        """(상태, 설명). 상태: ok | missing | partial"""
        raise NotImplementedError

    def install(self, job):
        raise NotImplementedError

    remove_touches = None   # 삭제 중 꺼야 하는 대상 (None 이면 touches 와 같음)

    def uninstall(self, job):
        """설치한 것을 지운다. 사용자가 넣은 데이터(RVC 모델, 서버 설정 등)는 건드리지 않는다."""
        raise InstallError("이 항목은 지우기를 지원하지 않아요")


class ServerPackages(Module):
    id, group, touches = "server", "required", "server"
    title = "서버 패키지"
    desc = "서버용 가상환경(venv)과 requirements.txt, 웹 검색(ddgs) 포함"
    size = "약 150MB"

    def check(self, env):
        if not env.server_dir:
            return "missing", "서버 폴더를 찾지 못했어요 ('런처' 탭에서 지정)"
        py = env.server_python()
        if py:
            return "ok", py
        return "missing", "필수 패키지가 없어요"

    def install(self, job):
        e = job.env
        if not e.server_dir:
            raise InstallError("서버 폴더를 찾지 못했어요. '런처' 탭에서 서버 폴더를 지정하세요")
        req = os.path.join(e.server_dir, "requirements.txt")
        if not os.path.isfile(req):
            raise InstallError(f"requirements.txt 가 없어요: {req}")
        configured = str(e.settings["server_python"] or "").strip()
        py = configured if configured and os.path.isfile(configured) else job.ensure_venv(e.server_venv())
        job.pip(py, "-r", req)
        job.pip(py, "ddgs")

    def uninstall(self, job):
        e = job.env
        if not e.server_dir:
            raise InstallError("서버 폴더를 찾지 못했어요")
        configured = str(e.settings["server_python"] or "").strip()
        venv = e.server_venv()
        if configured and os.path.isfile(configured) and not os.path.realpath(configured).startswith(os.path.realpath(venv) + os.sep):
            raise InstallError(f"직접 지정한 파이썬({configured})을 쓰고 있어서 지우지 않았어요. 설정에서 지정을 비우거나 직접 정리하세요")
        job.remove_dir(venv, [e.server_dir], "서버 가상환경(venv)")


class ClientPackages(Module):
    id, group, touches = "client", "required", "client"
    title = "클라이언트 (Electron)"
    desc = "npm install — Node.js 가 없으면 같이 받아요"
    size = "약 250MB"

    def check(self, env):
        if not env.client_dir:
            return "missing", "클라 폴더를 찾지 못했어요 ('런처' 탭에서 지정)"
        exe = os.path.join(env.client_dir, "node_modules", "electron", "dist", "electron.exe" if WIN else "electron")
        return ("ok", "electron 설치됨") if os.path.isfile(exe) else ("missing", "npm install 이 필요해요")

    def install(self, job):
        e = job.env
        if not e.client_dir:
            raise InstallError("클라 폴더를 찾지 못했어요. '런처' 탭에서 클라 폴더를 지정하세요")
        node, npm = job.ensure_node()
        job.run([npm, "install", "--no-audit", "--no-fund"], cwd=e.client_dir, extra_env=job.node_env(node))

    def uninstall(self, job):
        e = job.env
        if not e.client_dir:
            raise InstallError("클라 폴더를 찾지 못했어요")
        job.remove_dir(os.path.join(e.client_dir, "node_modules"), [e.client_dir], "node_modules")


class MicStt(Module):
    id, group, touches = "mic_stt", "mic", "server"
    title = "음성 인식 (faster-whisper)"
    desc = "마이크로 말해서 대화할 때 필요해요. 인식 모델 파일은 처음 쓸 때 따로 내려받아요 (수백 MB~수 GB)"
    size = "라이브러리 약 100MB"

    def check(self, env):
        py = env.server_target_python()
        if not py:
            return "missing", "먼저 '서버 패키지'를 설치하세요"
        return ("ok", "설치됨") if _spec_exists(py, "faster_whisper") else ("missing", "")

    def install(self, job):
        py = job.env.server_target_python()
        if not py:
            raise InstallError("먼저 '서버 패키지'를 설치하세요")
        job.pip(py, "faster-whisper>=1.0.0")

    def uninstall(self, job):
        py = job.env.server_target_python()
        if not py:
            raise InstallError("서버 파이썬을 찾지 못했어요")
        job.pip_uninstall(py, "faster-whisper")


class MicModels(Module):
    id, group, touches = "mic_models", "mic", "client"
    title = "말 구간 감지(VAD) / 화자 인증 모델"
    desc = "마이크 입력을 정확하게 잡아 줘요. 없으면 음량 방식으로 대신 동작해요"
    size = "약 28MB"

    def check(self, env):
        if not env.client_dir:
            return "missing", "클라 폴더를 찾지 못했어요"
        d = os.path.join(env.client_dir, "voice-models")
        vad = os.path.isfile(os.path.join(d, "silero_vad.onnx"))
        spk = os.path.isfile(os.path.join(d, "speaker.onnx"))
        if vad and spk:
            return "ok", "VAD + 화자 인증"
        if vad:
            return "partial", "VAD 만 있음 (화자 인증 모델 없음)"
        return "missing", ""

    def install(self, job):
        e = job.env
        if not e.client_dir or not os.path.isfile(os.path.join(e.client_dir, "scripts", "get-models.js")):
            raise InstallError("클라 폴더의 scripts/get-models.js 를 찾지 못했어요")
        node, _ = job.ensure_node()
        job.run([node, os.path.join("scripts", "get-models.js")], cwd=e.client_dir, extra_env=job.node_env(node))

    def uninstall(self, job):
        e = job.env
        if not e.client_dir:
            raise InstallError("클라 폴더를 찾지 못했어요")
        for name in ("silero_vad.onnx", "speaker.onnx"):
            job.remove_file(os.path.join(e.client_dir, "voice-models", name))


class MicGpu(Module):
    id, group, touches = "mic_gpu", "mic", "server"
    title = "NVIDIA GPU 가속 (선택)"
    desc = "음성 인식을 GPU 로 돌릴 때 필요한 CUDA 라이브러리 (cuBLAS/cuDNN). GPU 가 없으면 설치하지 마세요"
    size = "약 1GB"

    def check(self, env):
        py = env.server_target_python()
        gpu = "NVIDIA GPU 감지됨" if has_nvidia_gpu() else "NVIDIA GPU 를 찾지 못했어요"
        if py and _spec_exists(py, "nvidia.cublas") and _spec_exists(py, "nvidia.cudnn"):
            return "ok", "설치됨"
        return "missing", gpu

    def install(self, job):
        py = job.env.server_target_python()
        if not py:
            raise InstallError("먼저 '서버 패키지'를 설치하세요")
        job.pip(py, "nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*")

    def uninstall(self, job):
        py = job.env.server_target_python()
        if not py:
            raise InstallError("서버 파이썬을 찾지 못했어요")
        job.pip_uninstall(py, "nvidia-cublas-cu12", "nvidia-cudnn-cu12")


def torch_args(gpu):
    """RVC 용 torch 설치 인자. GPU 가 있으면 CUDA 11.8 용, 없으면 CPU 용."""
    if gpu:
        return [f"torch=={TORCH_VERSION}+cu118", f"torchaudio=={TORCH_VERSION}+cu118", "--index-url", TORCH_CUDA_INDEX]
    return [f"torch=={TORCH_VERSION}", f"torchaudio=={TORCH_VERSION}", "--index-url", TORCH_CPU_INDEX]


class Rvc(Module):
    id, group, touches = "rvc", "rvc", ""
    remove_touches = "server"     # 서버가 RVC 파이썬을 쓰고 있을 수 있다
    title = "RVC (목소리 변환)"
    desc = "Edge 음성을 다른 음색으로 바꿔요. 별도 환경에 rvc-python 과 torch 를 설치해요 (GPU 가 있으면 CUDA 용)"
    size = "약 3GB (GPU) / 약 1GB (CPU)"

    def check(self, env):
        py = env.rvc_python()
        if not os.path.isfile(py):
            return "missing", ""
        miss = _import_missing(py, ("rvc_python", "torch"))
        if not miss:
            return "ok", py
        return "partial", "없는 모듈: " + ", ".join(miss)

    def install(self, job):
        e = job.env
        py = e.rvc_python()
        if not os.path.isfile(py):
            py = job.ensure_venv(e.rvc_venv)
        gpu = has_nvidia_gpu()
        job.say("NVIDIA GPU 감지: CUDA 용 torch 를 설치해요" if gpu else "NVIDIA GPU 가 없어서 CPU 용 torch 를 설치해요 (변환이 느릴 수 있어요)")
        os.makedirs(os.path.join(e.tools, "rvc", "rvc_models"), exist_ok=True)   # 모델(.pth, .index)을 넣을 폴더
        # rvc-python 이 요구하는 omegaconf==2.0.6 의 메타데이터가 pip 24.1 이상에서는 거부돼서 설치가 안 된다 -> pip 를 낮춘다
        job.say("rvc-python 은 오래된 패키지(omegaconf 2.0.6)를 써서 pip 를 24.0 으로 맞춰요")
        job.pip(py, "pip==24.0")
        job.pip(py, "rvc-python")
        job.pip(py, *torch_args(gpu))   # rvc-python 이 끌고 온 torch 를 지정 버전으로 맞춘다
        rvc_root = os.path.join(e.tools, "rvc")
        if e.set_config:
            try:
                import shutil
                cur = e.server_config().cfg_or_empty() if e.server_config else {}
                launch = (((cur.get("tts") or {}).get("rvc") or {}).get("launch") or {})
                have_py, have_dir = str(launch.get("python") or ""), str(launch.get("dir") or "")
                # 비었거나 가리키는 곳이 없으면(예: 설정 예시의 C:/rvc) 이번에 설치한 곳으로 바꾼다
                if not (have_py and (os.path.isfile(have_py) or shutil.which(have_py))):
                    e.set_config("tts.rvc.launch.python", py)
                    job.say(f"서버 설정에 RVC 파이썬을 적어 뒀어요: {py}")
                if not (have_dir and os.path.isdir(have_dir)):
                    e.set_config("tts.rvc.launch.dir", rvc_root)
                    job.say(f"서버 설정에 RVC 폴더를 적어 뒀어요: {rvc_root}")
            except Exception as ex:  # noqa: BLE001  설정 저장 실패는 설치 실패가 아니다
                job.say(f"서버 설정에 RVC 경로를 적지 못했어요 ({ex}). '서버 설정' 탭에 직접 넣으세요 — RVC 파이썬: {py} / RVC 폴더: {rvc_root}")
        job.say(f"RVC 모델은 {os.path.join(rvc_root, 'rvc_models')} 안에 모델 폴더를 만들어 넣으세요 (.pth, 있으면 .index). 그다음 서버 설정 탭에서 모델을 고르세요")

    def uninstall(self, job):
        e = job.env
        venv = e.rvc_venv
        py = e.rvc_python()
        if os.path.isfile(py) and not os.path.realpath(py).startswith(os.path.realpath(venv) + os.sep):
            raise InstallError(f"서버 설정이 직접 지정한 RVC 파이썬({py})을 쓰고 있어서 지우지 않았어요")
        job.remove_dir(venv, [e.tools], "RVC 가상환경 (rvc_models 폴더의 모델은 그대로 둬요)")
        job.say("서버 설정의 RVC 경로는 그대로예요. RVC 를 안 쓸 거면 서버 설정에서 꺼 두세요")


def tyc_member_name(info):
    """zip 안의 이름을 읽을 수 있는 글자로. 일본어 윈도우에서 만든 zip 은 UTF-8 표시가 없고 CP932 라서 깨져 보인다."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp932")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def tyc_safe(name):
    """상위 폴더로 나가거나 절대경로인 항목은 쓰지 않는다."""
    n = name.replace("\\", "/")
    if n.startswith("/") or (len(n) > 1 and n[1] == ":"):
        return False
    return ".." not in n.split("/") and not n.startswith("__MACOSX/")


def tyc_pick_model(z):
    """공식 zip 에서 (모델 .pth, 짝인 .index 또는 None, 전체 .pth 이름 목록)을 고른다. 通常1 이 있으면 그것, 없으면 이름순 첫 번째."""
    entries = [(i, tyc_member_name(i)) for i in z.infolist() if not i.is_dir()]
    entries = [(i, n) for i, n in entries if tyc_safe(n)]
    pths = sorted([x for x in entries if x[1].lower().endswith(".pth")], key=lambda x: x[1])
    if not pths:
        return None, None, []
    pref = [x for x in pths if any(k in x[1] for k in TYC_PREFERRED)]
    pth = (pref or pths)[0]
    idx = [x for x in entries if x[1].lower().endswith(".index")]
    folder = pth[1].rsplit("/", 1)[0] if "/" in pth[1] else ""
    stem = pth[1].rsplit("/", 1)[-1][:-4].lower()
    pick = [x for x in idx if x[1].rsplit("/", 1)[-1].lower().replace("added_", "").startswith(stem)]
    if len(pick) != 1:
        pick = [x for x in idx if (x[1].rsplit("/", 1)[0] if "/" in x[1] else "") == folder]
    if len(pick) != 1 and len(idx) == 1:
        pick = idx
    return pth, (pick[0] if len(pick) == 1 else None), [x[1] for x in pths]


class RvcTsukuyomi(Module):
    id, group, touches = "rvc_tsukuyomi", "rvc", ""
    remove_touches = "server"
    title = "└ 츠쿠요미짱 공식 RVC 모델 (옵션)"
    desc = ("RVC 에서 쓸 음색 모델이에요. 위 RVC 를 설치해도 이건 자동으로 받지 않아요 — 다른 모델을 쓸 거면 설치하지 마세요. "
            "공식 배포처(tyc.rei-yumesaki.net)에서 받아 .pth 와 .index 만 rvc_models/tsukuyomi 에 넣어요. "
            "쓰기 전에 이용 약관(변환 전 음성·모델 크레딧 표기, 정치/종교 활동 금지 등)을 꼭 읽어 주세요: " + TYC_RVC_TERMS)
    size = "공식 zip 을 받아 필요한 것만 꺼내요"

    def check(self, env):
        d = os.path.join(env.rvc_models_dir(), TYC_MODEL_NAME)
        if os.path.isdir(d) and any(f.lower().endswith(".pth") for f in os.listdir(d)):
            return "ok", d
        return "missing", ""

    def install(self, job):
        e = job.env
        dest = os.path.join(e.rvc_models_dir(), TYC_MODEL_NAME)
        if self.check(e)[0] == "ok":
            job.say(f"이미 있어요: {dest}\n(다른 모델로 바꾸려면 이 폴더를 직접 지운 뒤 설치하세요)")
            return
        arc = job.download(TYC_RVC_URL, os.path.join(e.tools, "_dl", "tyc-rvc.zip"), "츠쿠요미짱 RVC 모델")
        tmp = dest + ".part"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            if not zipfile.is_zipfile(arc):
                raise InstallError(f"받은 파일이 zip 이 아니에요 (주소가 바뀌었을 수 있어요). 공식 페이지에서 직접 받아 주세요: {TYC_RVC_PAGE}")
            with zipfile.ZipFile(arc) as z:
                pth, idx, names = tyc_pick_model(z)
                if not pth:
                    raise InstallError(f".pth 파일을 찾지 못했어요. 공식 페이지에서 구성이 바뀌었는지 확인하세요: {TYC_RVC_PAGE}")
                job.say("들어 있는 모델: " + ", ".join(names))
                job.say(f"고른 모델: {pth[1]}  (다른 걸 쓰려면 공식 zip 에서 직접 꺼내 rvc_models 에 넣으세요)")
                os.makedirs(tmp, exist_ok=True)
                # 파일 이름은 영문으로 통일한다 (한/일본어 경로가 RVC 쪽에서 말썽을 내는 걸 피함). 내용은 그대로.
                items = [(pth[0], TYC_MODEL_NAME + ".pth")] + ([(idx[0], TYC_MODEL_NAME + ".index")] if idx else [])
                for info, out in items:
                    if info.file_size > TYC_MAX_MEMBER:
                        raise InstallError(f"압축 안의 파일이 너무 커요: {info.filename}")
                    with z.open(info) as src, open(os.path.join(tmp, out), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                if not idx:
                    job.say("짝이 되는 .index 를 찾지 못했어요. 없어도 동작은 해요")
                with open(os.path.join(tmp, "SOURCE.txt"), "w", encoding="utf-8") as f:
                    f.write("츠쿠요미짱 공식 RVC 모델 (CV.夢前黎, © Rei Yumesaki)\n"
                            f"받은 곳: {TYC_RVC_URL}\n배포 페이지: {TYC_RVC_PAGE}\n이용 약관: {TYC_RVC_TERMS}\n"
                            f"고른 모델: {pth[1]}\n파일 이름만 바꿨고 내용은 그대로입니다.\n")
            shutil.rmtree(dest, ignore_errors=True)   # .pth 없이 남은 빈 폴더
            os.replace(tmp, dest)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        try:
            os.remove(arc)
        except OSError:
            pass
        job.say(f"넣었어요: {dest}")
        job.say(f"서버 설정은 바꾸지 않았어요. 쓰려면 '서버 설정' 탭에서 RVC 모델을 '{TYC_MODEL_NAME}' 로 고르세요")
        job.say(f"공개하는 영상/작품에는 '변환 전 음성'과 '츠쿠요미짱 공식 RVC 모델' 크레딧이 필요해요. 약관: {TYC_RVC_TERMS}")

    def uninstall(self, job):
        e = job.env
        models = e.rvc_models_dir()
        job.remove_dir(os.path.join(models, TYC_MODEL_NAME), [models], f"rvc_models/{TYC_MODEL_NAME}")
        job.remove_file(os.path.join(e.tools, "_dl", "tyc-rvc.zip"))


class Sovits(Module):
    id, group, touches = "sovits", "sovits", ""
    remove_touches = "server"
    title = "GPT-SoVITS (기본 모델)"
    desc = ("목소리 복제 TTS 엔진 (공식 윈도우 통합 패키지: 자체 파이썬과 기본 사전학습 모델 포함). 목소리 모델은 들어 있지 않아서 "
            "참조 음성(5~10초 wav)과 그 대사 글자를 서버 설정에 직접 적어야 해요. 디스크 여유 25GB 이상 필요")
    size = "받기 약 8GB (푼 뒤 더 큼)"

    @staticmethod
    def _runtime_py(env):
        return os.path.join(env.sovits_dir, "runtime", "python.exe" if WIN else "python")

    def _check_files(self, env):
        d = env.sovits_dir
        if not os.path.isdir(d):
            return "missing", ""
        has_api = os.path.isfile(os.path.join(d, "api_v2.py"))
        has_py = os.path.isfile(self._runtime_py(env))
        has_cfg = os.path.isfile(os.path.join(d, "GPT_SoVITS", "configs", "tts_infer.yaml"))
        if has_api and has_py and has_cfg:
            return "ok", d
        return "partial", "폴더는 있는데 필요한 파일이 빠져 있어요 (지우고 다시 설치하세요)"

    def check(self, env):
        state, detail = self._check_files(env)
        # 한국어 TTS 는 eunjeon 이 GPT-SoVITS 자체 파이썬(runtime)에 있어야 한다. 없으면 /tts 가 400 으로 실패한다
        if state == "ok" and WIN and _import_missing(self._runtime_py(env), ("eunjeon",)):
            return "partial", "한국어 발음 패키지(eunjeon)가 없어요 — '이어서 설치'를 누르면 받아요 (8GB 는 다시 받지 않아요)"
        return state, detail

    def _ensure_eunjeon(self, job):
        """GPT-SoVITS 의 runtime 파이썬에 eunjeon 설치. GPT-SoVITS 가 스스로 'pip install' 을 해도 PATH 의 다른 파이썬에 깔려서 소용없다."""
        e = job.env
        py = self._runtime_py(e)
        if not WIN:
            return
        if not _import_missing(py, ("eunjeon",)):
            job.say("한국어 발음 패키지(eunjeon)는 이미 있어요")
            return
        job.say("한국어 TTS 에 필요한 eunjeon 을 GPT-SoVITS 의 파이썬(runtime)에 설치해요")
        try:
            job.pip(py, "eunjeon")
        except InstallError as ex:
            raise InstallError(f"eunjeon 설치에 실패했어요. 'Microsoft Visual C++ 14.0 required' 같은 빌드 오류면 "
                               f"Visual Studio Build Tools 의 'C++를 사용한 데스크톱 개발'을 설치한 뒤 다시 시도하세요.\n{ex}") from None
        if _import_missing(py, ("eunjeon",)):
            raise InstallError("eunjeon 을 설치했는데도 GPT-SoVITS 의 파이썬에서 찾지 못했어요")

    def install(self, job):
        e = job.env
        if not WIN:
            raise InstallError("GPT-SoVITS 통합 패키지는 윈도우용이에요. 다른 OS 는 GPT-SoVITS 를 직접 설치한 뒤 서버 설정의 tts.launch.dir 에 경로를 적으세요")
        dest = e.sovits_dir
        if self._check_files(e)[0] == "ok":
            # 사용자가 넣은 참조 음성/학습한 모델이 지워지면 안 되니, 이미 있으면 받지 않고 설정만 확인한다
            job.say(f"이미 설치돼 있어요: {dest}\n(처음부터 다시 받으려면 이 폴더를 직접 지운 뒤 설치하세요)")
        else:
            os.makedirs(e.tools, exist_ok=True)
            free = shutil.disk_usage(e.tools).free / 1e9
            if free < SOVITS_MIN_FREE_GB:
                raise InstallError(f"디스크 여유 공간이 부족해요 ({free:.0f}GB). {SOVITS_MIN_FREE_GB}GB 이상 필요해요 ({e.tools})")
            names = gpu_names()
            rtx50 = is_rtx50(names)
            job.say(f"GPU: {', '.join(names) if names else '없음'}" + (" → RTX 50 시리즈용 패키지를 받아요" if rtx50 else ""))
            try:
                info = requests.get(SOVITS_API, timeout=30, headers=UA).json()
                files = [x.get("rfilename", "") for x in info.get("siblings") or []]
            except (requests.RequestException, ValueError) as ex:
                raise InstallError(f"GPT-SoVITS 패키지 목록을 가져오지 못했어요: {ex}") from None
            name = pick_sovits_asset(files, rtx50)
            if not name:
                raise InstallError("받을 수 있는 통합 패키지를 찾지 못했어요. "
                                   f"https://huggingface.co/{SOVITS_REPO} 에서 직접 받아 tools/gptsovits 에 풀어 주세요")
            job.say(f"받을 파일: {name} (약 8GB, 끊겨도 다시 설치하면 이어서 받아요)")
            arc = job.download(SOVITS_FILE_URL.format(name=name), os.path.join(e.tools, "_dl", name), "GPT-SoVITS", resume=True)
            tmp = os.path.join(e.tools, "_sovits_extract")
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                job.extract_7z(arc, tmp)
                root = find_sovits_root(tmp)
                if not root:
                    raise InstallError("압축 안에서 api_v2.py 를 찾지 못했어요")
                shutil.rmtree(dest, ignore_errors=True)   # 불완전하게 남은 폴더
                os.replace(root, dest)
            except Cancelled:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            except Exception:
                shutil.rmtree(tmp, ignore_errors=True)   # 받은 7z 는 남겨서 다음에 풀기만 하면 되게 한다
                raise
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                os.remove(arc)
            except OSError:
                pass
        self._ensure_eunjeon(job)
        self._write_config(job, dest)

    def uninstall(self, job):
        e = job.env
        job.remove_dir(e.sovits_dir, [e.tools], "GPT-SoVITS (직접 넣은 참조 음성/학습 모델도 함께 지워져요)")
        job.remove_dir(os.path.join(e.tools, "_sovits_extract"), [e.tools])
        for f in os.listdir(os.path.join(e.tools, "_dl")) if os.path.isdir(os.path.join(e.tools, "_dl")) else []:
            if f.startswith("GPT-SoVITS-"):
                job.remove_file(os.path.join(e.tools, "_dl", f))
        job.say("서버 설정의 tts.launch.dir 는 그대로예요. 쓰지 않으면 tts.launch.enabled 를 끄세요")

    def _write_config(self, job, dest):
        e = job.env
        if not e.set_config:
            job.say(f"'서버 설정' 탭에서 tts.launch.dir 에 이 폴더를 넣으세요: {dest}")
            return
        try:
            cur = e.server_config().cfg_or_empty() if e.server_config else {}
            have = str((((cur.get("tts") or {}).get("launch") or {}).get("dir")) or "")
            if not (have and os.path.isdir(have)):   # 비었거나 가리키는 곳이 없으면 이번에 설치한 곳으로
                e.set_config("tts.launch.dir", dest)
                job.say(f"서버 설정에 GPT-SoVITS 폴더를 적어 뒀어요: {dest}")
        except Exception as ex:  # noqa: BLE001  설정 저장 실패는 설치 실패가 아니다
            job.say(f"서버 설정에 경로를 적지 못했어요 ({ex}). '서버 설정' 탭에서 tts.launch.dir 에 직접 넣으세요: {dest}")
        job.say("쓰려면 서버 설정에서: tts.provider = gptsovits, tts.launch.enabled = true, 참조 음성(ref_audio_path)과 그 대사(prompt_text)를 지정하세요")


MODULES = [ServerPackages(), ClientPackages(), MicStt(), MicModels(), MicGpu(), Rvc(), RvcTsukuyomi(), Sovits()]
GROUPS = [("required", "필수"), ("mic", "선택 · 마이크"), ("rvc", "선택 · RVC"), ("sovits", "선택 · GPT-SoVITS")]


# ---------------------------------------------------------------- 매니저
class InstallManager:
    def __init__(self, env, busy_check=None, modules=None):
        """busy_check(module) -> 설치할 수 없는 이유(글자) 또는 ''  (서버/클라가 켜져 있으면 파일이 잠겨서)."""
        self.env = env
        self.modules = modules or MODULES
        self.busy_check = busy_check
        self.status = {m.id: ("unknown", "확인 중…") for m in self.modules}
        self.lines = []            # [(seq, text)]
        self._seq = 0
        self.progress_text = ""
        self.progress_frac = None
        self.current = None        # 설치 중인 모듈 id
        self.queue = []
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._checking = False

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def say(self, text):
        with self._lock:
            self._seq += 1
            self.lines.append((self._seq, text))
            del self.lines[:-2000]

    def since(self, seq):
        with self._lock:
            return [x for x in self.lines if x[0] > seq]

    def _progress(self, frac, text):
        self.progress_frac, self.progress_text = frac, text

    def by_id(self, mid):
        return next(m for m in self.modules if m.id == mid)

    # ----- 상태 확인 (느릴 수 있어서 스레드에서)
    def refresh(self, wait=False):
        if self._checking:
            return
        self._checking = True

        def work():
            try:
                for m in self.modules:
                    try:
                        self.status[m.id] = m.check(self.env)
                    except Exception as e:  # noqa: BLE001
                        self.status[m.id] = ("missing", f"확인 중 오류: {e}")
            finally:
                self._checking = False

        t = threading.Thread(target=work, name="install-check", daemon=True)
        t.start()
        if wait:
            t.join()

    # ----- 설치
    def start(self, ids):
        """ids 를 차례로 설치한다. 시작했으면 ''. 못 시작하면 이유."""
        if self.running:
            return "이미 설치 중이에요"
        mods = [self.by_id(i) for i in ids]
        for m in mods:
            why = self.busy_check(m) if self.busy_check else ""
            if why:
                return why
        self._cancel.clear()
        self.queue = [m.id for m in mods]
        self._thread = threading.Thread(target=self._run, args=(mods,), name="install", daemon=True)
        self._thread.start()
        return ""

    def start_uninstall(self, ids):
        """ids 를 차례로 지운다. 시작했으면 ''. 못 시작하면 이유."""
        if self.running:
            return "이미 작업 중이에요"
        mods = [self.by_id(i) for i in ids]
        for m in mods:
            why = self.busy_check(m, removing=True) if self.busy_check else ""
            if why:
                return why
        self._cancel.clear()
        self.queue = [m.id for m in mods]
        self._thread = threading.Thread(target=self._run_uninstall, args=(mods,), name="uninstall", daemon=True)
        self._thread.start()
        return ""

    def cancel(self):
        self._cancel.set()

    def _run_uninstall(self, mods):
        job = Job(self.env, self.say, self._progress, self._cancel.is_set)
        done, failed = [], []
        for m in mods:
            if self._cancel.is_set():
                break
            self.current = m.id
            self.say(f"===== {m.title} 지우기 시작 =====")
            try:
                m.uninstall(job)
                self.status[m.id] = m.check(self.env)
                self.say(f"✔ {m.title} 지웠어요")
                done.append(m.title)
            except Cancelled:
                self.say("✖ 취소했어요")
                break
            except InstallError as e:
                self.say(f"✖ {m.title} 지우기 실패: {e}")
                failed.append(m.title)
            except Exception as e:  # noqa: BLE001
                self.say(f"✖ {m.title} 지우는 중 오류: {e!r}")
                failed.append(m.title)
        self.current = None
        self.queue = []
        self._progress(None, "")
        self.say("끝났어요. " + (f"지움 {len(done)}개" if done else "") + (f", 실패 {len(failed)}개 ({', '.join(failed)})" if failed else ""))
        self.refresh(wait=True)

    def _run(self, mods):
        job = Job(self.env, self.say, self._progress, self._cancel.is_set)
        done, failed = [], []
        for m in mods:
            if self._cancel.is_set():
                break
            self.current = m.id
            self.say(f"===== {m.title} 설치 시작 =====")
            try:
                m.install(job)
                self.status[m.id] = m.check(self.env)
                if self.status[m.id][0] == "ok" or m.id in ("mic_models",):
                    self.say(f"✔ {m.title} 설치 완료")
                    done.append(m.title)
                else:
                    self.say(f"⚠ {m.title}: 설치는 끝났지만 확인에서 문제가 있어요 — {self.status[m.id][1]}")
                    failed.append(m.title)
            except Cancelled:
                self.say("✖ 취소했어요")
                break
            except InstallError as e:
                self.say(f"✖ {m.title} 설치 실패: {e}")
                failed.append(m.title)
            except Exception as e:  # noqa: BLE001
                self.say(f"✖ {m.title} 설치 중 오류: {e!r}")
                failed.append(m.title)
        self.current = None
        self.queue = []
        self._progress(None, "")
        self.say("끝났어요. " + (f"완료 {len(done)}개" if done else "") + (f", 실패 {len(failed)}개 ({', '.join(failed)})" if failed else ""))
        self.refresh(wait=True)
