"""사고 수준(reasoning level): LLM 이 답하기 전에 얼마나 '생각'할지.

설정(llm.reasoning_level): auto | off | low | medium | high
  auto   아무것도 보내지 않는다 (모델 기본값 그대로. 예전과 똑같이 동작)
  off    생각을 최대한 끈다 (모델이 끌 수 없으면 가장 낮은 단계)
  low / medium / high   생각하는 양

업체마다 파라미터 이름과 허용 값이 달라서 여기서 변환한다. 모델마다 지원하는 값도 달라서 확실한 규칙을 만들 수 없으므로,
variants() 는 '시도해 볼 파라미터 후보'를 앞에서부터 순서대로 돌려주고, 마지막은 항상 {} (아무것도 안 덧붙임) 이다.
llm.py 가 서버가 400 으로 거부하면 다음 후보로 넘어가고, 통한 후보는 기억해 둔다.

  Gemini 3 이상    generationConfig.thinkingConfig.thinkingLevel  (minimal/low/medium/high. 모델마다 일부만 지원)
  Gemini 2.5       generationConfig.thinkingConfig.thinkingBudget (0=끔, 양수=토큰 예산)   * thinkingLevel 과 같이 보내면 400
  OpenAI 호환      reasoning_effort (none/minimal/low/medium/high. 추론 모델만 받음. Ollama 도 같은 이름)
  Groq             reasoning_effort + reasoning_format:hidden (gpt-oss 는 include_reasoning:false). 모델마다 허용 값이 다르다
"""
import copy
import re

LEVELS = ("auto", "off", "low", "medium", "high")
# 생각에 쓰는 토큰도 '최대 답변 길이'에서 깎이므로, 사고 수준을 켜면 그만큼 여유를 더해서 답이 잘리지 않게 한다
HEADROOM = {"low": 1024, "medium": 3072, "high": 6144}
_GEMINI_BUDGET = {"low": 1024, "medium": 8192, "high": 24576}   # 2.5 Flash/Flash-Lite 의 최대 24576 이하라서 어느 모델에도 들어간다


def normalize(level):
    """모르는 값/빈 값은 auto."""
    v = str(level or "auto").strip().lower()
    return v if v in LEVELS else "auto"


def _gemini_major_minor(model):
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", str(model or "").lower())
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def variants(provider, model, level):
    """요청에 덧붙일 파라미터 후보 목록. 마지막은 항상 {}."""
    level = normalize(level)
    m = str(model or "").lower()
    out = []

    if provider == "gemini":
        major, minor = _gemini_major_minor(model)
        if level == "auto":
            pass
        elif major >= 3:   # thinkingLevel (소문자가 안 먹으면 대문자도 시도)
            order = {"off": ["minimal", "low"], "low": ["low"], "medium": ["medium", "high", "low"], "high": ["high"]}[level]
            for lv in order:
                out.append({"generationConfig": {"thinkingConfig": {"thinkingLevel": lv}}})
            out.insert(1, {"generationConfig": {"thinkingConfig": {"thinkingLevel": order[0].upper()}}})
        elif major == 2 and minor >= 5:   # thinkingBudget
            budget = 0 if level == "off" else _GEMINI_BUDGET[level]
            out.append({"generationConfig": {"thinkingConfig": {"thinkingBudget": budget}}})

    elif provider == "groq":
        gptoss = "gpt-oss" in m
        hide = {"include_reasoning": False} if gptoss else {"reasoning_format": "hidden"}   # 생각 과정이 본문에 섞여 나오지 않게
        if level == "auto":
            out.append(dict(hide))
        elif level == "off":
            out.append({"reasoning_effort": "low" if gptoss else "none", **hide})   # gpt-oss 는 끌 수 없어서 가장 낮은 단계
            out.append(dict(hide))
        else:
            out.append({"reasoning_effort": level, **hide})
            out.append({"reasoning_effort": level})

    elif provider in ("openai", "ollama"):
        if level == "off":
            out += [{"reasoning_effort": "none"}] + ([{"reasoning_effort": "minimal"}] if provider == "openai" else [])
        elif level != "auto":
            out.append({"reasoning_effort": level})

    out.append({})
    return out


def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def apply(provider, body, patch):
    """요청 본문(body)에 후보(patch)를 덧붙인 새 본문. 원본은 건드리지 않는다.
    OpenAI 호환 API 중 추론 모델은 max_tokens 대신 max_completion_tokens 를 요구한다 (Groq 는 항상 그쪽 이름)."""
    out = _deep_merge(copy.deepcopy(body), patch)
    if provider in ("openai", "groq", "ollama") and "max_tokens" in out and (
            provider == "groq" or (provider == "openai" and "reasoning_effort" in patch)):
        out["max_completion_tokens"] = out.pop("max_tokens")
    return out
