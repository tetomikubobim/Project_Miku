"""채팅 한 번의 처리: 기록 불러오기 -> 프롬프트 -> LLM -> 파싱 -> 저장 -> TTS. (웹 프레임워크와 무관)"""
import mimetypes
import os
import queue
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import llm as llm_mod
import memory as mem
import now_playing as now_playing_mod
import extras as extras_mod
import websearch
import tts as tts_mod
from db import Database
from reply import DEFAULT_EMOTIONS, SentenceChunker, StreamParser, canonical_emotion, clean_for_tts, finalize, format_for_client

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TAG = re.compile(r"^[A-Za-z가-힣]{1,20}$")   # 감정 태그 ([태그] 로 파싱되는 글자만)
PERSONA_MAX, EMOTIONS_MAX = 20000, 24
# 모델별 음성(GPT-SoVITS): 키 이름은 config.yaml 의 tts 설정과 같다. 있으면 tts 설정 위에 덮어쓴다.
VOICE_KEYS = ("gpt_weights_path", "sovits_weights_path", "ref_audio_path", "prompt_text", "prompt_lang")
_LANG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
SPEED_MIN, SPEED_MAX = 0.5, 2.0   # 모델별 음성 속도(voice.json 의 speed_factor) 범위. GPT-SoVITS 의 speed_factor 와 같은 뜻 (1.0 = 보통)


_BAD_DIR_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)$", re.IGNORECASE)


def safe_model_dir(name):
    """모델 이름 -> chat_log 아래 폴더 이름. 경로로 쓸 수 없는 글자는 _ 로 바꾸고, 쓸 수 없으면 None (기본 기록을 쓴다)."""
    n = _BAD_DIR_CHARS.sub("_", str(name or "")).strip().strip(".").strip()[:80]
    if not n:
        return None
    return "_" + n if _RESERVED.match(n) else n


def safe_image_path(upload_dir, image_id):
    """업로드 폴더 안의 파일만 허용 (경로 조작 방지). 없으면 None."""
    if not isinstance(image_id, str) or not _SAFE_ID.match(image_id):
        return None
    p = os.path.join(upload_dir, image_id)
    return p if os.path.isfile(p) else None


TITLE_MAX = 28
_NOTI_HINT = "\n(방금 위 알림이"


def make_title(text, kind="chat", app_name=""):
    """새 대화의 제목: 첫 메시지 앞부분 (알림이 먼저 오면 '알림 · 앱 이름')."""
    if kind == "notification":
        return f"알림 · {app_name or '시스템'}"[:TITLE_MAX + 6]
    t = " ".join((text or "").split())
    if not t:
        return "새 대화"
    return t if len(t) <= TITLE_MAX else t[:TITLE_MAX].rstrip() + "…"


def display_text(role, content, kind):
    """기록 화면용 본문. 알림 메시지에 붙여 둔 AI 지시문('(방금 위 알림이…)')은 보여 주지 않는다."""
    if kind == "notification" and role == "user":
        return content.split(_NOTI_HINT, 1)[0]
    return content


class Engine:
    def __init__(self, cfg, db, base_dir=".", llm_fn=None, tts_fn=None, now_fn=None, stream_fn=None, search_fn=None):
        self.cfg = cfg
        self.default_db = db   # 모델 이름 없이 쓸 때의 기록 (기존 miku.db)
        self.db = db           # 지금 쓰는 기록: 모델별로 chat_log/<모델>/miku.db (set_profile 이 바꾼다)
        self.base_dir = base_dir
        paths = cfg.get("paths", {})
        self.chat_log_dir = os.path.join(base_dir, paths.get("chat_log", "chat_log"))
        self._db_name = os.path.basename(paths.get("db", "miku.db")) or "miku.db"
        self._model_dbs = {}
        self.audio_dir = os.path.join(base_dir, paths.get("audio", "audio"))
        self.upload_dir = os.path.join(base_dir, paths.get("uploads", "uploads"))
        os.makedirs(self.audio_dir, exist_ok=True)
        os.makedirs(self.upload_dir, exist_ok=True)
        self.llm_fn = llm_fn or llm_mod.generate
        # 스트리밍은 진짜 LLM 을 쓸 때만 (llm_fn 을 따로 넣은 시험/대체 구현은 stream_fn 도 같이 넣어야 한다)
        self.stream_fn = stream_fn or (llm_mod.generate_stream if llm_fn is None else None)
        self._maint_lock = threading.Lock()
        self._search_injected = search_fn is not None   # 시험용으로 검색 함수를 넣었으면 ddgs 설치 여부와 상관없이 켠다
        self.search_fn = search_fn or websearch.search
        self.tts_fn = tts_fn or tts_mod.synthesize
        self.now_fn = now_fn or (lambda: datetime.now(ZoneInfo(cfg.get("timezone", "Asia/Seoul"))))
        # 서버를 켤 때마다 새 대화로 시작한다. 활성 세션이 None 이면 "아직 저장 전인 새 대화"이고,
        # 첫 메시지에 대한 응답이 성공하면 그때 세션이 만들어진다 (빈 세션이 쌓이지 않게).
        self.active_session = None
        # 클라이언트가 지금 띄운 Live2D 모델의 프로필 (persona.txt / emotionAliases). 없으면 서버 설정을 쓴다.
        self.profile = None

    # ----- 모델별 프로필 -----
    def set_profile(self, data):
        """클라이언트가 모델을 불러올 때/프로필을 고칠 때마다 보낸다. 이상한 값은 조용히 걸러낸다 (예외를 내지 않음).
        data: {model, persona, emotions: {태그: 설명}}  persona 가 비면 서버 persona.txt, emotions 가 비면 서버 기본 감정을 쓴다."""
        d = data if isinstance(data, dict) else {}
        persona = d.get("persona")
        persona = persona.strip()[:PERSONA_MAX] if isinstance(persona, str) else ""
        emotions = {}
        raw = d.get("emotions")
        if isinstance(raw, dict):
            for k, v in raw.items():
                tag = str(k).strip().lower()
                if _TAG.match(tag) and len(emotions) < EMOTIONS_MAX:
                    emotions[tag] = " ".join(str(v or "").split())[:60]
        voice = {}
        raw = d.get("voice")
        if isinstance(raw, dict):
            for k in VOICE_KEYS:
                v = " ".join(str(raw.get(k) or "").split())[:500]
                if v and (k != "prompt_lang" or _LANG.match(v)):
                    voice[k] = v
            sp = raw.get("speed_factor")
            try:   # 속도는 숫자 (문자열 "1.2" 도 받는다). true/false, 범위 밖, 숫자가 아닌 값은 버린다
                sp = None if isinstance(sp, bool) else float(sp)
            except (TypeError, ValueError):
                sp = None
            if sp is not None and SPEED_MIN <= sp <= SPEED_MAX:
                voice["speed_factor"] = round(sp, 2)
        model = str(d.get("model") or "")[:80]
        self.profile = {"model": model, "persona": persona, "emotions": emotions, "voice": voice} if (persona or emotions or voice) else None
        changed = self._use_model_db(model)
        return {"model": model, "persona": bool(persona), "emotions": list(emotions), "voice": list(voice), "db_changed": changed}

    def _tts_cfg(self):
        """tts 설정. 모델 프로필에 음성(참조 음성/가중치/속도)이 있으면 그걸로 덮어쓴다. 모델 자신의 참조 음성을 쓰면
        서버 설정의 감정별 참조 음성(emotion_refs)은 다른 목소리의 것이니 쓰지 않는다."""
        base = self.cfg.get("tts", {})
        voice = (self.profile or {}).get("voice") or {}
        if not voice:
            return base
        speed = voice.get("speed_factor")
        merged = {**base, **{k: v for k, v in voice.items() if k != "speed_factor"}}
        if voice.get("ref_audio_path"):
            merged["emotion_refs"] = {}
        if speed:   # 엔진마다 속도 표현이 달라서 둘 다 맞춰 준다: GPT-SoVITS 는 params.speed_factor, Edge 는 rate(%)
            merged["params"] = {**(base.get("params") or {}), "speed_factor": speed}
            merged["edge"] = {**(base.get("edge") or {}), "rate": f"{round((speed - 1) * 100):+d}%"}
        return merged

    # ----- 모델별 대화 기록: chat_log/<모델 폴더 이름>/miku.db (세션, 메시지, 요약, 기억이 모델마다 따로) -----
    def _use_model_db(self, model):
        """이 모델의 기록으로 바꾼다. 바뀌었으면 True (이때 새 대화 상태로 돌아간다). 모델 이름이 없으면 기본 기록."""
        key = safe_model_dir(model)
        try:
            target = self._model_db(key) if key else self.default_db
        except (OSError, sqlite3.Error) as e:
            print(f"[chat_log] {model} 기록을 열지 못했어요 ({e}) — 기존 기록을 계속 써요")
            return False
        if target is self.db:
            return False
        got = self._maint_lock.acquire(timeout=20)   # 돌고 있는 요약/기억 정리가 끝난 뒤에 바꾼다 (낡은 기록에 쓰지 않게)
        try:
            self.db = target
            self.active_session = None
        finally:
            if got:
                self._maint_lock.release()
        return True

    def _model_db(self, key):
        k = key.lower()   # Windows 는 폴더 이름의 대소문자를 구분하지 않는다
        db = self._model_dbs.get(k)
        if db is None:
            first_ever = not os.path.isdir(self.chat_log_dir)
            path = os.path.join(self.chat_log_dir, key, self._db_name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if first_ever and not os.path.exists(path) and self._has_data(self.default_db):
                # 모델별 기록을 처음 쓰는 날: 지금까지의 기록(기본 miku.db)을 처음 접속한 모델의 기록으로 복사한다 (원본은 그대로 둔다)
                dst = sqlite3.connect(path)
                try:
                    with self.default_db.lock:
                        self.default_db.conn.backup(dst)
                finally:
                    dst.close()
                print(f"[chat_log] 기존 대화 기록을 {key} 로 옮겼어요 (원본은 그대로 남겨 뒀어요)")
            db = self._model_dbs[k] = Database(path)
        return db

    @staticmethod
    def _has_data(db):
        try:
            return bool(db.list_sessions() or db.list_memories())
        except Exception:  # noqa: BLE001
            return False

    # ----- 프롬프트 -----
    def _persona(self):
        text = (self.profile or {}).get("persona") or ""
        name = self.cfg.get("persona_file", "persona.txt")
        if not text:
            try:
                with open(os.path.join(self.base_dir, name), encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                text = "당신은 AI 캐릭터입니다. 사용자와 친근하게 대화하세요."
        return text   # 호칭/이름은 persona.txt 에 직접 적는다

    def emotion_table(self):
        """사용할 감정 {이름: 설명}. 모델 프로필의 감정 태그가 있으면 그것, 없으면 config.yaml 의 emotions (목록 또는 '이름: 설명' 표).
        neutral 은 항상 포함. 설명이 비어 있으면 기본 감정의 설명(anger -> angry 처럼 같은 뜻 포함)을 쓴다."""
        prof = (self.profile or {}).get("emotions")
        if prof:
            table = {k: (v or DEFAULT_EMOTIONS.get(canonical_emotion(k), "")) for k, v in prof.items()}
            table.setdefault("neutral", DEFAULT_EMOTIONS["neutral"])
            return table
        e = self.cfg.get("emotions")
        if isinstance(e, dict) and e:
            table = {str(k).strip().lower(): str(v or "").strip() for k, v in e.items()}
        elif isinstance(e, list) and e:
            table = {str(k).strip().lower(): "" for k in e}
        else:
            table = dict(DEFAULT_EMOTIONS)
        table.setdefault("neutral", DEFAULT_EMOTIONS["neutral"])
        return table

    def system_prompt(self, session_id=None, web="", playing=""):
        c = self.cfg
        now = self.now_fn().strftime("%Y-%m-%d %H:%M:%S")
        rules = [
            "[응답 형식 규칙]",
            "1. 맨 앞에 지금 감정을 대괄호 하나로 적습니다. 아래 목록에서만 고르세요.",
        ] + [f"   [{k}] {v}".rstrip() for k, v in self.emotion_table().items()]
        if c.get("use_think", True):
            rules.append("2. 이어서 표정/동작 묘사를 별표(*)로 감싸 한 번만 반드시 한국어(한글)로 짧게 적습니다. 영어로 쓰지 않습니다. 속마음이나 생각은 쓰지 않고, <think> 같은 태그도 쓰지 않습니다. 예: *고개를 숙이며 볼이 살짝 붉어진다*")
            rules.append("3. 그 다음에 실제로 소리 내어 말할 대사만 적습니다. 대사 안에는 괄호 지문이나 *행동* 표현을 쓰지 않습니다.")
        else:
            rules.append("2. 그 다음에 실제로 소리 내어 말할 대사만 적습니다. 대사 안에는 괄호 지문이나 *행동* 표현을 쓰지 않습니다.")
        extra = []
        if mem.enabled(c):
            ms = mem.settings(c)
            extra.append(mem.prompt_block(self.db.list_memories(), ms["max_items"]))
            if ms["summary"]:
                extra.append(mem.summary_block(self.db.get_summary(session_id)[0]))
        return "\n\n".join(p for p in [
            self._persona(),
            f"현재 시간은 {now} 입니다. 시간 정보가 필요하면 참고해서 대화하세요.",
            *extra,
            web,
            playing,
            "\n".join(rules),
        ] if p)

    def _history(self, sid):
        """문맥으로 넣을 이 대화의 기록. 요약이 켜져 있으면 요약에 들어간 앞부분은 빼고 그 뒤 원문만."""
        after = 0
        if mem.enabled(self.cfg) and mem.settings(self.cfg)["summary"]:
            after = self.db.get_summary(sid)[1]
        return self.db.recent(self.cfg.get("history_turns", 40), sid, after_id=after)

    # ----- 공통 -----
    def _respond(self, user_text, kind, images, title=None, now_playing=None, extra=""):
        sid = self.active_session if self.db.session_exists(self.active_session) else None
        history = self._history(sid)  # 이 대화의 기록만 문맥으로 쓴다
        messages = history + [{"role": "user", "content": user_text}]
        web = self._web_context(user_text, history, bool(images)) if kind == "chat" else ""
        raw = self.llm_fn(
            self.cfg["llm"], self.system_prompt(sid, web, self._playing(now_playing, extra)), messages, images or None,
            max_tokens=self.cfg.get("max_output_tokens", 1024), temperature=self.cfg.get("temperature", 0.9),
        )
        if not raw or not raw.strip():
            raise llm_mod.LLMError("LLM 이 빈 응답을 돌려줬어요")
        emotion, think, spoken = finalize(raw, tuple(self.emotion_table()), self.cfg.get("use_think", True))
        if sid is None:
            sid = self.db.create_session(title or make_title(user_text, kind))
            self.active_session = sid
        self.db.add("user", user_text, kind=kind, session_id=sid)
        self.db.add("assistant", spoken, raw=raw.strip(), emotion=emotion, kind=kind, session_id=sid)

        audio_name = None
        speakable = clean_for_tts(spoken)
        if speakable:
            audio_name = self.tts_fn(self._tts_cfg(), speakable, canonical_emotion(emotion), self.audio_dir)
        self._spawn_maintenance(sid)
        return {
            "llm_response": format_for_client(think, spoken),
            "emotion": emotion,
            "audio_filename": audio_name,
            "should_speak": True,
            "session_id": sid,
            "session_title": (self.db.get_session(sid) or {}).get("title", ""),
        }

    def _load_images(self, image_ids):
        out, used = [], []
        for iid in image_ids or []:
            p = safe_image_path(self.upload_dir, iid)
            if not p:
                continue
            mime = mimetypes.guess_type(p)[0] or "image/jpeg"
            with open(p, "rb") as f:
                out.append((mime, f.read()))
            used.append(p)
        return out, used

    @staticmethod
    def _delete(paths):
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass

    # ----- 진입점 (블로킹: 스레드에서 호출) -----
    def _playing(self, now_playing, extra=""):
        """이번 턴에만 시스템 프롬프트에 붙는 글: 재생 중인 곡 + 부가 정보(일정/클립보드/기념일/회상)."""
        return "\n\n".join(x for x in (now_playing_mod.block(now_playing), extra) if x)

    def _extras(self, text, context):
        """이번 턴의 부가 정보 글과, 답이 성공하면 '축하함'으로 표시할 기념일. 어느 하나가 실패해도 대화는 계속한다."""
        parts, due = [], []
        today = self.now_fn().date()
        try:
            ctx = extras_mod.sanitize(context)
            parts += [extras_mod.schedule_block(ctx["schedules"]), extras_mod.clipboard_block(ctx["clipboard"])]
            due = extras_mod.due_anniversaries(self.db, today)
            parts.append(extras_mod.anniversary_block(due, today))
            if mem.enabled(self.cfg):
                sid = self.active_session if self.db.session_exists(self.active_session) else None
                floor = self.db.recent_floor_id(self.cfg.get("history_turns", 40), sid, self._summary_upto(sid))
                who, char = self._names()
                parts.append(extras_mod.recall_block(self.db, text, extras_mod.recall_settings(self.cfg), sid, floor, who, char))
        except Exception as e:  # noqa: BLE001
            print(f"[extras] 부가 정보를 만들지 못했어요: {e!r}")
        return "\n\n".join(p for p in parts if p), (due, today)

    def _summary_upto(self, sid):
        if sid is not None and mem.enabled(self.cfg) and mem.settings(self.cfg)["summary"]:
            return self.db.get_summary(sid)[1]
        return 0

    def _done(self, mark):
        due, today = mark
        if due:
            try:
                extras_mod.mark_anniversaries(self.db, due, today)
            except Exception as e:  # noqa: BLE001
                print(f"[extras] 기념일 표시 실패: {e!r}")

    def chat(self, text, image_ids=None, now_playing=None, context=None):
        text = (text or "").strip()
        images, used = self._load_images(image_ids)
        if not text and not images:
            return None
        try:
            extra, mark = self._extras(text, context)
            res = self._respond(text or "(화면을 보냈어요)", "chat", images, now_playing=now_playing_mod.sanitize(now_playing), extra=extra)
            self._done(mark)
            return res
        finally:
            self._delete(used)  # 업로드한 이미지는 한 번 쓰고 지움

    def notification(self, app_name, content, image_id=None):
        content = (content or "").strip()
        images, used = self._load_images([image_id] if image_id else [])
        if not content and not images:
            return None
        try:
            msg = f"[알림] {app_name or '시스템'}: {content}\n(방금 위 알림이 도착했어요. 상황에 맞게 자연스럽게 한마디 반응하세요.)"
            return self._respond(msg, "notification", images, title=make_title("", "notification", app_name))
        finally:
            self._delete(used)

    def proactive(self, image_ids=None):
        """사용자가 말하지 않았는데 캐릭터가 먼저 거는 말. 클라이언트의 '먼저 말 걸기' 타이머가 부른다.
        시스템이 만든 안내 메시지(kind='proactive')를 사용자 턴으로 넣어 AI 에게 보여 주고, 화면 기록에서는 숨긴다.
        image_ids: 화면 공유 모드일 때 클라이언트가 같이 올린 지금 화면 (한 번 쓰고 지운다)."""
        images, used = self._load_images(image_ids)
        try:
            return self._proactive(images)
        finally:
            self._delete(used)

    def _proactive(self, images):
        sid = self.active_session if self.db.session_exists(self.active_session) else None
        last = self.db.last_ts(sid)
        if last is None:
            situation = "지금 막 대화를 시작하는 참이야. 이 대화에는 아직 주고받은 말이 없어."
        else:
            minutes = max(1, round((self.now_fn().timestamp() - last) / 60))
            situation = f"마지막으로 말을 주고받은 지 약 {minutes}분이 지났어."
        msg = (
            f"[시스템] {situation} 사용자는 지금 컴퓨터 앞에 있어. 먼저 가볍게 말을 걸어줘. "
            "질문을 억지로 던지지 말고, 직전에 나눈 이야기를 이어가거나 지금 시간대에 어울리는 한마디를 해. "
            "방금까지 한 말을 반복하지 마. 이 메시지는 사용자가 쓴 게 아니라 시스템이 보낸 거니까, "
            "사용자가 말했다고 착각하면 안 돼."
        )
        if images:
            msg += (
                " 참고로 화면 공유 모드가 켜져 있어서 사용자의 지금 화면을 같이 보내. "
                "화면에 보이는 것이 말을 걸 만한 거리면 자연스럽게 한마디 해도 되지만, 억지로 설명하거나 훑어 읽듯이 말하지 마."
            )
        return self._respond(msg, "proactive", images or None, title="먼저 말 걸기")

    # ----- 세션 관리 (블로킹: 스레드에서 호출) -----
    @staticmethod
    def _sid(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError("올바르지 않은 대화 번호예요") from None

    def _require(self, value):
        sid = self._sid(value)
        if not self.db.session_exists(sid):
            raise ValueError("없는 대화예요 (이미 지워졌을 수 있어요)")
        return sid

    def _messages(self, sid, limit=200):
        return [
            {"id": m["id"], "role": m["role"], "kind": m["kind"], "emotion": m["emotion"], "ts": m["ts"],
             "content": display_text(m["role"], m["content"], m["kind"])}
            for m in self.db.messages(sid, limit)
            if not (m["role"] == "user" and m["kind"] == "proactive")  # 시스템이 보낸 지시문은 기록에 보이지 않는다
        ]

    def list_sessions(self):
        active = self.active_session if self.db.session_exists(self.active_session) else None
        return {"sessions": self.db.list_sessions(), "active": active}

    def new_session(self):
        """새 대화로 전환. 실제 세션은 첫 메시지를 보낼 때 만들어진다."""
        self.active_session = None
        return {"session_id": None, "title": "", "messages": []}

    def switch_session(self, session_id):
        sid = self._require(session_id)
        self.active_session = sid
        return {"session_id": sid, "title": self.db.get_session(sid)["title"], "messages": self._messages(sid)}

    def session_messages(self, session_id):
        sid = self._require(session_id)
        return {"session_id": sid, "messages": self._messages(sid)}

    def rename_session(self, session_id, title):
        sid = self._require(session_id)
        title = " ".join(str(title or "").split())[:60]
        if not title:
            raise ValueError("이름이 비어 있어요")
        self.db.rename_session(sid, title)
        return {"session_id": sid, "title": title}

    def delete_session(self, session_id):
        """지운 대화가 지금 대화였다면 새 대화 상태로 돌아간다."""
        sid = self._require(session_id)
        self.db.delete_session(sid)
        was_active = self.active_session == sid
        if was_active:
            self.active_session = None
        return {"session_id": sid, "was_active": was_active}

    def delete_all_sessions(self):
        """모든 대화를 지우고 새 대화 상태로 돌아간다."""
        n = self.db.delete_all_sessions()
        self.active_session = None
        return {"deleted": n}

    # ----- 스트리밍 -----
    def can_stream(self):
        return self.stream_fn is not None and self.cfg.get("stream", True) is not False

    def chat_stream(self, text, image_ids=None, emit=None, cancel=None, now_playing=None, context=None):
        """chat() 과 같지만 답을 만드는 동안 emit({...}) 으로 진행 상황을 보낸다 (블로킹: 스레드에서 호출).
        cancel(threading.Event)을 주고 밖에서 set() 하면 답 만들기를 멈추고 LLMError 를 낸다 (사용자가 중단 버튼을 누른 경우). 중단된 대화는 저장하지 않는다.
        stream_start -> stream_meta{emotion,think} -> stream_text{delta} ... / stream_audio{seq,audio_filename,text} (문장마다)
        마지막에 돌려주는 값은 chat() 과 같은 결과(streamed=True, audio_filename=None): 이미 문장별로 음성을 보냈다."""
        text = (text or "").strip()
        images, used = self._load_images(image_ids)
        if not text and not images:
            return None
        try:
            extra, mark = self._extras(text, context)
            res = self._respond_stream(text or "(화면을 보냈어요)", images, emit or (lambda ev: None), cancel, now_playing_mod.sanitize(now_playing), extra)
            self._done(mark)
            return res
        finally:
            self._delete(used)

    def _respond_stream(self, user_text, images, emit, cancel=None, now_playing=None, extra=""):
        sid = self.active_session if self.db.session_exists(self.active_session) else None
        history = self._history(sid)
        messages = history + [{"role": "user", "content": user_text}]
        allowed = tuple(self.emotion_table())
        use_think = self.cfg.get("use_think", True)
        parser, chunker = StreamParser(allowed, use_think), SentenceChunker()
        stream_id = uuid.uuid4().hex[:12]
        tts_q, abort = queue.Queue(), (cancel if cancel is not None else threading.Event())   # 밖(중단 버튼)에서도 같은 이벤트로 멈출 수 있다
        state = {"emotion": "neutral", "seq": 0, "spoke": False}

        def safe_emit(ev):
            if abort.is_set():   # 중단된 뒤에는 더 보내지 않는다 (음성 만들던 중이던 문장 포함)
                return
            try:
                emit({**ev, "stream_id": stream_id})
            except Exception as e:  # noqa: BLE001  연결이 끊긴 경우 등: 더 만들 필요 없다
                abort.set()
                print(f"[stream] 전송 실패, 중단해요: {e!r}")

        def tts_worker():
            while True:
                item = tts_q.get()
                if item is None:
                    return
                if abort.is_set():
                    continue
                piece, emo = item
                speakable = clean_for_tts(piece)
                if not speakable:
                    continue
                try:
                    name = self.tts_fn(self._tts_cfg(), speakable, canonical_emotion(emo), self.audio_dir)
                except Exception as e:  # noqa: BLE001  음성 실패는 글자로 계속
                    print(f"[stream] 음성 만들기 실패: {e!r}")
                    continue
                if name:
                    safe_emit({"op": "stream_audio", "seq": state["seq"], "audio_filename": name, "text": piece})
                    state["seq"] += 1

        worker = threading.Thread(target=tts_worker, daemon=True)
        worker.start()

        def handle(events):
            for ev in events:
                if ev[0] == "meta":
                    state["emotion"] = ev[1]
                    safe_emit({"op": "stream_meta", "emotion": ev[1], "think": ev[2]})
                elif ev[0] == "text":
                    state["spoke"] = True
                    safe_emit({"op": "stream_text", "delta": ev[1]})
                    for piece in chunker.feed(ev[1]):
                        tts_q.put((piece, state["emotion"]))

        parts = []
        try:
            safe_emit({"op": "stream_start"})
            if abort.is_set():
                raise llm_mod.LLMError("사용자가 중단했어요")
            web = self._web_context(user_text, history, bool(images), notify=lambda q: safe_emit({"op": "stream_search", "query": q}))
            if abort.is_set():   # 검색하는 동안 중단됨: 답을 만들기 시작하지 않는다
                raise llm_mod.LLMError("사용자가 중단했어요")
            system = self.system_prompt(sid, web, self._playing(now_playing, extra))
            for delta in self.stream_fn(
                self.cfg["llm"], system, messages, images or None,
                max_tokens=self.cfg.get("max_output_tokens", 1024), temperature=self.cfg.get("temperature", 0.9),
            ):
                if abort.is_set():
                    raise llm_mod.LLMError("클라이언트 연결이 끊겨서 중단했어요")
                parts.append(delta)
                handle(parser.feed(delta))
            handle(parser.finish())
            if state["spoke"]:
                tail = chunker.flush()
            else:
                # 형식이 어긋나 스트리밍 중에는 대사가 하나도 안 나온 경우: 최종 분해로 건진 대사를 지금 읽는다 (안 그러면 음성이 통째로 빠진다)
                _, _, late = finalize("".join(parts), allowed, use_think)
                tail = chunker.feed(late) + chunker.flush()
            for piece in tail:
                tts_q.put((piece, state["emotion"]))
        except BaseException:
            abort.set()
            raise
        finally:
            tts_q.put(None)
            worker.join()
        if abort.is_set():
            raise llm_mod.LLMError("클라이언트 연결이 끊겨서 중단했어요")

        raw = "".join(parts)
        if not raw.strip():
            raise llm_mod.LLMError("LLM 이 빈 응답을 돌려줬어요")
        emotion, think, spoken = finalize(raw, allowed, use_think)
        if sid is None:
            sid = self.db.create_session(make_title(user_text, "chat"))
            self.active_session = sid
        self.db.add("user", user_text, kind="chat", session_id=sid)
        self.db.add("assistant", spoken, raw=raw.strip(), emotion=emotion, kind="chat", session_id=sid)
        self._spawn_maintenance(sid)
        return {
            "llm_response": format_for_client(think, spoken),
            "emotion": emotion,
            "audio_filename": None,
            "should_speak": True,
            "streamed": True,
            "stream_id": stream_id,
            "session_id": sid,
            "session_title": (self.db.get_session(sid) or {}).get("title", ""),
        }

    # ----- 웹 검색 (답을 만들기 전에) -----
    def _search_on(self):
        return websearch.settings(self.cfg)["enabled"] is not False and (self._search_injected or websearch.available())

    def _web_context(self, user_text, history, has_images=False, notify=None):
        """필요하다고 판단되면 검색해서 프롬프트에 넣을 글을 돌려준다 (아니면 빈 글자). 어떤 실패도 대화를 막지 않는다."""
        if has_images or not self._search_on() or not websearch.worth_checking(user_text):
            return ""
        now_text = self.now_fn().strftime("%Y-%m-%d %H:%M")
        try:
            raw = self.llm_fn(self._aux_llm(), "당신은 검색 필요 여부를 판단하는 도우미입니다.",
                              [{"role": "user", "content": websearch.decision_prompt(user_text, history, now_text)}], None,
                              max_tokens=150, temperature=0)
            plan = websearch.parse_decision(raw)
        except Exception as e:  # noqa: BLE001
            print(f"[search] 검색 판단 실패(검색 없이 답해요): {e!r}")
            return ""
        if plan is None:
            return ""
        query, news = plan
        if notify:
            try:
                notify(query)
            except Exception:  # noqa: BLE001
                pass
        try:
            results = self.search_fn(query, self.cfg, news=news)
        except Exception as e:  # noqa: BLE001
            print(f"[search] 검색 실패: {e}")
            return ("[웹 검색 결과] (인터넷 검색을 시도했지만 실패했어요.) 최신 정보는 확인하지 못했다고 솔직하게 말하고, "
                    "아는 범위에서만 답하세요. 모르는 내용은 지어내지 마세요.")
        return websearch.prompt_block(query, results, now_text)

    # ----- 장기 기억 / 요약 (대화가 끝난 뒤 백그라운드에서) -----
    def _spawn_maintenance(self, sid):
        if mem.enabled(self.cfg) and sid is not None:
            threading.Thread(target=self.maintain, args=(sid, self.db), daemon=True).start()

    @staticmethod
    def _names():
        """기억 정리 프롬프트와 대화 내보내기에서 쓰는 호칭. 설정으로 바꾸지 않는다 (호칭은 persona.txt 에 적는다)."""
        return "사용자", "캐릭터"

    def _aux_llm(self):
        """검색 판단/요약/기억 추출 같은 짧은 보조 호출용 LLM 설정: 사고 수준을 꺼서 빠르고 토큰이 모자라 잘리지 않게 한다."""
        return {**self.cfg["llm"], "reasoning_level": "off"}

    def _ask(self, prompt, max_tokens):
        raw = self.llm_fn(self._aux_llm(), "당신은 정확하고 간결한 기록 담당자입니다.", [{"role": "user", "content": prompt}], None,
                          max_tokens=max_tokens, temperature=0.2)
        return (raw or "").strip()

    def maintain(self, sid, db=None):
        """긴 대화 요약 + 새 기억 추출 (블로킹). 이미 다른 정리가 돌고 있으면 건너뛴다. 실패해도 예외는 내지 않는다.
        db: 이 정리를 시작할 때의 기록. 그 사이 모델이 바뀌었으면(sid 가 다른 기록의 것이니) 건너뛴다."""
        if not mem.enabled(self.cfg) or not self._maint_lock.acquire(blocking=False):
            return False
        try:
            if db is not None and db is not self.db:
                return False
            ms = mem.settings(self.cfg)
            if ms["summary"]:
                try:
                    self._summarize(sid, ms)
                except Exception as e:  # noqa: BLE001
                    print(f"[memory] 대화 요약 실패: {e!r}")
            if ms["extract_every"] > 0:
                try:
                    self._extract(sid, ms)
                except Exception as e:  # noqa: BLE001
                    print(f"[memory] 기억 추출 실패: {e!r}")
            if ms["consolidate"]:
                try:
                    self._consolidate(ms)
                except Exception as e:  # noqa: BLE001
                    print(f"[memory] 기억 합치기 실패: {e!r}")
            return True
        finally:
            self._maint_lock.release()

    def _summarize(self, sid, ms):
        old_summary, upto = self.db.get_summary(sid)
        msgs = self.db.messages_after(sid, upto)
        keep = ms["keep_turns"] * 2
        if len(msgs) <= keep + ms["summary_batch"]:
            return False
        cut = len(msgs) - keep
        while cut < len(msgs) and msgs[cut]["role"] != "user":   # 남는 원문은 사용자 말로 시작해야 한다
            cut += 1
        if cut >= len(msgs) or cut == 0:
            return False
        who, char = self._names()
        clipped = [{"role": m["role"], "content": str(m["content"])[:600]} for m in msgs[:cut]]
        summary = self._ask(mem.summary_prompt(old_summary, clipped, who, char), 800)
        if not summary:
            return False
        self.db.set_summary(sid, summary, msgs[cut - 1]["id"])
        return True

    def _extract(self, sid, ms):
        key = f"memcur:{sid}"
        cursor = int(self.db.kv_get(key, 0) or 0)
        msgs = self.db.messages_after(sid, cursor)
        if sum(1 for m in msgs if m["role"] == "user" and m["kind"] == "chat") < ms["extract_every"]:
            return False
        msgs = msgs[-40:]
        who, char = self._names()
        clipped = [{"role": m["role"], "content": str(m["content"])[:400]} for m in msgs]
        ops = mem.parse_ops(self._ask(mem.extraction_prompt(self.db.list_memories(), clipped, who, char, self.db.list_anniversaries()), 900))
        mem.apply_ops(self.db, ops, ms["max_items"])
        self.db.kv_set(key, msgs[-1]["id"])   # 성공했을 때만 앞으로 옮긴다 (실패하면 다음에 다시)
        return True

    def _consolidate(self, ms):
        """기억이 max_items 를 넘으면 LLM 이 비슷한 것을 합치고 불필요한 것만 지운다 (오래된 순으로 그냥 지우지 않는다).
        실패하거나 충분히 못 줄여도 기억은 그대로 두고, 일정 시간(CONSOLIDATE_COOLDOWN) 뒤에 다시 시도한다."""
        mems = self.db.list_memories()
        if len(mems) <= ms["max_items"]:
            self.db.kv_set("memcons_at", 0)   # 정상 상태로 돌아왔으니 다음에 넘치면 바로 정리할 수 있다
            return False
        now = self.now_fn().timestamp()
        last = float(self.db.kv_get("memcons_at", 0) or 0)
        if last and now - last < mem.CONSOLIDATE_COOLDOWN:
            return False
        self.db.kv_set("memcons_at", now)   # 성공/실패와 상관없이 기록: 실패해도 매 대화마다 LLM 을 부르지 않는다
        target = max(1, int(ms["max_items"] * 0.8))   # 목표보다 조금 더 줄여 두면 정리를 또 하지 않아도 된다
        who, _ = self._names()
        plan = mem.parse_consolidation(self._ask(mem.consolidation_prompt(mems, target, who), 2000))
        reduced, deleted = mem.apply_consolidation(self.db, plan)
        left = len(self.db.list_memories())
        print(f"[memory] 기억 정리: {len(mems)}개 -> {left}개 (합침 {reduced}, 삭제 {deleted})")
        if left <= ms["max_items"]:
            self.db.kv_set("memcons_at", 0)
        return reduced + deleted > 0

    # ----- 기억 관리 (설정 창의 '기억' 탭) -----
    def memories(self):
        return {"memories": self.db.list_memories()}

    def add_memory(self, text):
        t = mem.clean_text(text)
        if not t:
            raise ValueError("기억할 내용이 비어 있어요")
        self.db.add_memory(t)
        mem.apply_ops(self.db, {"add": [], "update": [], "delete": []}, mem.settings(self.cfg)["max_items"])
        return self.memories()

    def update_memory(self, memory_id, text):
        t = mem.clean_text(text)
        if not t:
            raise ValueError("기억할 내용이 비어 있어요")
        if not self.db.update_memory(self._sid(memory_id), t):
            raise ValueError("없는 기억이에요 (이미 지워졌을 수 있어요)")
        return self.memories()

    def delete_memory(self, memory_id):
        self.db.delete_memory(self._sid(memory_id))
        return self.memories()

    def clear_memories(self):
        self.db.delete_all_memories()
        return self.memories()

    # ----- 기념일 -----
    def anniversaries(self):
        return {"anniversaries": self.db.list_anniversaries()}

    def add_anniversary(self, month, day, label, year=None):
        a = mem.clean_anniversary({"month": month, "day": day, "label": label, "year": year})
        if not a:
            raise ValueError("기념일 날짜(월/일)나 이름이 올바르지 않아요")
        self.db.add_anniversary(a["month"], a["day"], a["label"], a["year"])
        return self.anniversaries()

    def delete_anniversary(self, ann_id):
        self.db.delete_anniversary(int(ann_id))
        return self.anniversaries()

    # ----- 검색 / 내보내기 -----
    @staticmethod
    def _snippet(text, query, width=36):
        t = " ".join(str(text or "").split())
        i = t.lower().find((query or "").lower())
        if i < 0:
            return t[:width * 2]
        a, b = max(0, i - width), min(len(t), i + len(query) + width)
        return ("…" if a else "") + t[a:b] + ("…" if b < len(t) else "")

    def search(self, query):
        q = " ".join(str(query or "").split())
        if not q:
            return {"query": "", "results": []}
        rows = self.db.search(q)
        return {"query": q, "results": [
            {"session_id": r["session_id"], "title": r["title"], "updated": r["updated"], "role": r["role"],
             "snippet": self._snippet(r["snippet"], q) if r["message_id"] is not None else ""} for r in rows]}

    def _fmt_ts(self, ts):
        return datetime.fromtimestamp(ts, self.now_fn().tzinfo).strftime("%Y-%m-%d %H:%M")

    def export(self, session_id=None, fmt="md"):
        """대화를 글로 내보낸다. session_id 가 None 이면 모든 대화. 돌려주는 값: {filename, text, count}"""
        fmt = "txt" if fmt == "txt" else "md"
        who, char = self._names()
        if session_id is None:
            sessions = sorted(self.db.list_sessions(), key=lambda s: s["created"])
        else:
            s = self.db.get_session(self._require(session_id))
            sessions = [s]
        if not sessions:
            raise ValueError("내보낼 대화가 없어요")
        blocks, total = [], 0
        for s in sessions:
            msgs = self.db.all_messages(s["id"])
            total += len(msgs)
            title = s["title"] or "(제목 없음)"
            lines = []
            for m in msgs:
                body = display_text(m["role"], m["content"], m["kind"]).strip()
                if m["kind"] == "notification" and m["role"] == "user":
                    who_label, body = "알림", re.sub(r"^\[알림\]\s*", "", body)
                else:
                    who_label = who if m["role"] == "user" else char
                lines.append((m["ts"], who_label, body))
            if fmt == "md":
                head = f"# {title}\n\n> {self._fmt_ts(s['created'])} · 메시지 {len(msgs)}개\n"
                body = "\n\n".join(f"**{w}**: {b}" for _, w, b in lines)
            else:
                head = f"=== {title} ({self._fmt_ts(s['created'])}) ==="
                body = "\n".join(f"[{self._fmt_ts(t)}] {w}: {b}" for t, w, b in lines)
            blocks.append(head + "\n\n" + body if body else head)
        text = ("\n\n---\n\n" if fmt == "md" else "\n\n").join(blocks) + "\n"
        day = self.now_fn().strftime("%Y%m%d")
        label = "전체" if len(sessions) > 1 else re.sub(r'[\\/:*?"<>|\s]+', "_", sessions[0]["title"] or "대화").strip("_")[:40] or "대화"
        return {"filename": f"miku_{label}_{day}.{fmt}", "text": text, "count": total}

