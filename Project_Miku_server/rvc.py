"""RVC(목소리 변환) 후처리: Edge TTS 가 만든 음성을 미쿠 같은 다른 음색으로 바꾼다.

rvc-python 의 API 서버(`python -m rvc_python api`)를 따로 켜 두고 HTTP 로 부른다.
서버가 RVC 를 같이 켜고 끄는 것도 맡는다 (GPT-SoVITS 자동 실행과 같은 방식, tts_launcher.Launcher).
RVC 는 음색만 바꾸고 말투/억양/속도는 Edge 음성의 것을 그대로 가져간다 (그래서 edge.emotions 의 감정 표현이 유지됨).

적용 조건 (모두 만족): tts.enabled, tts.provider == edge, tts.rvc.enabled == true
변환이 실패하면 원래 Edge 음성으로 대화를 이어간다 (tts.rvc.fallback: false 면 음성 없이 글자만).
"""
import base64
import os
import shutil
import threading
from urllib.parse import quote, urlparse

import requests

import tts_launcher as SL

DEFAULT_URL = "http://127.0.0.1:5050"   # rvc-python api 의 기본 포트
DEFAULT_PARAMS = {
    "f0method": "rmvpe",     # harvest | crepe | rmvpe | pm
    "f0up_key": 0,           # 반음 단위 음높이 이동 (tts.rvc.pitch 로도 지정 가능)
    "index_rate": 0.5,
    "filter_radius": 3,
    "resample_sr": 0,
    "rms_mix_rate": 0.25,
    "protect": 0.33,
}


def rvc_cfg(tts_cfg):
    return (tts_cfg or {}).get("rvc") or {}


def active(tts_cfg):
    """RVC 변환을 적용하는가. 기본은 꺼짐 (설치/설정이 필요하므로 켜는 쪽이 명시해야 한다)."""
    tts_cfg = tts_cfg or {}
    if not tts_cfg.get("enabled", True):
        return False
    if str(tts_cfg.get("provider") or "gptsovits").strip().lower() != "edge":
        return False
    return rvc_cfg(tts_cfg).get("enabled") is True


def url_of(tts_cfg):
    return (rvc_cfg(tts_cfg).get("url") or DEFAULT_URL).rstrip("/")


def build_params(rvc):
    p = dict(DEFAULT_PARAMS)
    if rvc.get("pitch") is not None:
        p["f0up_key"] = rvc["pitch"]
    p.update(rvc.get("params") or {})
    return p


# ---------------------------------------------------------------- 자동 실행
def should_launch(tts_cfg):
    return active(tts_cfg) and (rvc_cfg(tts_cfg).get("launch") or {}).get("enabled", True) is not False


def build_command(tts_cfg):
    """실행 명령과 작업 폴더. 설정이 잘못됐으면 ValueError (메시지는 사용자에게 보여 준다).
    rvc-python 은 작업 폴더의 rvc_models/<모델 이름>/ 에서 모델(.pth, .index)을 찾는다."""
    rvc = rvc_cfg(tts_cfg)
    launch = rvc.get("launch") or {}
    directory = launch.get("dir") or ""
    if not directory:
        raise ValueError("tts.rvc.launch.dir 가 비어 있어요 (rvc_models 폴더가 들어 있는 폴더 경로)")
    directory = os.path.normpath(directory)
    if not os.path.isdir(directory):
        raise ValueError(f"tts.rvc.launch.dir 폴더가 없어요: {directory}")

    model = rvc.get("model")
    if model and not os.path.isdir(os.path.join(directory, "rvc_models", str(model))):
        raise ValueError(f"모델 폴더가 없어요: {os.path.join(directory, 'rvc_models', str(model))} (안에 .pth 파일이 있어야 해요)")

    cmd = launch.get("command")
    if cmd:
        if isinstance(cmd, str):
            raise ValueError("tts.rvc.launch.command 는 문자열이 아니라 목록이어야 해요")
        return [str(c) for c in cmd], directory

    py = launch.get("python") or ""
    if not py:
        raise ValueError("tts.rvc.launch.python 이 비어 있어요 (RVC 를 설치한 가상환경의 python.exe 경로)")
    if not os.path.isfile(py):
        found = shutil.which(py)
        if not found:
            raise ValueError(f"tts.rvc.launch.python 을 찾을 수 없어요: {py}")
        py = found
    port = urlparse(url_of(tts_cfg)).port or 5050
    return [py, "-m", "rvc_python", "api", "-p", str(port)], directory


SPEC = SL.Spec("rvc", "RVC", should_launch, url_of, lambda c: rvc_cfg(c).get("launch") or {}, build_command,
               default_wait=120.0, default_log="rvc.log")


def make_launcher(tts_cfg, base_dir="."):
    """서버가 켜질 때 RVC 도 켜고, 준비되면 모델/파라미터를 설정한다."""
    return SL.Launcher(tts_cfg, base_dir, spec=SPEC, on_ready=lambda: get_client(tts_cfg).setup())


# ---------------------------------------------------------------- 변환 요청
class RvcClient:
    def __init__(self, tts_cfg, post=requests.post):
        self.rvc = rvc_cfg(tts_cfg)
        self.base = url_of(tts_cfg)
        self.post = post
        self.timeout = float(self.rvc.get("timeout", 30))
        self.ok = False                  # 모델/파라미터를 서비스에 설정해 뒀는가 (서비스가 다시 켜지면 다시 설정한다)
        self._lock = threading.Lock()

    def _call(self, path, payload, timeout):
        r = self.post(self.base + path, json=payload, timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f"{path} -> HTTP {r.status_code}: {str(getattr(r, 'text', ''))[:200]}")
        return r

    def setup(self):
        """모델 불러오기 / 장치 / 파라미터 설정. 성공하면 True, 서비스가 아직 없거나 실패하면 예외."""
        with self._lock:
            # 장치를 먼저 정한다: 모델을 CPU 로 불러온 뒤에 바꾸면 모델이 CPU 에 남아 느릴 수 있다
            if self.rvc.get("device"):
                self._call("/set_device", {"device": str(self.rvc["device"])}, self.timeout)
            model = self.rvc.get("model")
            if model:
                self._call(f"/models/{quote(str(model), safe='')}", None, max(self.timeout, 120))   # 모델 불러오기는 오래 걸릴 수 있다
            self._call("/params", {"params": build_params(self.rvc)}, self.timeout)
            self.ok = True
            return True

    def convert(self, wav_bytes):
        """변환된 wav 바이트. 실패하면 None (호출한 쪽이 원래 음성으로 대신한다)."""
        try:
            if not self.ok:
                self.setup()
            r = self._call("/convert", {"audio_data": base64.b64encode(wav_bytes).decode("ascii")}, self.timeout)
            if not r.content[:4] == b"RIFF":
                raise RuntimeError("변환 결과가 wav 가 아니에요")
            return r.content
        except requests.ConnectionError:
            self.ok = False
            print(f"[rvc] RVC 에 연결하지 못했어요 ({self.base}). 아직 켜지는 중일 수 있어요. 원래 Edge 음성으로 대신해요.")
            return None
        except (requests.RequestException, RuntimeError) as e:
            self.ok = False   # 서비스가 재시작됐을 수 있으니 다음에 다시 설정한다
            print(f"[rvc] 변환 실패 (원래 Edge 음성으로 대신해요): {e}")
            return None


_clients = {}
_clients_lock = threading.Lock()


def get_client(tts_cfg):
    key = (url_of(tts_cfg), str(rvc_cfg(tts_cfg).get("model")))
    with _clients_lock:
        if key not in _clients:
            _clients[key] = RvcClient(tts_cfg)
        return _clients[key]
