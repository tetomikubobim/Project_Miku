"""서버 설정 항목 정의와 검증. 통합 런처가 이 정의로 설정 화면을 만들고 config.yaml 에 저장한다.

서버는 설정 파일을 직접 쓰지 않는다 (config.yaml 은 런처가 고치고, 서버는 읽기만 한다).
런처가 저장한 뒤 서버의 POST /admin/reload 를 부르면 실행 중인 설정에도 바로 반영된다.
서버를 다시 켜야 적용되는 항목은 restart=True 로 표시해 둔다.
API 키 같은 비밀 값(secret)은 화면으로 내보내지 않는다 (설정돼 있는지만 알려 줌).
"""

# type: bool | int | float | str | secret | select
FIELDS = [
    {"path": "history_turns", "label": "문맥으로 쓰는 최근 대화 수(턴)", "type": "int", "min": 1, "max": 200, "group": "기본"},
    {"path": "use_think", "label": "행동 묘사(*표정/동작*) 사용", "type": "bool", "group": "기본"},
    {"path": "temperature", "label": "창의성(temperature)", "type": "float", "min": 0, "max": 2, "group": "기본"},
    {"path": "max_output_tokens", "label": "최대 답변 길이(토큰)", "type": "int", "min": 64, "max": 8192, "group": "기본"},
    {"path": "stream", "label": "답을 만드는 대로 먼저 보여주기(스트리밍)", "type": "bool", "group": "기본", "default": True},

    {"path": "llm.provider", "label": "LLM 종류", "type": "select", "options": ["gemini", "openai", "groq", "ollama"], "group": "LLM"},
    {"path": "llm.reasoning_level", "label": "사고 수준 (reasoning)", "type": "select", "options": ["auto", "off", "low", "medium", "high"], "group": "LLM", "default": "auto",
     "hint": "auto=모델 기본 그대로. 모델이 지원하지 않는 값이면 자동으로 빼고 보내요. low/medium/high 는 답변 길이 여유가 자동으로 늘어나고 답이 느려져요"},
    {"path": "llm.gemini.model", "label": "Gemini 모델", "type": "str", "group": "LLM"},
    {"path": "llm.gemini.api_key", "label": "Gemini API 키", "type": "secret", "group": "LLM"},
    {"path": "llm.openai.base_url", "label": "OpenAI 호환 주소", "type": "str", "group": "LLM"},
    {"path": "llm.openai.model", "label": "OpenAI 호환 모델", "type": "str", "group": "LLM"},
    {"path": "llm.openai.api_key", "label": "OpenAI 호환 API 키", "type": "secret", "group": "LLM"},
    {"path": "llm.groq.model", "label": "Groq 모델", "type": "str", "group": "LLM", "hint": "예: openai/gpt-oss-120b, qwen/qwen3-32b. 이미지(화면 공유)는 비전 모델만 가능"},
    {"path": "llm.groq.api_key", "label": "Groq API 키", "type": "secret", "group": "LLM"},
    {"path": "llm.ollama.base_url", "label": "Ollama 주소", "type": "str", "group": "LLM", "default": "http://127.0.0.1:11434/v1", "hint": "기본 http://127.0.0.1:11434/v1"},
    {"path": "llm.ollama.model", "label": "Ollama 모델", "type": "str", "group": "LLM", "hint": "예: qwen3:8b (ollama list 로 확인)"},
    {"path": "llm.ollama.api_key", "label": "Ollama API 키 (클라우드일 때만)", "type": "secret", "group": "LLM"},

    {"path": "tts.enabled", "label": "음성 사용", "type": "bool", "group": "음성", "default": True},
    {"path": "tts.provider", "label": "음성 엔진", "type": "select", "options": ["edge", "gptsovits"], "group": "음성", "restart": True},
    {"path": "tts.edge.voice", "label": "Edge 목소리", "type": "str", "group": "음성"},
    {"path": "tts.edge.rate", "label": "Edge 속도 (예: +15%)", "type": "str", "group": "음성"},
    {"path": "tts.edge.pitch", "label": "Edge 음높이 (예: +30Hz)", "type": "str", "group": "음성"},
    {"path": "tts.edge.volume", "label": "Edge 볼륨 (예: +0%)", "type": "str", "group": "음성"},
    {"path": "tts.rvc.enabled", "label": "RVC 목소리 변환", "type": "bool", "group": "음성", "restart": True, "default": False},
    {"path": "tts.rvc.pitch", "label": "RVC 음높이(반음)", "type": "int", "min": -24, "max": 24, "group": "음성"},

    # GPT-SoVITS (tts.provider 가 gptsovits 일 때 쓰임). 값은 합성할 때마다 읽으니 바로 적용된다.
    {"path": "tts.gpt_weights_path", "label": "GPT 가중치(.ckpt) 경로", "type": "str", "group": "GPT-SoVITS", "hint": "비우면 GPT-SoVITS 가 켜질 때 읽은 가중치 그대로. 상대 경로는 GPT-SoVITS 폴더 기준"},
    {"path": "tts.sovits_weights_path", "label": "SoVITS 가중치(.pth) 경로", "type": "str", "group": "GPT-SoVITS", "hint": "비우면 GPT-SoVITS 가 켜질 때 읽은 가중치 그대로. 모델별 음성은 설정 > 모델 탭"},
    {"path": "tts.ref_audio_path", "label": "참조 음성 파일 경로", "type": "str", "group": "GPT-SoVITS", "hint": "서버 PC 기준 경로 (예: C:/voice/ref.wav)"},
    {"path": "tts.prompt_text", "label": "참조 음성의 대사", "type": "str", "group": "GPT-SoVITS", "hint": "참조 음성에서 실제로 말하는 내용"},
    {"path": "tts.prompt_lang", "label": "참조 음성 언어", "type": "str", "group": "GPT-SoVITS", "hint": "ko / ja / en / zh"},
    {"path": "tts.text_lang", "label": "합성할 글 언어", "type": "str", "group": "GPT-SoVITS", "hint": "ko / ja / en / zh"},
    {"path": "tts.params.speed_factor", "label": "GPT-SoVITS 속도 (1.0=보통)", "type": "float", "min": 0.5, "max": 2, "group": "GPT-SoVITS", "default": 1.0},
    {"path": "tts.params.temperature", "label": "GPT-SoVITS temperature", "type": "float", "min": 0.1, "max": 2, "group": "GPT-SoVITS", "default": 1.0},
    {"path": "tts.params.top_k", "label": "GPT-SoVITS top_k", "type": "int", "min": 1, "max": 100, "group": "GPT-SoVITS", "default": 15},
    {"path": "tts.params.top_p", "label": "GPT-SoVITS top_p", "type": "float", "min": 0.1, "max": 1, "group": "GPT-SoVITS", "default": 1.0},
    {"path": "tts.params.repetition_penalty", "label": "GPT-SoVITS 반복 억제", "type": "float", "min": 1, "max": 2, "group": "GPT-SoVITS", "default": 1.35},
    {"path": "tts.params.text_split_method", "label": "GPT-SoVITS 문장 나누기", "type": "select",
     "options": ["cut0", "cut1", "cut2", "cut3", "cut4", "cut5"], "group": "GPT-SoVITS", "default": "cut5",
     "hint": "cut5=문장부호 기준(기본), cut0=나누지 않음"},
    {"path": "tts.params.seed", "label": "GPT-SoVITS seed (-1=매번 랜덤)", "type": "int", "min": -1, "max": 2147483647, "group": "GPT-SoVITS", "default": -1},

    {"path": "stt.enabled", "label": "음성 인식 사용 (faster-whisper 설치 필요)", "type": "bool", "group": "음성 인식", "default": True},
    {"path": "stt.model", "label": "음성 인식 모델", "type": "str", "group": "음성 인식", "default": "small",
     "hint": "tiny / base / small / medium / large-v3 / large-v3-turbo. 클수록 정확하고 느려요. 바꾸면 다음 인식 때 새로 불러오고, 처음엔 내려받느라 오래 걸려요"},
    {"path": "stt.device", "label": "음성 인식 장치", "type": "select", "options": ["auto", "cpu", "cuda"], "group": "음성 인식", "default": "auto",
     "hint": "auto=GPU 가 있으면 GPU. GPU 를 못 쓰면 자동으로 CPU 로 바뀌어요"},
    {"path": "stt.language", "label": "음성 인식 언어 (ko, en, ja… 비우면 자동)", "type": "str", "group": "음성 인식", "default": "ko"},

    {"path": "search.enabled", "label": "웹 검색 (ddgs 설치 필요)", "type": "bool", "group": "검색", "default": True},
    {"path": "search.engine", "label": "검색 엔진", "type": "select", "options": ["google", "duckduckgo", "bing", "brave", "auto"], "group": "검색", "default": "google",
     "hint": "google/bing/brave 는 막히거나 실패하면 자동으로 duckduckgo 로 넘어가요. 뉴스 검색은 항상 duckduckgo(또는 bing)를 써요"},
    {"path": "search.max_results", "label": "검색 결과 개수", "type": "int", "min": 1, "max": 10, "group": "검색", "default": 5},
    {"path": "search.region", "label": "검색 지역 (kr-kr, us-en, jp-jp, wt-wt=전체)", "type": "str", "group": "검색", "default": "kr-kr"},

    {"path": "memory.enabled", "label": "장기 기억/요약 사용", "type": "bool", "group": "기억", "default": True},
    {"path": "memory.extract_every", "label": "기억 추출 주기(내 메시지 N개마다, 0=끔)", "type": "int", "min": 0, "max": 100, "group": "기억", "default": 6},
    {"path": "memory.max_items", "label": "기억 최대 개수", "type": "int", "min": 1, "max": 500, "group": "기억", "default": 60},
    {"path": "memory.summary", "label": "긴 대화 요약", "type": "bool", "group": "기억", "default": True},
    {"path": "memory.consolidate", "label": "기억이 넘치면 비슷한 것 합치기 (끄면 오래된 것부터 삭제)", "type": "bool", "group": "기억", "default": True},
    {"path": "recall.enabled", "label": "질문과 관련된 과거 대화 찾아 붙이기", "type": "bool", "group": "기억", "default": True},
    {"path": "recall.max_items", "label": "과거 대화 최대 줄 수 (많을수록 토큰 증가)", "type": "int", "min": 1, "max": 10, "group": "기억", "default": 4},
    {"path": "recall.max_chars", "label": "과거 대화 한 줄 최대 글자 수", "type": "int", "min": 30, "max": 400, "group": "기억", "default": 120},
    {"path": "memory.keep_turns", "label": "요약 때 원문으로 남기는 최근 턴", "type": "int", "min": 2, "max": 200, "group": "기억", "default": 20},
]
_BY_PATH = {f["path"]: f for f in FIELDS}


def _get(cfg, path, default=None):
    cur = cfg
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _set(d, path, value):
    keys = path.split(".")
    for k in keys[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


def view(cfg):
    """화면에 보낼 항목 목록 (현재 값 포함). 비밀 값은 값 대신 set(설정 여부)만."""
    out = []
    for f in FIELDS:
        item = {k: v for k, v in f.items() if k != "default"}
        v = _get(cfg, f["path"], f.get("default"))
        if f["type"] == "secret":
            item["set"] = bool(v)
            item["value"] = ""
        else:
            item["value"] = v if v is not None else ("" if f["type"] in ("str", "select") else None)
        out.append(item)
    return out


def coerce(field, value):
    """화면에서 온 값을 검사해서 올바른 형식으로 바꾼다. 안 되면 ValueError (사용자에게 보여 줄 메시지)."""
    t, label = field["type"], field["label"]
    if t == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"'{label}' 은 켜기/끄기여야 해요")
        return value
    if t in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"'{label}' 은 숫자여야 해요")
        try:
            n = float(value) if t == "float" else int(float(value))
        except ValueError:
            raise ValueError(f"'{label}' 은 숫자여야 해요") from None
        if n != n or n in (float("inf"), float("-inf")):
            raise ValueError(f"'{label}' 값이 올바르지 않아요")
        lo, hi = field.get("min"), field.get("max")
        if (lo is not None and n < lo) or (hi is not None and n > hi):
            raise ValueError(f"'{label}' 은 {lo} ~ {hi} 사이여야 해요")
        return n
    if t == "select":
        if value not in field["options"]:
            raise ValueError(f"'{label}' 은 {' / '.join(field['options'])} 중 하나여야 해요")
        return value
    if not isinstance(value, str):
        raise ValueError(f"'{label}' 은 글자여야 해요")
    value = value.strip()
    if len(value) > 500:
        raise ValueError(f"'{label}' 이 너무 길어요")
    return value


def validate(changes):
    """changes: {경로: 값}. 모두 검사해서 올바른 형식으로 바꾼 {경로: 값} 을 돌려준다 (하나라도 틀리면 ValueError, 아무것도 적용하지 않음).
    비밀 값을 비워서 보내면 '그대로 두기'라서 결과에서 빠진다."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("바꿀 항목이 없어요")
    clean = {}
    for path, value in changes.items():
        f = _BY_PATH.get(path)
        if f is None:
            raise ValueError(f"바꿀 수 없는 항목이에요: {path}")
        if f["type"] == "secret" and (value is None or value == ""):
            continue   # 비밀 값은 비워 두면 그대로
        clean[path] = coerce(f, value)
    return clean


def changed_paths(cfg, clean):
    """검사를 마친 값 중에서 지금 설정과 다른 것의 경로 목록."""
    return [p for p, v in clean.items() if _get(cfg, p, _BY_PATH[p].get("default")) != v]


def restart_labels(paths):
    """바뀐 항목 중 서버를 다시 켜야 적용되는 것의 이름들."""
    return [_BY_PATH[p]["label"] for p in paths if _BY_PATH[p].get("restart")]


def field(path):
    return _BY_PATH.get(path)
