"""음성 인식(STT): faster-whisper 를 서버 안에서 직접 돌린다 (따로 STT 서버를 켤 필요 없음).

    t = Transcriber(cfg, BASE)         # cfg["stt"] 를 읽는다 (아래 DEFAULTS)
    t.available() -> (True, "") | (False, "이유")
    t.transcribe(wav_bytes, language=None, prompt=None) -> {"text", "language", "duration", "elapsed"}
    t.preload()                        # 서버를 켤 때 백그라운드로 모델을 미리 불러온다 (첫 인식이 느려지지 않게)

- 모델은 처음 쓸 때(또는 preload 때) 불러온다. 처음 한 번은 모델을 내려받느라 오래 걸릴 수 있다 (small 은 약 460MB).
- device 가 auto 면 GPU(CUDA)가 있으면 쓰고 없으면 CPU. GPU 를 잡았는데 cuBLAS/cuDNN 같은 라이브러리가 없어서 실패하면
  자동으로 CPU(int8) 로 바꿔서 다시 시도한다 (로그에 이유가 남는다).
- 설정(stt.model/device/compute_type 등)이 바뀌면 다음 인식 때 모델을 다시 불러온다.
"""
import io
import os
import threading
import time
import wave

DEFAULTS = {
    "enabled": True,
    "model": "small",          # tiny | base | small | medium | large-v3 | large-v3-turbo | distil-large-v3, 또는 모델 폴더 경로
    "device": "auto",          # auto | cpu | cuda
    "compute_type": "auto",    # auto | int8 | int8_float16 | float16 | float32
    "language": "ko",          # 비우면 자동 감지
    "beam_size": 1,
    "prompt": "",              # 자주 나오는 이름/용어를 알려 주면 인식이 좋아진다 (예: "미쿠, 하츠네")
    "preload": True,
    "cpu_threads": 0,          # 0 = 기본값
    "download_root": "",       # 모델을 저장할 폴더 (비우면 기본 캐시)
}

# GPU 라이브러리가 없을 때 나오는 오류 메시지의 흔한 조각들 -> CPU 로 전환
_GPU_ERRORS = ("cublas", "cudnn", "cudart", "cuda", "nvrtc", "dll", "libcu", "no kernel image", "out of memory")


class SttError(Exception):
    """사용자에게 그대로 보여 줘도 되는 메시지를 가진 오류."""


def settings(cfg):
    s = dict(DEFAULTS)
    raw = (cfg or {}).get("stt")
    if isinstance(raw, dict):
        for k in DEFAULTS:
            if k in raw and raw[k] is not None:
                s[k] = raw[k]
    s["enabled"] = s["enabled"] is not False
    s["preload"] = s["preload"] is not False
    for k in ("beam_size", "cpu_threads"):
        try:
            s[k] = max(0, int(s[k]))
        except (TypeError, ValueError):
            s[k] = DEFAULTS[k]
    s["beam_size"] = max(1, s["beam_size"])
    for k in ("model", "device", "compute_type", "language", "prompt", "download_root"):
        s[k] = str(s[k] or "").strip()
    s["model"] = s["model"] or DEFAULTS["model"]
    s["device"] = s["device"] if s["device"] in ("auto", "cpu", "cuda") else "auto"
    s["compute_type"] = s["compute_type"] or "auto"
    return s


def _add_nvidia_dll_dirs():
    """Windows: pip 로 설치한 nvidia-cublas-cu12 / nvidia-cudnn-cu12 의 DLL 폴더를 찾아 등록한다 (없으면 아무 일도 안 함)."""
    if os.name != "nt":
        return []
    added = []
    try:
        import importlib.util
        spec = importlib.util.find_spec("nvidia")
        for loc in (list(spec.submodule_search_locations) if spec and spec.submodule_search_locations else []):
            for sub in os.listdir(loc):
                d = os.path.join(loc, sub, "bin")
                if os.path.isdir(d):
                    os.add_dll_directory(d)
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                    added.append(d)
    except Exception:  # noqa: BLE001 - 보조 기능: 실패해도 모델 로딩은 계속 시도한다
        pass
    return added


def _is_gpu_error(e):
    msg = str(e).lower()
    return any(w in msg for w in _GPU_ERRORS)


def decode_wav(data):
    """16bit PCM WAV 바이트 -> (float32 모노 16kHz numpy 배열). WAV 가 아니거나 형식이 다르면 None."""
    try:
        import numpy as np
        with wave.open(io.BytesIO(data), "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if width != 2 or ch < 1 or rate < 8000:
            return None
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if ch > 1:
            x = x[: (len(x) // ch) * ch].reshape(-1, ch).mean(axis=1)
        if rate != 16000 and len(x) > 1:
            t_new = np.arange(int(len(x) * 16000 / rate)) * (rate / 16000)
            x = np.interp(t_new, np.arange(len(x)), x).astype(np.float32)
        return x
    except Exception:  # noqa: BLE001
        return None


class Transcriber:
    def __init__(self, cfg, base_dir="", model_factory=None, log=print):
        self.cfg = cfg
        self.base_dir = base_dir
        self._factory = model_factory
        self.log = log
        self._model = None
        self._key = None
        self._force_cpu = False
        self._load_lock = threading.RLock()
        self._run_lock = threading.Lock()      # 모델은 한 번에 하나씩만 돌린다
        self.loading = False
        self.last_error = ""

    # ---------- 상태 ----------
    def available(self):
        s = settings(self.cfg)
        if not s["enabled"]:
            return False, "서버의 음성 인식이 꺼져 있어요 (config.yaml 의 stt.enabled)"
        if self._factory is None:
            try:
                import faster_whisper  # noqa: F401
            except ImportError:
                return False, "음성 인식을 쓰려면 서버에서 `pip install faster-whisper` 를 실행하세요"
        return True, ""

    @property
    def ready(self):
        return self._model is not None

    # ---------- 모델 ----------
    def _resolve_model(self, s):
        m = s["model"]
        local = os.path.join(self.base_dir, m) if self.base_dir and not os.path.isabs(m) else m
        return local if os.path.isdir(local) else m

    def _device_args(self, s):
        device, compute = s["device"], s["compute_type"]
        if self._force_cpu:
            device = "cpu"
            if compute in ("auto", "float16", "int8_float16"):
                compute = "int8"
        return device, compute

    def _make(self, s, device, compute):
        if self._factory is not None:
            return self._factory(s, device, compute)
        if device in ("auto", "cuda"):
            _add_nvidia_dll_dirs()
        from faster_whisper import WhisperModel
        kw = {"device": device, "compute_type": compute}
        if s["cpu_threads"]:
            kw["cpu_threads"] = s["cpu_threads"]
        if s["download_root"]:
            kw["download_root"] = s["download_root"]
        return WhisperModel(self._resolve_model(s), **kw)

    def _get_model(self):
        s = settings(self.cfg)
        with self._load_lock:
            device, compute = self._device_args(s)
            key = (s["model"], device, compute, s["cpu_threads"], s["download_root"])
            if self._model is not None and key == self._key:
                return self._model
            self.loading = True
            try:
                self.log(f"[stt] 음성 인식 모델을 불러오는 중… (model={s['model']}, device={device}, compute={compute})")
                t0 = time.time()
                try:
                    self._model = self._make(s, device, compute)
                except Exception as e:  # noqa: BLE001
                    if device != "cpu" and _is_gpu_error(e):
                        self.log(f"[stt] GPU 를 쓰지 못해 CPU 로 전환합니다: {e}")
                        self._force_cpu = True
                        device, compute = self._device_args(s)
                        key = (s["model"], device, compute, s["cpu_threads"], s["download_root"])
                        self._model = self._make(s, device, compute)
                    else:
                        raise
                self._key = key
                self.last_error = ""
                self.log(f"[stt] 준비 완료 ({time.time() - t0:.1f}초, device={device})")
                return self._model
            except Exception as e:  # noqa: BLE001
                self._model = None
                self.last_error = f"음성 인식 모델을 불러오지 못했어요: {e}"
                raise SttError(self.last_error) from e
            finally:
                self.loading = False

    def preload(self, background=True):
        """서버를 켤 때 호출: 모델을 미리 불러와서 첫 인식이 느려지지 않게 한다. 실패해도 서버는 계속 동작한다."""
        s = settings(self.cfg)
        ok, _why = self.available()
        if not (ok and s["preload"]):
            return None

        def run():
            try:
                self._get_model()
            except SttError as e:
                self.log(f"[stt] {e}")
        if not background:
            run()
            return None
        th = threading.Thread(target=run, name="stt-preload", daemon=True)
        th.start()
        return th

    # ---------- 인식 ----------
    @staticmethod
    def _keep(seg):
        """조용한 소리에서 지어낸 문장(환청)과 반복 루프를 걸러낸다."""
        no_speech = getattr(seg, "no_speech_prob", 0.0) or 0.0
        logprob = getattr(seg, "avg_logprob", 0.0) or 0.0
        ratio = getattr(seg, "compression_ratio", 0.0) or 0.0
        if no_speech > 0.6 and logprob < -1.0:
            return False
        return ratio <= 2.4

    def _run(self, model, audio, s, language, prompt):
        kw = {
            "language": (language or s["language"] or None),
            "beam_size": s["beam_size"],
            "condition_on_previous_text": False,
            "without_timestamps": True,
            "vad_filter": False,           # 말 구간은 클라이언트(Silero VAD)가 이미 잘라서 보낸다
        }
        p = prompt if prompt is not None and prompt != "" else s["prompt"]
        if p:
            kw["initial_prompt"] = p
        segments, info = model.transcribe(audio, **kw)
        parts = [seg.text.strip() for seg in segments if self._keep(seg)]   # segments 는 제너레이터: 여기서 실제 계산이 일어난다
        return " ".join(x for x in parts if x).strip(), info

    def transcribe(self, data, language=None, prompt=None):
        ok, why = self.available()
        if not ok:
            raise SttError(why)
        if not data:
            raise SttError("받은 음성이 비어 있어요")
        s = settings(self.cfg)
        audio = decode_wav(data)
        if audio is None:
            audio = io.BytesIO(data)          # WAV 가 아니면 faster-whisper(PyAV)가 직접 해석한다 (mp3, m4a 등)
        t0 = time.time()
        with self._run_lock:
            model = self._get_model()
            try:
                text, info = self._run(model, audio, s, language, prompt)
            except SttError:
                raise
            except Exception as e:  # noqa: BLE001
                # GPU 로 불러오는 데는 성공했지만 실제 계산에서 라이브러리가 없다고 실패하는 경우가 많다 -> CPU 로 바꿔 한 번 더
                if not self._force_cpu and _is_gpu_error(e):
                    self.log(f"[stt] GPU 계산에 실패해 CPU 로 전환합니다: {e}")
                    self._force_cpu = True
                    self._model = None
                    model = self._get_model()
                    if isinstance(audio, io.BytesIO):
                        audio.seek(0)
                    try:
                        text, info = self._run(model, audio, s, language, prompt)
                    except Exception as e2:  # noqa: BLE001
                        raise SttError(f"음성 인식에 실패했어요: {e2}") from e2
                else:
                    raise SttError(f"음성 인식에 실패했어요: {e}") from e
        duration = float(getattr(info, "duration", 0.0) or (len(audio) / 16000 if hasattr(audio, "__len__") and not isinstance(audio, io.BytesIO) else 0.0))
        return {"text": text, "language": getattr(info, "language", "") or "", "duration": duration, "elapsed": time.time() - t0}
