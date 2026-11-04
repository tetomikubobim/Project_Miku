"""LLM 응답 파싱: [감정]*행동 묘사*대사  ->  (emotion, think, spoken)

행동 묘사는 `*별표*` 로 감싼 것이다 (예전 형식인 <think>*…*</think> 도 받아 준다. 모델이 <thought>, </thought> 처럼
태그를 틀리게 써도 같은 뜻으로 본다). 행동 묘사는 대사에서 빼서 think 로 돌려준다: 대사창/음성에는 대사만 남는다."""
import re

# 기본 감정 (config.yaml 의 emotions 로 바꾸거나 늘릴 수 있다). 값은 AI 에게 알려 줄 설명.
DEFAULT_EMOTIONS = {
    "neutral": "평온, 보통",
    "happy": "기쁨, 즐거움",
    "sad": "슬픔, 서운함",
    "angry": "화남, 짜증",
    "surprise": "놀람, 당황",
    "smirk": "집착, 조롱",
    "fear": "공포, 두려움",
    "disgust": "역겨움, 경멸"
}
EMOTIONS = tuple(DEFAULT_EMOTIONS)
_ALIASES = {
    "보통": "neutral", "중립": "neutral", "normal": "neutral", "default": "neutral",
    "기쁨": "happy", "행복": "happy", "joy": "happy", "smile": "happy",
    "슬픔": "sad", "sadness": "sad",
    "분노": "angry", "화남": "angry", "anger": "angry", "mad": "angry",
    "놀람": "surprise", "당황": "surprise", "surprised": "surprise", "shock": "surprise",
    "집착": "smirk", "조롱": "smirk", "비웃음": "smirk", "냉소": "smirk", "smug": "smirk", "sneer": "smirk",
    "공포": "fear", "두려움": "fear", "무서움": "fear", "겁": "fear", "scared": "fear", "afraid": "fear",
    "역겨움": "disgust", "경멸": "disgust", "혐오": "disgust", "disgusted": "disgust", "contempt": "disgust",
}
_EMO_TAG = re.compile(r"\[\s*([A-Za-z가-힣]+)\s*\]")
# 모델이 think / thought / thinking 을 섞어 쓰는 경우가 있다 (<think>…</thought> 처럼 닫는 태그가 달라도 같은 블록으로 본다)
_OPEN_PAT = r"<\s*(?:think|thought|thinking)\s*>"
_CLOSE_PAT = r"<\s*/\s*(?:think|thought|thinking)\s*>"
_THINK = re.compile(_OPEN_PAT + r"(.*?)" + _CLOSE_PAT, re.S | re.I)
_THINK_OPEN = re.compile(_OPEN_PAT + r"(.*)$", re.S | re.I)
_OPEN_TAG = re.compile(_OPEN_PAT, re.I)
_CLOSE_TAG = re.compile(_CLOSE_PAT, re.I)
_OPEN_NAMES = ("<think>", "<thought>", "<thinking>")
_CLOSE_NAMES = ("</think>", "</thought>", "</thinking>")
# *행동 묘사*: 한 줄 안에서 별표 하나로 감싼 것 (**굵게** 는 제외)
_ACTION = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)")
_LEADING_ACTIONS = re.compile(r"\s*(?:(?<!\*)\*(?!\*)[^*\n]+?(?<!\*)\*(?!\*)\s*)+")
_JUNK = ("<bos>", "<eos>", "<end_of_turn>", "<turn|>", "<|turn>")


def canonical_emotion(word):
    """같은 뜻의 표기를 기본 이름으로 (anger -> angry, 기쁨 -> happy). 모르면 소문자로 그대로."""
    w = (word or "").strip().lower()
    return _ALIASES.get(w) or _ALIASES.get((word or "").strip()) or w


def norm_emotion(word, allowed=EMOTIONS):
    """허용된 감정 이름이면 그대로, 흔한 다른 표기(기쁨, joy 등)면 허용된 이름으로 바꿔 준다. 모르면 None.
    모델별 감정 태그(예: anger)를 쓰는 경우에도 같은 뜻의 다른 표기(angry)를 받아 준다."""
    w = (word or "").strip().lower()
    if w in allowed:
        return w
    canon = canonical_emotion(word)
    if canon in allowed:
        return canon
    for a in allowed:
        if canonical_emotion(a) == canon:
            return a
    return None


def parse_reply(raw, allowed=EMOTIONS):
    text = raw or ""
    for j in _JUNK:
        text = text.replace(j, "")
    text = text.strip()

    # think (닫히지 않은 것도 처리)
    thinks = [t.strip() for t in _THINK.findall(text)]
    text = _THINK.sub("", text)
    m = _THINK_OPEN.search(text)
    if m:
        # 닫는 태그가 아예 없다: 앞쪽의 *행동 묘사* 는 think, 그 뒤의 글은 대사로 살린다 (통째로 속마음으로 보면 대사/음성이 사라진다)
        head, rest = split_leading_actions(m.group(1))
        if head:
            thinks.append(head)
            text = text[: m.start()] + rest
        else:   # 행동 묘사가 없는 잘린 속마음: 전부 think 로 본다
            thinks.append(m.group(1).strip())
            text = text[: m.start()]
    text = _CLOSE_TAG.sub("", text)   # 여는 태그 없이 남은 닫는 태그

    # 감정 태그: 인식되는 첫 번째만 감정으로 쓰고, 인식되는 모든 [감정] 태그는 본문에서 지운다
    emotion = None

    def _strip(mo):
        nonlocal emotion
        e = norm_emotion(mo.group(1), allowed)
        if e is None:
            return mo.group(0)
        if emotion is None:
            emotion = e
        return ""

    text = _EMO_TAG.sub(_strip, text)
    # 대사 속의 *행동 묘사* 는 대사창/음성에서 빼고 think 로 옮긴다
    thinks += [f"*{a.strip()}*" for a in _ACTION.findall(text) if a.strip()]
    text = _ACTION.sub("", text)
    spoken = re.sub(r"\n{3,}", "\n\n", text).strip()
    return emotion or "neutral", " ".join(t for t in thinks if t), spoken


def split_leading_actions(s):
    """'*행동* *행동* 나머지' -> ('*행동* *행동*', '나머지'). 앞에 행동 묘사가 없으면 ('', 원래 글)."""
    s = s or ""
    m = _LEADING_ACTIONS.match(s)
    if not m:
        return "", s
    return " ".join(f"*{a.strip()}*" for a in _ACTION.findall(m.group(0)) if a.strip()), s[m.end():]


def finalize(raw, allowed=EMOTIONS, use_think=True):
    """엔진이 쓰는 최종 분해: (emotion, think, spoken). 대사가 비어 버렸으면 태그/행동 묘사만 걷어낸 글로 채운다."""
    emotion, think, spoken = parse_reply(raw, allowed)
    if not use_think:
        think = ""   # 행동 묘사를 끈 상태: 모델이 써도 클라이언트로 보내지 않는다
    if not spoken:
        spoken = re.sub(r"[ \t]{2,}", " ", _ACTION.sub("", re.sub(r"<[^>]*>|\[[^\]]*\]", "", raw or ""))).strip() or (raw or "").strip()
    return emotion, think, spoken


def format_for_client(think, spoken):
    """클라이언트는 <think> 안의 단서로 표정을 고른다."""
    return f"<think>{think}</think>{spoken}" if think else spoken


def clean_for_tts(spoken):
    """음성으로 읽으면 이상한 것들(지문, 이모지, 반복 기호)을 걷어낸다."""
    t = re.sub(r"\*[^*\n]*\*", " ", spoken)            # *행동* (대사에서는 이미 빠져 있지만 한 번 더)
    t = re.sub(r"\([^)\n]*\)", " ", t)                  # (지문)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F♡♥❤☆★]", "", t)
    t = re.sub(r"/{2,}", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    return t


# ---------------------------------------------------------------- 스트리밍 파서
class StreamParser:
    """LLM 이 글자를 조금씩 보내는 동안 [감정]<think>…</think>대사 를 실시간으로 나눈다.

    feed(조각) / finish() 가 이벤트 목록을 돌려준다:
      ("meta", emotion, think)  머리말(감정 태그, think)이 끝나는 순간 한 번
      ("text", 대사 조각)        그 뒤로 대사가 나올 때마다
    최종 결과는 서버가 전체 글자로 parse_reply 를 다시 돌려 확정하므로, 여기서는 '화면에 먼저 보여 줄 글자'만 정확하면 된다."""

    HOLD_BRACKET = 24   # '[' 뒤에 이 글자 수 안에 ']' 가 안 닫히면 태그가 아니라 그냥 글자로 본다
    HOLD_ACTION = 160   # '*' 뒤에 이 글자 수 안에 '*' 가 안 닫히면 행동 묘사가 아니라 그냥 글자로 본다

    def __init__(self, allowed=EMOTIONS, use_think=True):
        self.allowed = allowed
        self.use_think = use_think
        self.buf = ""
        self.pend = ""
        self.head = True
        self.started = False   # 대사가 한 글자라도 나왔는가 (맨 앞 공백/줄바꿈은 버린다)
        self.emotion = None
        self.thinks = []

    # ----- 머리말 -----
    def _head_step(self):
        """머리말을 더 읽을 수 있으면 True, 더 기다려야 하거나 끝났으면 False. 끝나면 self.head=False."""
        b = self.buf.lstrip()
        for j in _JUNK:
            if b.startswith(j):
                self.buf = b[len(j):]
                return True
        self.buf = b
        if not b:
            return False
        if b[0] == "[":
            mo = _EMO_TAG.match(b)
            if mo:
                e = norm_emotion(mo.group(1), self.allowed)
                if e:
                    self.emotion = self.emotion or e
                    self.buf = b[mo.end():]
                    return True
                self.head = False   # [참고] 같은 건 대사의 일부
                return False
            if "]" not in b and len(b) < self.HOLD_BRACKET:
                return False
            self.head = False
            return False
        if b[0] == "*":   # *행동 묘사* (머리말 자리에 오면 표정을 먼저 바꿀 수 있다)
            if b.startswith("**"):
                self.head = False
                return False
            k, nl = b.find("*", 1), b.find("\n")
            if k > 0 and (nl < 0 or nl > k):
                self.thinks.append(b[:k + 1].strip())
                self.buf = b[k + 1:]
                return True
            if k < 0 and nl < 0 and len(b) < self.HOLD_ACTION:
                return False
            self.head = False
            return False
        if b[0] == "<":
            low = b.lower()
            mo = _OPEN_TAG.match(b)
            if mo:
                cm = _CLOSE_TAG.search(b, mo.end())
                if not cm:
                    return False   # 닫는 태그를 기다린다 (끝까지 안 오면 finish 에서 *행동 묘사* 만 think 로 살리고 나머지는 대사로 본다)
                self.thinks.append(b[mo.end():cm.start()].strip())
                self.buf = b[cm.end():]
                return True
            if any(n.startswith(low) for n in _OPEN_NAMES) or any(j.startswith(low) for j in _JUNK):
                return False
        self.head = False
        return False

    def _meta(self):
        think = " ".join(t for t in self.thinks if t) if self.use_think else ""
        return ("meta", self.emotion or "neutral", think)

    # ----- 본문 -----
    def _body(self, text):
        out, buf = "", self.pend + text
        self.pend = ""
        while buf:
            m = re.search(r"[\[<*]", buf)
            if not m:
                out += buf
                break
            out += buf[:m.start()]
            buf = buf[m.start():]
            if buf[0] == "[":
                mo = _EMO_TAG.match(buf)
                if mo and norm_emotion(mo.group(1), self.allowed):
                    buf = buf[mo.end():]
                    continue
                if not mo and "]" not in buf and len(buf) < self.HOLD_BRACKET:
                    self.pend, buf = buf, ""
                    break
                out, buf = out + "[", buf[1:]
            elif buf[0] == "*":   # 대사 속 *행동 묘사* 는 화면/음성에서 뺀다 (최종 think 에는 서버가 모아 보낸다)
                if len(buf) == 1:
                    self.pend, buf = buf, ""
                    break
                if buf[1] == "*":   # **굵게** 는 그대로
                    out, buf = out + "**", buf[2:]
                    continue
                k, nl = buf.find("*", 1), buf.find("\n")
                if k > 0 and (nl < 0 or nl > k):
                    buf = buf[k + 1:]
                    continue
                if k < 0 and nl < 0 and len(buf) < self.HOLD_ACTION:
                    self.pend, buf = buf, ""
                    break
                out, buf = out + "*", buf[1:]
            else:
                low = buf.lower()
                mo = _OPEN_TAG.match(buf)
                if mo:
                    cm = _CLOSE_TAG.search(buf, mo.end())
                    if not cm:
                        self.pend, buf = buf, ""
                        break
                    buf = buf[cm.end():]
                    continue
                cm = _CLOSE_TAG.match(buf)   # 여는 태그 없이 나온 닫는 태그
                if cm:
                    buf = buf[cm.end():]
                    continue
                if any(n.startswith(low) for n in _OPEN_NAMES + _CLOSE_NAMES) or any(j.startswith(low) for j in _JUNK):
                    self.pend, buf = buf, ""
                    break
                hit = next((j for j in _JUNK if low.startswith(j)), None)
                if hit:
                    buf = buf[len(hit):]
                    continue
                out, buf = out + "<", buf[1:]
        return out

    def _emit(self, events, t):
        if not self.started:
            t = t.lstrip()
        if t:
            self.started = True
            events.append(("text", t))

    def feed(self, delta):
        events = []
        if self.head:
            self.buf += delta
            while self._head_step():
                pass
            if self.head:
                return events
            events.append(self._meta())
            text, self.buf = self.buf, ""
            delta = text
        self._emit(events, self._body(delta))
        return events

    def finish(self):
        events = []
        if self.head:
            b = self.buf.lstrip()
            mo = _OPEN_TAG.match(b)
            if mo:   # 닫히지 않은 think: 앞쪽 *행동 묘사* 만 think 로 보고, 나머지는 대사로 살린다 (없으면 전부 속마음)
                head, rest = split_leading_actions(b[mo.end():])
                if head:
                    self.thinks.append(head)
                    b = rest
                else:
                    self.thinks.append(b[mo.end():].strip())
                    b = ""
            self.head, self.buf = False, ""
            events.append(self._meta())
            self._emit(events, self._body(b))
        if self.pend and not _OPEN_TAG.match(self.pend):
            self._emit(events, self.pend)
        self.pend = ""
        return events


class SentenceChunker:
    """대사를 문장 단위로 끊는다 (문장마다 따로 음성을 만들어 먼저 재생하기 위해).
    너무 짧은 조각은 다음 문장과 합치고, 마침표 없이 너무 길어지면 쉼표/공백에서 자른다."""

    END = re.compile(r"[.!?。！？…~]+[\"'”’)\]」』]*(?=\s)|\n+")

    def __init__(self, min_chars=8, max_chars=140):
        self.min = min_chars
        self.max = max_chars
        self.buf = ""

    def feed(self, text):
        self.buf += text
        out = []
        while True:
            cut = None
            for m in self.END.finditer(self.buf):
                if len(self.buf[:m.end()].strip()) >= self.min:
                    cut = m.end()
                    break
            if cut is None and len(self.buf) > self.max:
                sp = max(self.buf.rfind(c, 0, self.max) for c in " ,，、")
                cut = sp + 1 if sp > self.min else self.max
            if cut is None:
                return out
            piece, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if piece:
                out.append(piece)

    def flush(self):
        piece, self.buf = self.buf.strip(), ""
        return [piece] if piece else []
