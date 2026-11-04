"""가사 검색어 보정 (LLM). 클라이언트가 가사를 못 찾았을 때만 부른다.

유튜브 제목은 지저분하고 (Official MV, 채널 꼬리표 ...), 로마자로 적힌 일본어 곡명(Kiseki)은 알송에서 안 잡힌다.
LLM 에게 제목/가수를 정리시키고, 검색에 쓸 후보(원어 / 한국어 표기 / 영어)를 최대 3개 받는다.
결과는 DB(kv)에 캐시해서 같은 곡은 다시 부르지 않는다. 음악이 아닌 영상은 is_music=False 로 알려 준다.
"""
import hashlib
import json
import re

SYSTEM = "당신은 음악 메타데이터 정리 도구입니다. 설명 없이 JSON 한 개만 출력합니다."

PROMPT = """미디어 플레이어(유튜브, 스포티파이 등)가 알려 준 정보입니다. 한국 가사 DB(알송)에서 검색할 수 있게 정리하세요.

제목: {title}
가수/채널: {artist}

규칙:
- 음악이 아니면(게임 방송, 뉴스, 강의, 예능, 브이로그 등) is_music 을 false 로.
- 제목에서 (Official MV), [MV], Lyrics, 가사, feat. 표기, 채널 꼬리표 같은 잡음을 뺀다.
- 로마자로 적힌 일본어/한국어 곡명은 확실할 때만 원래 표기로 바꾼다 (예: Kiseki -> キセキ). 모르면 그대로 둔다.
- 가수가 채널명이면 그대로, "- Topic"/VEVO/레이블처럼 가수가 아닌 채널이면 제목에서 가수를 찾고 모르면 빈 문자열.
- candidates: 검색에 쓸 후보를 가능성 높은 순으로 최대 3개. 각각 {{"title": "", "artist": ""}}.
  같은 곡의 다른 표기(원어 / 한국어에서 쓰는 표기 / 영어)가 있으면 후보로 넣는다.
- 지어내지 않는다. 확실하지 않으면 입력을 정리만 해서 쓴다.

출력 형식: {{"is_music": true, "candidates": [{{"title": "", "artist": ""}}]}}"""

MAX_TITLE = 120
MAX_ARTIST = 80
MAX_CANDIDATES = 3
CACHE_PREFIX = "lyrics_norm:"


def cache_key(title, artist):
    raw = f"{(title or '').strip().lower()}\x1f{(artist or '').strip().lower()}"
    return CACHE_PREFIX + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _json_object(text):
    """응답에서 첫 JSON 객체를 꺼낸다 (```json 울타리나 앞뒤 설명이 붙어도 됨). 없으면 None."""
    s = (text or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s)
    start = s.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except ValueError:
                    return None
    return None


def parse_response(text):
    """LLM 응답 -> {"is_music": bool, "candidates": [{"title","artist"}]}. 해석할 수 없으면 None."""
    obj = _json_object(text)
    if not isinstance(obj, dict):
        return None
    is_music = obj.get("is_music", True)
    is_music = is_music if isinstance(is_music, bool) else str(is_music).strip().lower() not in ("false", "no", "0")
    out, seen = [], set()
    raw = obj.get("candidates")
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        t = str(c.get("title") or "").strip()[:MAX_TITLE]
        a = str(c.get("artist") or "").strip()[:MAX_ARTIST]
        key = (t.lower(), a.lower())
        if not t or key in seen:
            continue
        seen.add(key)
        out.append({"title": t, "artist": a})
        if len(out) >= MAX_CANDIDATES:
            break
    if is_music and not out:
        return None   # 음악이라면서 후보가 없으면 쓸모없는 응답
    return {"is_music": is_music, "candidates": out}


def normalize(ask, store, title, artist=""):
    """ask(prompt) -> 응답 글자 (블로킹). store: kv_get/kv_set 을 가진 객체(없으면 None). 해석 실패 시 ValueError."""
    title = (title or "").strip()[:200]
    artist = (artist or "").strip()[:120]
    key = cache_key(title, artist)
    if store is not None:
        cached = store.kv_get(key)
        if cached:
            try:
                res = json.loads(cached)
                res["cached"] = True
                return res
            except ValueError:
                pass   # 깨진 캐시는 무시하고 다시 묻는다
    res = parse_response(ask(PROMPT.format(title=title, artist=artist or "(없음)")))
    if res is None:
        raise ValueError("LLM 응답을 해석하지 못했어요")
    if store is not None:
        store.kv_set(key, json.dumps(res, ensure_ascii=False))
    res["cached"] = False
    return res
