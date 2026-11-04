"""가사 번역 (LLM). 원문만 있는 가사를 줄 단위로 번역해서 같은 개수의 줄로 돌려준다.

클라이언트가 곡마다 한 번만 부르고 결과를 자기 쪽 캐시에 저장한다. 서버에서도 같은 가사 + 같은 언어는 DB(kv)에 캐시한다.
긴 가사는 CHUNK 줄씩 나눠 번역한다. 줄마다 번호를 붙여 주고받아서 모델이 줄을 빼먹어도 나머지는 밀리지 않고,\n빠진 줄만 한 번 더 요청한다 (그래도 20% 넘게 빠지면 실패, 조금이면 그 줄만 번역 없이 둔다).
"""
import hashlib
import json
import re

SYSTEM = "당신은 노래 가사 번역가입니다. 설명 없이 번역한 줄들만 출력합니다."

LANG_NAMES = {"ko": "한국어", "en": "영어", "ja": "일본어", "zh": "중국어"}

# 줄마다 번호표([번호])를 붙여 보내고 같은 번호표로 받는다. 번호로 짝을 맞추니 모델이 줄을 하나 합치거나 빼먹어도 나머지는 밀리지 않는다.
PROMPT = """노래 \"{title}\" ({artist}) 의 가사입니다. 각 줄을 {lang_name}로 번역하세요.

규칙:
- 입력과 같은 번호표 [번호] 를 앞에 붙여서 한 줄에 하나씩 출력한다. 예: [3] 번역한 글
- 줄을 합치거나 나누거나 빼지 않는다. 같은 가사가 반복돼도 줄마다 따로 번역한다.
- 앞뒤 줄의 문맥을 참고해 자연스럽게, 의미 위주로 옮긴다.
- 이미 {lang_name}이거나 추임새/의성어(la la la, 아아 등)인 줄은 그대로 둔다.
- 해설, 주석, 따옴표 장식을 붙이지 않는다.

입력:
{lines_text}"""

CACHE_PREFIX = "lyrics_tr:"
CHUNK = 80
MAX_LINES = 300
MAX_LINE_CHARS = 300


def cache_key(lines, lang):
    raw = f"{lang}\x1f" + "\n".join(lines)
    return CACHE_PREFIX + hashlib.sha1(raw.encode("utf-8")).hexdigest()


_LABEL_RE = re.compile(r"^\s*\[(\d+)\]\s?(.*)$")


def parse_numbered(text, allowed):
    """응답의 "[번호] 글" 줄들을 {번호: 글} 로. allowed 에 없는 번호는 버린다. 번호 없이 이어지는 줄은 앞 번호의 뒷부분으로 붙인다."""
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", (text or "").strip())
    out, cur = {}, None
    for raw in s.split("\n"):
        m = _LABEL_RE.match(raw)
        if m:
            n = int(m.group(1))
            cur = n if n in allowed else None
            if cur is not None:
                out[cur] = m.group(2).strip()
        elif cur is not None and raw.strip():
            out[cur] = (out[cur] + " " + raw.strip()).strip()
    return out


def _translate_chunk(ask, chunk, lang, title, artist):
    """chunk: [(번호, 원문)]. 빠진 줄만 한 번 더 요청하고, 그래도 많이 빠지면 실패 (조금 빠진 줄은 번역 없이 둔다)."""
    res = {}
    pending = [(i, t) for i, t in chunk if t]
    for _ in range(2):
        if not pending:
            break
        prompt = PROMPT.format(title=title or "(제목 없음)", artist=artist or "(가수 없음)", lang_name=LANG_NAMES.get(lang, lang),
                               lines_text="\n".join(f"[{i}] {t}" for i, t in pending))
        got = parse_numbered(ask(prompt, len(pending)), {i for i, _ in pending})
        res.update({i: v for i, v in got.items() if v})
        pending = [(i, t) for i, t in pending if i not in res]
    if len(pending) > max(2, len([1 for _, t in chunk if t]) // 5):   # 20% 넘게 빠지면 믿을 수 없다
        raise ValueError(f"번역이 {len(pending)}줄 빠졌어요")
    return [res.get(i, "") for i, _ in chunk]


def translate(ask, store, lines, lang="ko", title="", artist="", force=False):
    """ask(prompt, n_lines) -> 응답 글자 (블로킹). lines: 원문 문자열 목록. -> {"lines": [...], "cached": bool}. 실패하면 ValueError."""
    lines = [str(x or "").strip()[:MAX_LINE_CHARS] for x in lines][:MAX_LINES]
    if not any(lines):
        raise ValueError("번역할 줄이 없어요")
    title = (title or "").strip()[:120]
    artist = (artist or "").strip()[:80]
    key = cache_key(lines, lang)
    if store is not None and not force:
        cached = store.kv_get(key)
        if cached:
            try:
                res = json.loads(cached)
                if isinstance(res, list) and len(res) == len(lines):
                    return {"lines": res, "cached": True}
            except ValueError:
                pass
    out = []
    for i in range(0, len(lines), CHUNK):
        out.extend(_translate_chunk(ask, list(enumerate(lines))[i:i + CHUNK], lang, title, artist))
    if store is not None:
        store.kv_set(key, json.dumps(out, ensure_ascii=False))
    return {"lines": out, "cached": False}
