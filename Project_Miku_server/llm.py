"""LLM 제공자 어댑터. 공통 입력: system(str), messages([{'role','content'}]), images([(mime, bytes)]).
- gemini : Google Gemini REST (generateContent)
- openai : OpenAI 호환 API (OpenAI, OpenRouter, 로컬 서버 등. base_url 로 지정)
- groq   : Groq (OpenAI 호환. 주소는 기본값이 정해져 있다)
- ollama : Ollama (OpenAI 호환. 기본 http://127.0.0.1:11434/v1, API 키 불필요)

사고 수준(llm.reasoning_level)은 reasoning.py 가 업체별 파라미터로 바꾼다. 모델이 그 파라미터를 거부(400)하면 다음 후보로, 마지막엔 파라미터 없이 다시 보낸다.
"""
import base64
import json
import time

import requests

import reasoning as R


class LLMError(RuntimeError):
    def __init__(self, message="", status=None):
        super().__init__(message)
        self.status = status   # HTTP 상태 (네트워크 오류 등은 None)


# OpenAI 호환 제공자: 기본 주소와 API 키 필수 여부
OPENAI_LIKE = {
    "openai": {"base_url": "https://api.openai.com/v1", "key_required": False, "label": "llm.openai"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_required": True, "label": "llm.groq"},
    "ollama": {"base_url": "http://127.0.0.1:11434/v1", "key_required": False, "label": "llm.ollama"},
}
PROVIDERS = ("gemini",) + tuple(OPENAI_LIKE)
_GOOD = {}   # (제공자, 모델, 사고 수준) -> 마지막으로 통한 파라미터 후보. 거부되는 후보를 요청마다 다시 시도하지 않게 한다


def _log(msg):
    """런처의 'LLM 로그' 가 이 줄들만 모아서 보여 준다 (LLM 연결은 실시간으로 검사하지 않고 이 로그로 확인한다)."""
    print(f"[llm] {msg}", flush=True)


def _log_fail(label, e, t0):
    code = f"HTTP {e.status} · " if getattr(e, "status", None) else ""
    _log(f"실패 {label} ({time.monotonic() - t0:.1f}s) · {code}{str(e)[:500]}")


def _log_rejected(label, variant, e):
    _log(f"{label} 모델이 사고 수준 설정 {str(variant)[:80]} 을(를) 거부했어요 (HTTP {e.status}). 다른 방식으로 다시 보내요")


def _plan_logged(cfg, system, messages, images, max_tokens, temperature, stream):
    """_plan 과 같고, 설정이 잘못돼 요청을 만들지 못한 경우에도 로그를 남긴다."""
    try:
        return _plan(cfg, system, messages, images, max_tokens, temperature, stream)
    except LLMError as e:
        _log(f"실패 {cfg.get('provider', 'gemini')} · 설정 오류 · {e}")
        raise


def build_gemini_request(system, messages, images, max_tokens, temperature):
    contents = []
    for i, m in enumerate(messages):
        parts = [{"text": m["content"]}]
        if images and i == len(messages) - 1 and m["role"] == "user":
            for mime, data in images:
                parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
        contents.append({"role": "user" if m["role"] == "user" else "model", "parts": parts})
    return {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
    }


_GEMINI_HINTS = {
    "MAX_TOKENS": "출력 토큰 한도를 생각(thinking)에 다 써서 답이 없어요 → 사고 수준을 낮추거나 max_tokens 를 늘려 보세요",
    "SAFETY": "안전 필터에 걸렸어요", "PROHIBITED_CONTENT": "금지 콘텐츠로 분류돼 막혔어요", "BLOCKLIST": "차단 단어 목록에 걸렸어요",
    "SPII": "개인정보로 분류돼 막혔어요", "IMAGE_SAFETY": "이미지가 안전 필터에 걸렸어요", "RECITATION": "저작물 인용으로 분류돼 막혔어요",
    "OTHER": "Gemini 가 이유를 밝히지 않고 멈췄어요", "MALFORMED_FUNCTION_CALL": "함수 호출 형식 오류예요",
}


def gemini_empty_detail(events):
    """Gemini 가 글자를 하나도 안 줬을 때 원인을 구체적으로 적은 한 줄 (events: 응답 JSON 하나, 또는 스트리밍 덩어리 목록).
    차단 사유/종료 사유/안전 필터/토큰 사용량을 모아서 '서버 문제인지, 차단인지, 토큰 부족인지' 로그에서 바로 알 수 있게 한다."""
    if isinstance(events, dict):
        events = [events]
    block = block_msg = finish = finish_msg = model = None
    usage, flagged, n_cand, n_parts, n_thought = {}, [], 0, 0, 0
    for j in events:
        if not isinstance(j, dict):
            continue
        fb = j.get("promptFeedback") or {}
        block, block_msg = fb.get("blockReason") or block, fb.get("blockReasonMessage") or block_msg
        flagged += [r for r in (fb.get("safetyRatings") or []) if isinstance(r, dict) and r.get("blocked")]
        model = j.get("modelVersion") or model
        usage = j.get("usageMetadata") or usage
        for c in (j.get("candidates") or []):
            if not isinstance(c, dict):
                continue
            n_cand += 1
            finish, finish_msg = c.get("finishReason") or finish, c.get("finishMessage") or finish_msg
            flagged += [r for r in (c.get("safetyRatings") or []) if isinstance(r, dict) and r.get("blocked")]
            for p in ((c.get("content") or {}).get("parts") or []):
                if isinstance(p, dict):
                    n_parts += 1
                    n_thought += 1 if p.get("thought") else 0
    out = []
    if block:
        out.append(f"프롬프트 차단: {block}" + (f" ({block_msg})" if block_msg else ""))
    if finish:
        out.append(f"종료 사유: {finish}" + (f" ({finish_msg})" if finish_msg else ""))
    if flagged:
        out.append("안전 필터: " + ", ".join(f"{r.get('category', '?')}={r.get('probability', 'BLOCKED')}" for r in flagged[:4]))
    if not n_cand and not block:
        out.append("응답에 candidates 가 없어요")
    elif n_cand and not n_parts:
        out.append("답변 내용(parts)이 비어 있어요")
    elif n_parts and n_parts == n_thought:
        out.append("생각(thought)만 오고 답변 글자는 없어요")
    if usage:
        out.append("토큰 입력 %s / 생각 %s / 출력 %s" % (usage.get("promptTokenCount", "?"), usage.get("thoughtsTokenCount", 0), usage.get("candidatesTokenCount", 0)))
    if model:
        out.append(f"모델 {model}")
    hint = _GEMINI_HINTS.get(block or finish or "")
    if hint:
        out.append("→ " + hint)
    elif (finish or "STOP") == "STOP" and not block:
        out.append("→ 차단은 아니에요. 모델이 빈 답을 냈어요 (Gemini 쪽 일시 문제일 수 있어 다시 시도해 보세요. 계속되면 모델/사고 수준을 바꿔 보세요)")
    return " · ".join(out)


def _gemini_empty_error(events):
    reason = None
    for j in (events if isinstance(events, list) else [events]):
        if isinstance(j, dict):
            reason = (j.get("promptFeedback") or {}).get("blockReason") or ((j.get("candidates") or [{}])[0] or {}).get("finishReason") or reason
    detail = gemini_empty_detail(events)
    return LLMError(f"Gemini 응답 없음 (사유: {reason or '알 수 없음'})" + (f" · {detail}" if detail else ""))


def parse_gemini_response(j):
    try:
        cands = j["candidates"]
        parts = cands[0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    except (KeyError, IndexError, TypeError):
        raise _gemini_empty_error(j or {}) from None


def build_openai_request(system, messages, images, model, max_tokens, temperature):
    msgs = [{"role": "system", "content": system}]
    for i, m in enumerate(messages):
        if images and i == len(messages) - 1 and m["role"] == "user":
            content = [{"type": "text", "text": m["content"]}]
            for mime, data in images:
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}})
            msgs.append({"role": "user", "content": content})
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    return {"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": temperature}


def openai_like_settings(cfg, provider):
    """cfg(llm 설정)에서 (url, headers, model) 을 꺼낸다. 빠진 필수 값은 LLMError."""
    spec = OPENAI_LIKE[provider]
    o = cfg.get(provider) or {}
    if not o.get("model"):
        raise LLMError(f"config.yaml 의 {spec['label']}.model 이 비어 있어요")
    if spec["key_required"] and not o.get("api_key"):
        raise LLMError(f"config.yaml 의 {spec['label']}.api_key 가 비어 있어요")
    url = (o.get("base_url") or spec["base_url"]).rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if o.get("api_key"):
        headers["Authorization"] = "Bearer " + o["api_key"]
    return url, headers, o["model"]


def _plan(cfg, system, messages, images, max_tokens, temperature, stream):
    """요청 한 건을 준비한다: (제공자, url, headers, [(후보, 본문)...], 캐시 키). 후보는 시도할 순서."""
    provider = cfg.get("provider", "gemini")
    level = R.normalize(cfg.get("reasoning_level"))
    max_tokens = int(max_tokens) + R.HEADROOM.get(level, 0)
    if provider == "gemini":
        g = cfg.get("gemini") or {}
        if not g.get("api_key"):
            raise LLMError("config.yaml 의 llm.gemini.api_key 가 비어 있어요")
        model = g["model"]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:" + ("streamGenerateContent?alt=sse" if stream else "generateContent")
        headers = {"x-goog-api-key": g["api_key"], "Content-Type": "application/json"}
        base = build_gemini_request(system, messages, images, max_tokens, temperature)
    elif provider in OPENAI_LIKE:
        url, headers, model = openai_like_settings(cfg, provider)
        base = build_openai_request(system, messages, images, model, max_tokens, temperature)
        if stream:
            base["stream"] = True
    else:
        raise LLMError(f"알 수 없는 llm.provider: {provider}")
    key = (provider, model, level)
    cands = [(v, R.apply(provider, base, v)) for v in R.variants(provider, model, level)]
    good = _GOOD.get(key)
    if good is not None and any(v == good for v, _ in cands):
        cands.sort(key=lambda c: c[0] != good)   # 통했던 후보를 맨 앞으로 (나머지 순서는 그대로)
    return provider, url, headers, cands, key


def _rejected(e, variant):
    """파라미터 후보를 서버가 거부한 경우인가 (기본 요청 {} 의 400 은 진짜 오류라서 해당 안 됨)."""
    return bool(variant) and isinstance(e, LLMError) and e.status == 400


def _hint_images(e, images):
    if images and e.status == 400:
        return LLMError(f"{e} (이미지를 받지 못하는 모델일 수 있어요. 화면 공유를 끄거나 이미지를 지원하는 모델을 쓰세요)", status=400)
    return e


def parse_openai_response(j):
    try:
        return (j["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"OpenAI 호환 응답 형식 오류: {str(j)[:200]}")


def _post(url, headers, body, timeout=120, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as e:
            last = LLMError(f"네트워크 오류: {e}")
        else:
            if r.status_code == 200:
                return r.json()
            last = LLMError(f"HTTP {r.status_code}: {r.text[:300]}", status=r.status_code)
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last


def generate(cfg, system, messages, images=None, max_tokens=1024, temperature=0.9):
    """cfg: config['llm']. 블로킹 호출이므로 비동기 코드에서는 스레드로 부른다."""
    provider, url, headers, cands, key = _plan_logged(cfg, system, messages, images, max_tokens, temperature, stream=False)
    label, t0, last = f"{key[0]}/{key[1]}", time.monotonic(), None
    for variant, body in cands:
        try:
            j = _post(url, headers, body)
        except LLMError as e:
            if _rejected(e, variant):   # 이 모델이 사고 수준 파라미터를 받지 않음 -> 다음 후보
                _log_rejected(label, variant, e)
                last = e
                continue
            _log_fail(label, e, t0)
            raise _hint_images(e, images) from None
        _GOOD[key] = variant
        try:
            text = parse_gemini_response(j) if provider == "gemini" else parse_openai_response(j)
        except LLMError as e:
            _log_fail(label, e, t0)
            raise
        _log(f"성공 {label} ({time.monotonic() - t0:.1f}s, {len(text)}자)")
        return text
    _log_fail(label, last, t0)
    raise _hint_images(last, images)


# ---------------------------------------------------------------- 스트리밍 (글자가 만들어지는 대로 받기)
def parse_gemini_stream_chunk(j):
    """SSE 한 덩어리에서 글자만 꺼낸다 (없으면 '')."""
    try:
        parts = j["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return ""
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def parse_openai_stream_chunk(j):
    try:
        return j["choices"][0]["delta"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _sse_events(url, headers, body, timeout=120, retries=2, post=None):
    """SSE 의 data: 줄을 JSON 으로 돌려준다 ('[DONE]' 이면 끝). 첫 응답을 받기 전의 429/5xx 와 네트워크 오류는 다시 시도한다."""
    post = post or requests.post
    resp, last = None, None
    for attempt in range(retries + 1):
        try:
            r = post(url, headers=headers, json=body, timeout=timeout, stream=True)
        except requests.RequestException as e:
            last = LLMError(f"네트워크 오류: {e}")
        else:
            if r.status_code == 200:
                resp = r
                break
            last = LLMError(f"HTTP {r.status_code}: {r.text[:300]}", status=r.status_code)
            r.close()
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    if resp is None:
        raise last
    try:
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except ValueError:
                continue
    except requests.RequestException as e:
        raise LLMError(f"스트리밍 중 연결이 끊겼어요: {e}") from None
    finally:
        resp.close()


def _stream_chunks(provider, events):
    """SSE 이벤트에서 글자 조각만 꺼낸다. 아무 글자도 없이 끝나면 LLMError (Gemini 는 받은 덩어리 전부로 원인을 적는다)."""
    got, seen = False, []
    for j in events:
        if provider == "gemini" and len(seen) < 50:
            seen.append(j)
        t = parse_gemini_stream_chunk(j) if provider == "gemini" else parse_openai_stream_chunk(j)
        if t:
            got = True
            yield t
    if not got:
        if provider == "gemini":
            raise _gemini_empty_error(seen)
        raise LLMError("OpenAI 호환 응답이 비어 있어요")


def generate_stream(cfg, system, messages, images=None, max_tokens=1024, temperature=0.9, post=None):
    """generate() 와 같은 입력. 만들어지는 글자 조각을 하나씩 돌려주는 제너레이터. 아무 글자도 없이 끝나면 LLMError."""
    provider, url, headers, cands, key = _plan_logged(cfg, system, messages, images, max_tokens, temperature, stream=True)
    label, t0, last = f"{key[0]}/{key[1]}", time.monotonic(), None
    for variant, body in cands:
        started, first, chars = False, None, 0
        try:
            for t in _stream_chunks(provider, _sse_events(url, headers, body, post=post)):
                if not started:
                    started, first = True, time.monotonic() - t0
                chars += len(t)
                yield t
        except GeneratorExit:   # 받는 쪽이 도중에 그만둠 (중단 버튼 등)
            _log(f"중단 {label} ({time.monotonic() - t0:.1f}s, {chars}자까지)")
            raise
        except LLMError as e:
            if not started and _rejected(e, variant):   # 사고 수준 파라미터를 거부했고 아직 글자가 나가지 않았다 -> 다음 후보
                _log_rejected(label, variant, e)
                last = e
                continue
            _log_fail(label, e, t0)
            raise _hint_images(e, images) from None
        _GOOD[key] = variant
        _log(f"성공 {label} (첫 글자 {first:.1f}s, 전체 {time.monotonic() - t0:.1f}s, {chars}자)")
        return
    _log_fail(label, last, t0)
    raise _hint_images(last, images)
