"""런처가 보는 서버 상태 (GET /health 의 내용). 웹 프레임워크와 무관한 순수 함수라서 따로 시험할 수 있다.

상태 값(state): ok(정상) / loading(켜지는 중, 모델 불러오는 중) / idle(쓸 때 불러옴) / down(꺼짐·응답 없음) / error(오류) / off(설정에서 꺼 둠)

LLM 은 실시간으로 확인하지 않는다 (호출마다 토큰을 쓰고 한도에 걸릴 수 있어서). 어떤 제공자/모델을 쓰는지만 알려 주고,
성공/실패는 서버 콘솔의 `[llm]` 로그로 본다.
"""
import os
import time

import rvc
import stt as stt_mod
import tts_launcher

DEFAULT_SOVITS_URL = "http://127.0.0.1:9880"
_ORDER = {"ok": 0, "idle": 0, "off": 0, "loading": 1, "error": 2, "down": 2}   # 여러 개를 합칠 때 더 나쁜 쪽


def service_state(url, launcher, is_up):
    """GPT-SoVITS / RVC 같은 따로 도는 서비스 하나의 상태. launcher: tts_launcher.Launcher 또는 None."""
    if is_up(url):
        return {"state": "ok", "url": url, "detail": ""}
    proc = getattr(launcher, "proc", None)
    if proc is not None:
        code = proc.poll()
        if code is None:
            return {"state": "loading", "url": url, "detail": "시작하는 중 (모델 불러오기)"}
        return {"state": "down", "url": url, "detail": f"프로세스가 종료됐어요 (코드 {code}). 로그를 확인하세요"}
    return {"state": "down", "url": url, "detail": "응답이 없어요"}


def _worst(states):
    return max(states, key=lambda s: _ORDER.get(s, 0), default="ok")


def tts_status(cfg, launchers, is_up):
    t = (cfg or {}).get("tts") or {}
    provider = str(t.get("provider") or "gptsovits").strip().lower()
    if not t.get("enabled", True):
        return {"state": "off", "provider": provider, "detail": "음성 꺼짐", "services": {}}
    services = {}
    if provider == "gptsovits":
        services["sovits"] = service_state(t.get("url") or DEFAULT_SOVITS_URL, (launchers or {}).get("sovits"), is_up)
    elif rvc.active(t):
        services["rvc"] = service_state(rvc.url_of(t), (launchers or {}).get("rvc"), is_up)
    if services:
        state = _worst(v["state"] for v in services.values())
        detail = next((v["detail"] for v in services.values() if v["state"] == state and v["detail"]), "")
    else:
        state, detail = "ok", "Edge TTS"
    return {"state": state, "provider": provider, "detail": detail, "services": services}


def stt_status(cfg, transcriber):
    s = stt_mod.settings(cfg)
    base = {"model": s["model"]}
    if not s["enabled"]:
        return {**base, "state": "off", "detail": "음성 인식 꺼짐"}
    ok, why = transcriber.available()
    if not ok:
        return {**base, "state": "error", "detail": why}
    if transcriber.last_error:
        return {**base, "state": "error", "detail": str(transcriber.last_error)}
    if transcriber.ready:
        return {**base, "state": "ok", "detail": ""}
    if transcriber.loading:
        return {**base, "state": "loading", "detail": "모델 불러오는 중"}
    return {**base, "state": "idle", "detail": "처음 쓸 때 불러와요"}


def llm_info(cfg):
    llm = (cfg or {}).get("llm") or {}
    provider = llm.get("provider", "gemini")
    return {"provider": provider, "model": (llm.get(provider) or {}).get("model", "")}


def build(cfg, transcriber, launchers, clients, started, is_up=tts_launcher.is_up, launched_by_launcher=False):
    """GET /health 의 본문. is_up 은 네트워크를 쓰므로 비동기 코드에서는 스레드로 불러야 한다."""
    return {
        "ok": True,
        "pid": os.getpid(),
        "started": started,
        "uptime": round(max(0.0, time.time() - started), 1),
        "host": cfg.get("host", "127.0.0.1"),
        "port": cfg.get("port", 3545),
        "clients": clients,
        "launcher": bool(launched_by_launcher),
        "stt": stt_status(cfg, transcriber),
        "tts": tts_status(cfg, launchers, lambda u: is_up(u, timeout=0.8)),
        "llm": llm_info(cfg),
    }
