"""TTS 호출. config.yaml 의 tts.provider 로 엔진을 고른다.

  "provider": "gptsovits"  (기본값. 생략해도 이것)  GPT-SoVITS api_v2 /tts 를 호출. 따로 켜 둬야 한다.
  "provider": "edge"                                 Microsoft Edge TTS. 별도 서버 없이 이 서버 안에서 바로 만든다.

어느 엔진이든 결과는 audio 폴더의 wav 파일 이름으로 돌려준다 (실패하면 None -> 글자만 전달).
단독 테스트:  python tts.py "안녕하세요"   (config.yaml 의 설정 그대로 한 번 합성해 본다)
"""
import array
import asyncio
import io
import os
import re
import sys
import threading
import time
import uuid
import wave

import requests

import rvc as rvc_mod

_lock = threading.Lock()  # GPU 보호: 한 번에 하나만 변환
EDGE_SAMPLE_RATE = 24000  # Edge TTS 는 24kHz mp3 를 준다
DEFAULT_OUTPUT_RATE = 48000  # 클라이언트로 보내는 wav 의 샘플레이트 (tts.output_sample_rate, 0 이면 변환 안 함)


# ---------------------------------------------------------------- GPT-SoVITS
def build_payload(cfg_tts, text, emotion):
    ref = {
        "path": cfg_tts["ref_audio_path"],
        "text": cfg_tts["prompt_text"],
        "lang": cfg_tts.get("prompt_lang", "ko"),
    }
    ref.update((cfg_tts.get("emotion_refs") or {}).get(emotion, {}))  # 감정별 참조 음성이 있으면 덮어씀
    payload = {
        "text": text,
        "text_lang": cfg_tts.get("text_lang", "ko"),
        "ref_audio_path": ref["path"],
        "prompt_text": ref["text"],
        "prompt_lang": ref["lang"],
        "streaming_mode": False,
        "media_type": "wav",
    }
    payload.update(cfg_tts.get("params") or {})
    return payload


def _synth_gptsovits(cfg_tts, text, emotion):
    """wav 바이트, 실패하면 None."""
    payload = build_payload(cfg_tts, text, emotion)
    try:
        r = requests.post(cfg_tts["url"].rstrip("/") + "/tts", json=payload, timeout=cfg_tts.get("timeout", 60))
    except requests.RequestException as e:
        print(f"[tts] 통신 실패: {e}")
        return None
    if r.status_code != 200 or not r.headers.get("Content-Type", "").startswith("audio/"):
        print(f"[tts] 서버 오류 {r.status_code}: {r.text[:200]}")
        return None
    return r.content


# ---------------------------------------------------------------- Edge TTS
_PROSODY_UNITS = {"rate": "%", "pitch": "Hz", "volume": "%"}
_PROSODY_NUM = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(%|hz)?\s*$", re.I)


def _prosody_number(value, key):
    """'+12Hz' / '-10%' / 5 -> 숫자. 단위가 다르거나 못 읽으면 None."""
    m = _PROSODY_NUM.match(str(value))
    if not m:
        return None
    unit = (m.group(2) or "").lower()
    if unit and unit != _PROSODY_UNITS[key].lower():
        return None
    return float(m.group(1))


def edge_prosody(edge_cfg, emotion):
    """기본 rate/pitch/volume 에 감정별 값(edge.emotions)을 **더한다** (기본). 예: 기본 pitch +12Hz, happy +3Hz -> +15Hz.
    edge.emotion_mode: absolute 로 하면 예전처럼 감정 값이 기본값을 통째로 덮어쓴다."""
    p = {
        "rate": edge_cfg.get("rate", "+0%"),
        "pitch": edge_cfg.get("pitch", "+0Hz"),
        "volume": edge_cfg.get("volume", "+0%"),
    }
    emo = (edge_cfg.get("emotions") or {}).get(emotion) or {}
    absolute = str(edge_cfg.get("emotion_mode", "offset")).strip().lower() == "absolute"
    for k, v in emo.items():
        if k not in p:
            continue
        if absolute:
            p[k] = v
            continue
        base, delta = _prosody_number(p[k], k), _prosody_number(v, k)
        if base is None or delta is None:
            print(f"[tts] edge.emotions.{emotion}.{k} 값을 읽지 못해서 무시해요: {v!r} (예: \"+3{_PROSODY_UNITS[k]}\")")
            continue
        p[k] = f"{round(base + delta):+d}{_PROSODY_UNITS[k]}"
    return p


TRIM_THRESHOLD = 30   # 끝의 이 값 이하(약 -60dB)인 샘플은 무음으로 보고 잘라낸다 (16bit 기준)


def _read_pcm16(data):
    """16bit PCM wav -> (채널 수, 샘플레이트, array('h')). 다른 형식이면 None."""
    with wave.open(io.BytesIO(data), "rb") as r:
        ch, width, rate, n = r.getnchannels(), r.getsampwidth(), r.getframerate(), r.getnframes()
        raw = r.readframes(n)
    if width != 2 or n < 16:
        return None
    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    return ch, rate, a


def _write_pcm16(ch, rate, a):
    if sys.byteorder == "big":
        a.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(a.tobytes())
    return out.getvalue()


def pad_wav(data, head_ms=0, tail_ms=0):
    """16bit PCM wav 의 앞/뒤에 무음을 붙인다. 다른 형식이거나 읽지 못하면 그대로 돌려준다.
    RVC 가 입력 끝의 마지막 음절을 깎는 것을 막으려고 변환 전에 쓴다."""
    try:
        pcm = _read_pcm16(data)
        if pcm is None:
            return data
        ch, rate, a = pcm
        head = array.array("h", bytes(2 * ch * int(rate * max(head_ms, 0) / 1000)))
        tail = array.array("h", bytes(2 * ch * int(rate * max(tail_ms, 0) / 1000)))
        return _write_pcm16(ch, rate, head + a + tail)
    except Exception as e:  # noqa: BLE001  부가 기능: 실패해도 원본 음성을 쓴다
        print(f"[tts] 무음 붙이기 건너뜀: {e!r}")
        return data


def fade_wav(data, fade_in_ms=8, fade_out_ms=8, head_pad_ms=0, tail_pad_ms=0, trim_tail=False):
    """16bit PCM wav 의 처음/끝을 아주 짧게 키우고 줄여서 문장 사이 '틱/팝' 소리를 막는다 (RVC 출력이 0 이 아닌 값에서 시작/끝나는 경우).
    페이드는 실제 소리의 가장자리에만 걸고(짧게), 그 바깥에 무음(head_pad_ms/tail_pad_ms)을 붙인다 -> 끝소리가 깎이지 않는다.
    trim_tail: 끝의 거의 무음인 부분을 먼저 잘라서, 붙이는 무음 길이가 항상 tail_pad_ms 가 되게 한다.
    다른 형식(float 등)이거나 읽지 못하면 그대로 돌려준다."""
    try:
        pcm = _read_pcm16(data)
        if pcm is None:
            return data
        ch, rate, a = pcm
        n = len(a) // ch
        if trim_tail:
            while n > 16 and all(abs(a[(n - 1) * ch + c]) <= TRIM_THRESHOLD for c in range(ch)):
                n -= 1
            a = a[: n * ch]
        fi = min(int(rate * fade_in_ms / 1000), n // 4)
        fo = min(int(rate * fade_out_ms / 1000), n // 4)
        for i in range(fi):
            g = i / fi
            for c in range(ch):
                a[i * ch + c] = int(a[i * ch + c] * g)
        for i in range(fo):
            g = i / fo
            for c in range(ch):
                j = (n - 1 - i) * ch + c
                a[j] = int(a[j] * g)
        head = array.array("h", bytes(2 * ch * int(rate * max(head_pad_ms, 0) / 1000)))
        tail = array.array("h", bytes(2 * ch * int(rate * max(tail_pad_ms, 0) / 1000)))
        return _write_pcm16(ch, rate, head + a + tail)
    except Exception as e:  # noqa: BLE001  페이드는 부가 기능: 실패해도 원본 음성을 쓴다
        print(f"[tts] 페이드 처리 건너뜀: {e!r}")
        return data


def pcm_to_wav(pcm_bytes, sample_rate):
    """16bit mono PCM -> wav 바이트."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


def fix_sample_rate(data, target):
    """16bit PCM wav 를 target Hz 로 맞춘다 (엔진/RVC 모델마다 24k/40k/48k 로 제각각인 것을 하나로 고정).
    이미 같거나, target 이 0 이거나, 읽지 못하면 그대로 돌려준다. (miniaudio 는 Edge 용으로 이미 설치돼 있어야 함)"""
    if not target:
        return data
    try:
        with wave.open(io.BytesIO(data), "rb") as r:
            rate, ch, width = r.getframerate(), r.getnchannels(), r.getsampwidth()
        if rate == target or width != 2:
            return data
        import miniaudio
        pcm = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=ch, sample_rate=target)
        return _write_pcm16(ch, target, pcm.samples)
    except Exception as e:  # noqa: BLE001  부가 기능: 실패해도 원본 음성을 쓴다
        print(f"[tts] 샘플레이트 고정 건너뜀: {e!r}")
        return data


def _synth_edge(cfg_tts, text, emotion):
    """wav 바이트, 실패하면 None. (edge-tts, miniaudio 는 이 엔진을 쓸 때만 필요)"""
    edge_cfg = cfg_tts.get("edge") or {}
    try:
        import edge_tts
        import miniaudio
    except ImportError as e:
        print(f"[tts] Edge TTS 를 쓰려면 설치가 필요해요: pip install edge-tts miniaudio  ({e})")
        return None

    voice = edge_cfg.get("voice", "ko-KR-SunHiNeural")
    p = edge_prosody(edge_cfg, emotion)

    async def run():
        mp3 = bytearray()
        async for chunk in edge_tts.Communicate(text, voice, rate=p["rate"], pitch=p["pitch"], volume=p["volume"]).stream():
            if chunk["type"] == "audio":
                mp3 += chunk["data"]
        return bytes(mp3)

    # Edge 서버(비공식 웹소켓)가 가끔 응답 없이 멈춘다 -> 한 번에 오래 기다리지 말고, 짧게 기다렸다가 다시 시도한다.
    # 평소 0.5~2초 걸려서 시도 하나는 12초면 충분하다. timeout 은 모든 시도를 합친 상한.
    attempts = 1 + max(0, int(edge_cfg.get("retries", 2)))
    per_try = float(edge_cfg.get("attempt_timeout", 12))
    deadline = time.monotonic() + float(edge_cfg.get("timeout", 30))
    try:
        mp3 = b""
        for n in range(1, attempts + 1):
            left = deadline - time.monotonic()
            if left <= 0:
                raise asyncio.TimeoutError()
            try:
                # 이 함수는 엔진의 작업 스레드에서 불리므로 실행 중인 이벤트 루프가 없다 -> asyncio.run 사용 가능
                mp3 = asyncio.run(asyncio.wait_for(run(), timeout=min(per_try, left)))
                break
            except Exception as e:  # noqa: BLE001
                if n >= attempts:
                    raise
                print(f"[tts] Edge TTS 다시 시도해요 ({n}/{attempts - 1}): {e!r}")
        if not mp3:
            print(f"[tts] Edge TTS 가 빈 오디오를 돌려줬어요 (voice={voice!r}, 텍스트/음성 이름 확인)")
            return None
        # mp3 -> wav 로 바꿔서 GPT-SoVITS 결과와 같은 형식으로 맞춘다 (클라이언트/정리 로직이 wav 기준)
        pcm = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=EDGE_SAMPLE_RATE)
        return pcm_to_wav(pcm.samples.tobytes(), EDGE_SAMPLE_RATE)
    except Exception as e:  # noqa: BLE001  네트워크/음성 이름 오류 등. 대화는 글자로라도 이어지게 한다
        print(f"[tts] Edge TTS 실패: {e!r}")
        return None


# ---------------------------------------------------------------- 공통
PROVIDERS = {"gptsovits": _synth_gptsovits, "edge": _synth_edge}


def synthesize(cfg_tts, text, emotion, out_dir):
    """성공하면 저장된 wav 파일 이름, 실패하면 None."""
    if not cfg_tts.get("enabled", True) or not text.strip():
        return None
    provider = str(cfg_tts.get("provider") or "gptsovits").strip().lower()
    fn = PROVIDERS.get(provider)
    if fn is None:
        print(f"[tts] 알 수 없는 provider {provider!r} (사용 가능: {', '.join(PROVIDERS)})")
        return None
    t0 = time.monotonic()
    with _lock:
        data = fn(cfg_tts, text, emotion)
    if not data:
        return None
    if rvc_mod.active(cfg_tts):  # Edge 음성을 RVC 로 다른 음색(예: 미쿠)으로 바꾼다. 실패하면 원래 음성으로 대신한다
        t1 = time.monotonic()
        rvc_cfg = rvc_mod.rvc_cfg(cfg_tts)
        # RVC 가 입력 끝의 마지막 음절을 깎지 않게 변환 전에 끝에 무음을 붙인다
        converted = rvc_mod.get_client(cfg_tts).convert(pad_wav(data, tail_ms=float(rvc_cfg.get("in_tail_pad_ms", 100))))
        print(f"[tts] 시간: Edge {t1 - t0:.1f}초 + RVC {time.monotonic() - t1:.1f}초" + ("" if converted else " (변환 실패)"))  # 어느 단계가 느린지 보기 위함
        if converted:
            # RVC 출력의 처음/끝 팝노이즈는 실제 소리 가장자리의 짧은 페이드로 막고, 끝에는 무음을 붙여 끝소리/출력 장치 지연으로 잘리지 않게 한다
            data = fade_wav(converted, head_pad_ms=float(rvc_cfg.get("head_pad_ms", 10)),
                            tail_pad_ms=float(rvc_cfg.get("tail_pad_ms", 80)), trim_tail=True)
        elif rvc_mod.rvc_cfg(cfg_tts).get("fallback", True) is False:
            return None
    data = fix_sample_rate(data, int(cfg_tts.get("output_sample_rate", DEFAULT_OUTPUT_RATE) or 0))
    os.makedirs(out_dir, exist_ok=True)
    name = f"{uuid.uuid4()}.wav"
    with open(os.path.join(out_dir, name), "wb") as f:
        f.write(data)
    prune_old(out_dir)
    return name


def prune_old(out_dir, keep=40):
    try:
        files = sorted((os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".wav")), key=os.path.getmtime)
        for p in files[:-keep]:
            os.remove(p)
    except OSError:
        pass


if __name__ == "__main__":
    import sys

    import configfile

    base = os.path.dirname(os.path.abspath(__file__))
    cfg = configfile.load(base)
    sample = " ".join(sys.argv[1:]) or "안녕하세요, 목소리 테스트예요."
    tts_cfg = cfg.get("tts", {})
    print(f"[tts] provider={tts_cfg.get('provider') or 'gptsovits'} 로 합성해 볼게요: {sample!r}")
    out = os.path.join(base, cfg.get("paths", {}).get("audio", "audio"))
    result = synthesize(tts_cfg, sample, "neutral", out)
    print(f"성공: {os.path.join(out, result)}" if result else "실패 (위 로그를 확인하세요)")
