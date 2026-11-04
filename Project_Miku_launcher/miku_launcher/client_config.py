"""클라이언트(Electron)의 config.json 중 런처가 대신 고쳐 주는 항목 (지금은 오디오 버퍼 audioLatency 하나).
클라이언트가 같이 쓰는 파일이라 다른 값/순서는 그대로 두고 해당 키만 바꾼다. 클라이언트는 시작할 때 읽으므로 바꾼 값은 다시 시작해야 적용된다."""
import json
import os

AUDIO_LATENCY_HINTS = ("playback", "balanced", "interactive")
AUDIO_LATENCY_ERROR = "오디오 버퍼는 playback / balanced / interactive 중 하나이거나, 0.005 ~ 1 사이의 초 단위 숫자여야 해요 (예: 0.2)"


def audio_latency_error(raw):
    """올바르면 '', 아니면 사용자에게 보여 줄 메시지. (클라이언트의 renderer/lipsync.js normalizeAudioLatency 와 같은 규칙)"""
    s = str(raw if raw is not None else "").strip().lower()
    if s == "" or s in AUDIO_LATENCY_HINTS:
        return ""
    try:
        n = float(s)
    except ValueError:
        return AUDIO_LATENCY_ERROR
    return "" if 0.005 <= n <= 1 else AUDIO_LATENCY_ERROR


def audio_latency_value(raw):
    """config.json 에 적을 값. 비면 None(= 키를 지워 기본값 playback), 이름은 소문자 글자, 숫자는 숫자."""
    s = str(raw if raw is not None else "").strip().lower()
    if s == "":
        return None
    if s in AUDIO_LATENCY_HINTS:
        return s
    n = float(s)
    return int(n) if n == int(n) else n


class ClientConfigError(Exception):
    pass


class ClientConfig:
    def __init__(self, client_dir):
        """client_dir: 클라이언트 폴더를 돌려주는 함수 (런처 설정에서 바꿀 수 있어서 매번 물어본다)."""
        self._dir = client_dir

    @property
    def path(self):
        d = self._dir() if self._dir else ""
        return os.path.join(d, "config.json") if d else ""

    def exists(self):
        return bool(self.path) and os.path.isfile(self.path)

    def _load(self):
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ClientConfigError(f"클라이언트 config.json 을 읽을 수 없어요: {e}") from None
        if not isinstance(data, dict):
            raise ClientConfigError("클라이언트 config.json 은 '항목: 값' 형태여야 해요")
        return data

    def read(self, key, default=None):
        """값을 글자로. 없거나 못 읽으면 default."""
        try:
            v = self._load().get(key)
        except ClientConfigError:
            return default
        return default if v is None else str(v)

    def write(self, key, value):
        """key 하나만 바꿔서 저장한다 (value 가 None 이면 키를 지운다). 임시 파일에 쓰고 바꿔치기해서 도중에 끊겨도 파일이 깨지지 않는다."""
        data = self._load()
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)   # 클라이언트(Node)가 쓰는 JSON.stringify(cfg, null, 2) 와 같은 모양
            os.replace(tmp, self.path)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise ClientConfigError(f"클라이언트 config.json 에 저장하지 못했어요: {e}") from None
