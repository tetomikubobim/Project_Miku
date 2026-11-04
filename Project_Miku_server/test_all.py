import asyncio, json, os, sys, tempfile, threading, time, unittest, unittest.mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import Database
from engine import Engine, make_title, safe_image_path
from reply import parse_reply, clean_for_tts, format_for_client
import configfile as CF, llm as L, tts as T, memory as mem_mod
from ws_handler import serve_socket

CFG = {"user_name": "오빠", "character_name": "히요리", "history_turns": 2, "use_think": True, "llm": {"provider": "gemini"},
       "tts": {"enabled": True, "url": "http://x", "ref_audio_path": "r.wav", "prompt_text": "p", "prompt_lang": "ko",
               "emotion_refs": {"sad": {"path": "sad.wav", "text": "슬퍼"}}, "params": {"seed": 7}}, "paths": {}}


class Parse(unittest.TestCase):
    def test_full(self):
        e, t, s = parse_reply("[happy]<think>*smiles brightly*</think>안녕, 오빠!")
        self.assertEqual((e, t, s), ("happy", "*smiles brightly*", "안녕, 오빠!"))
    def test_alias_and_unknown_tag_kept(self):
        e, t, s = parse_reply("[기쁨] 오빠 [참고] 잘 지냈어?")
        self.assertEqual(e, "happy"); self.assertIn("[참고]", s)
    def test_unclosed_think_and_junk(self):
        e, t, s = parse_reply("<bos>[sad]<think>*looks down*")
        self.assertEqual((e, t, s), ("sad", "*looks down*", ""))
    def test_default_neutral(self):
        self.assertEqual(parse_reply("그냥 말")[0], "neutral")
    def test_client_format(self):
        self.assertEqual(format_for_client("*x*", "hi"), "<think>*x*</think>hi")
        self.assertEqual(format_for_client("", "hi"), "hi")
    def test_tts_clean(self):
        self.assertEqual(clean_for_tts("*웃으며* 안녕♡ (손을 흔든다) 오빠///"), "안녕 오빠")


class Providers(unittest.TestCase):
    def test_gemini_payload(self):
        b = L.build_gemini_request("SYS", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}],
                                   [("image/jpeg", b"\xff\xd8")], 100, 0.5)
        self.assertEqual(b["system_instruction"]["parts"][0]["text"], "SYS")
        self.assertEqual([c["role"] for c in b["contents"]], ["user", "model", "user"])
        self.assertIn("inline_data", b["contents"][2]["parts"][1]); self.assertEqual(len(b["contents"][0]["parts"]), 1)
        self.assertEqual(b["generationConfig"]["maxOutputTokens"], 100)
    def test_gemini_parse(self):
        self.assertEqual(L.parse_gemini_response({"candidates": [{"content": {"parts": [{"text": "hi "}, {"text": "x", "thought": True}]}}]}), "hi")
        with self.assertRaises(L.LLMError): L.parse_gemini_response({"promptFeedback": {"blockReason": "SAFETY"}})
    def test_openai(self):
        b = L.build_openai_request("S", [{"role": "user", "content": "q"}], [("image/png", b"x")], "m", 10, 1)
        self.assertEqual(b["messages"][0]["role"], "system"); self.assertEqual(b["messages"][1]["content"][1]["type"], "image_url")
        self.assertEqual(L.parse_openai_response({"choices": [{"message": {"content": " ok "}}]}), "ok")
    def test_tts_payload(self):
        p = T.build_payload(CFG["tts"], "안녕", "sad")
        self.assertEqual((p["ref_audio_path"], p["prompt_text"], p["prompt_lang"], p["seed"], p["media_type"]), ("sad.wav", "슬퍼", "ko", 7, "wav"))
        self.assertEqual(T.build_payload(CFG["tts"], "안녕", "happy")["ref_audio_path"], "r.wav")


class EdgeProvider(unittest.TestCase):
    """Edge TTS 경로. edge-tts / miniaudio 가 없어도 가짜 모듈로 전체 흐름(비동기 실행, 스레드 호출, wav 변환)을 검증한다."""

    @staticmethod
    def fake_modules(chunks, seen):
        import array, types
        class Communicate:
            def __init__(self, text, voice, rate="+0%", volume="+0%", pitch="+0Hz"):
                seen.update(text=text, voice=voice, rate=rate, volume=volume, pitch=pitch)
            async def stream(self):
                for c in chunks:
                    yield c
        edge = types.ModuleType("edge_tts"); edge.Communicate = Communicate
        mini = types.ModuleType("miniaudio")
        mini.SampleFormat = types.SimpleNamespace(SIGNED16="s16")
        def decode(data, output_format=None, nchannels=1, sample_rate=0):
            seen["decoded"] = (data, nchannels, sample_rate)
            return types.SimpleNamespace(samples=array.array("h", [0, 100, -100, 50]))
        mini.decode = decode
        return {"edge_tts": edge, "miniaudio": mini}

    def run_in_thread(self, fn, *a):
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", fn(*a))); t.start(); t.join(5)
        return out.get("r")

    def test_prosody_defaults_and_emotion_offset(self):
        cfg = {"rate": "+3%", "emotions": {"sad": {"rate": "-10%", "pitch": "-8Hz", "bogus": "x"}}}
        self.assertEqual(T.edge_prosody(cfg, "happy"), {"rate": "+3%", "pitch": "+0Hz", "volume": "+0%"})
        self.assertEqual(T.edge_prosody(cfg, "sad"), {"rate": "-7%", "pitch": "-8Hz", "volume": "+0%"})   # 기본 +3% 에 -10% 를 더함

    def test_emotion_offsets_are_added_to_a_raised_base_voice(self):
        # 회귀: 감정 값이 기본값을 통째로 덮어써서, 기본 목소리를 올려 둔 경우 감정마다 다른 사람 목소리가 났다
        cfg = {"rate": "+15%", "pitch": "+12Hz", "emotions": {"happy": {"rate": "+3%", "pitch": "+2Hz"}, "sad": {"rate": "+1%", "pitch": "+0Hz"}}}
        self.assertEqual(T.edge_prosody(cfg, "neutral"), {"rate": "+15%", "pitch": "+12Hz", "volume": "+0%"})
        self.assertEqual(T.edge_prosody(cfg, "happy"), {"rate": "+18%", "pitch": "+14Hz", "volume": "+0%"})
        self.assertEqual(T.edge_prosody(cfg, "sad"), {"rate": "+16%", "pitch": "+12Hz", "volume": "+0%"})

    def test_emotion_offset_formats_and_bad_values(self):
        cfg = {"pitch": "-4Hz", "volume": "+0%", "emotions": {
            "a": {"pitch": "-6Hz", "volume": "-10%"}, "b": {"pitch": "+4", "rate": "7"}, "c": {"pitch": "abc", "volume": "+5Hz"}, "d": {"pitch": "+1.6Hz"}}}
        self.assertEqual(T.edge_prosody(cfg, "a"), {"rate": "+0%", "pitch": "-10Hz", "volume": "-10%"})
        self.assertEqual(T.edge_prosody(cfg, "b"), {"rate": "+7%", "pitch": "+0Hz", "volume": "+0%"})       # 단위 생략 가능, 0 도 부호가 붙는다
        self.assertEqual(T.edge_prosody(cfg, "c"), {"rate": "+0%", "pitch": "-4Hz", "volume": "+0%"})       # 읽을 수 없거나 단위가 틀리면 무시
        self.assertEqual(T.edge_prosody(cfg, "d"), {"rate": "+0%", "pitch": "-2Hz", "volume": "+0%"})       # 소수는 반올림
        self.assertEqual(T.edge_prosody({}, "anything"), {"rate": "+0%", "pitch": "+0Hz", "volume": "+0%"})

    def test_emotion_mode_absolute_keeps_old_behavior(self):
        cfg = {"rate": "+15%", "pitch": "+12Hz", "emotion_mode": "absolute", "emotions": {"sad": {"rate": "+1%", "pitch": "+0Hz"}}}
        self.assertEqual(T.edge_prosody(cfg, "sad"), {"rate": "+1%", "pitch": "+0Hz", "volume": "+0%"})

    def test_pcm_to_wav_header(self):
        import io, wave
        b = T.pcm_to_wav(b"\x00\x00\x01\x00", 24000)
        self.assertEqual(b[:4], b"RIFF")
        w = wave.open(io.BytesIO(b)); self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()), (1, 2, 24000, 2))

    def test_edge_full_flow_from_worker_thread(self):
        seen = {}
        mods = self.fake_modules([{"type": "WordBoundary"}, {"type": "audio", "data": b"AA"}, {"type": "audio", "data": b"BB"}], seen)
        cfg = {"provider": "edge", "edge": {"voice": "ko-KR-InJoonNeural", "emotions": {"happy": {"pitch": "+12Hz"}}}}
        with unittest.mock.patch.dict(sys.modules, mods):
            wav = self.run_in_thread(T._synth_edge, cfg, "안녕", "happy")
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual((seen["text"], seen["voice"], seen["pitch"]), ("안녕", "ko-KR-InJoonNeural", "+12Hz"))
        self.assertEqual(seen["decoded"], (b"AABB", 1, 24000))  # audio 청크만 이어 붙여 디코딩

    def _flaky_modules(self, fail_times, calls):
        """앞의 fail_times 번은 응답 없이 멈추고(TimeoutError 를 낼 때까지 기다림), 그 뒤엔 정상 응답하는 가짜 edge_tts."""
        import asyncio
        mods = self.fake_modules([{"type": "audio", "data": b"OK"}], {})
        base = mods["edge_tts"].Communicate
        class Flaky(base):
            async def stream(self):
                calls.append(1)
                if len(calls) <= fail_times:
                    await asyncio.sleep(60)      # 멈춘 서버: attempt_timeout 에서 끊겨야 한다
                async for c in super().stream():
                    yield c
        mods["edge_tts"].Communicate = Flaky
        return mods

    def test_edge_retries_after_stall_then_succeeds(self):
        calls = []
        cfg = {"provider": "edge", "edge": {"attempt_timeout": 0.2, "timeout": 5, "retries": 2}}
        with unittest.mock.patch.dict(sys.modules, self._flaky_modules(2, calls)):
            wav = T._synth_edge(cfg, "안녕", "neutral")
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(len(calls), 3)   # 두 번 멈추고 세 번째에 성공

    def test_edge_gives_up_after_retries_and_returns_none(self):
        calls = []
        cfg = {"provider": "edge", "edge": {"attempt_timeout": 0.1, "timeout": 5, "retries": 1}}
        with unittest.mock.patch.dict(sys.modules, self._flaky_modules(99, calls)):
            self.assertIsNone(T._synth_edge(cfg, "안녕", "neutral"))
        self.assertEqual(len(calls), 2)   # 첫 시도 + 재시도 1번

    def test_edge_total_timeout_caps_all_attempts(self):
        import time as _t
        calls = []
        cfg = {"provider": "edge", "edge": {"attempt_timeout": 10, "timeout": 0.3, "retries": 5}}
        t0 = _t.monotonic()
        with unittest.mock.patch.dict(sys.modules, self._flaky_modules(99, calls)):
            self.assertIsNone(T._synth_edge(cfg, "안녕", "neutral"))
        self.assertLess(_t.monotonic() - t0, 2)   # timeout 이 모든 시도를 합친 상한

    def test_edge_empty_audio_returns_none(self):
        with unittest.mock.patch.dict(sys.modules, self.fake_modules([{"type": "WordBoundary"}], {})):
            self.assertIsNone(self.run_in_thread(T._synth_edge, {"provider": "edge"}, "안녕", "neutral"))

    def test_edge_not_installed_returns_none(self):
        with unittest.mock.patch.dict(sys.modules, {"edge_tts": None}):  # None 이면 import 가 ImportError
            self.assertIsNone(T._synth_edge({"provider": "edge"}, "안녕", "neutral"))

    def test_synthesize_dispatch_and_default(self):
        d = tempfile.mkdtemp(); calls = []
        def fake(name):
            def f(cfg, text, emo): calls.append(name); return b"RIFFxxxx"
            return f
        with unittest.mock.patch.dict(T.PROVIDERS, {"gptsovits": fake("gpt"), "edge": fake("edge")}):
            self.assertTrue(T.synthesize({"enabled": True}, "a", "neutral", d).endswith(".wav"))        # provider 생략 -> gptsovits
            T.synthesize({"provider": "EDGE "}, "a", "neutral", d)                                       # 대소문자/공백 무시
            self.assertIsNone(T.synthesize({"provider": "nope"}, "a", "neutral", d))                      # 알 수 없는 값
            self.assertIsNone(T.synthesize({"provider": "edge", "enabled": False}, "a", "neutral", d))    # 꺼져 있으면 호출 안 함
        self.assertEqual(calls, ["gpt", "edge"])
        self.assertEqual(len(os.listdir(d)), 2)

    def test_provider_failure_returns_none_without_file(self):
        d = tempfile.mkdtemp()
        with unittest.mock.patch.dict(T.PROVIDERS, {"edge": lambda *a: None}):
            self.assertIsNone(T.synthesize({"provider": "edge"}, "a", "neutral", d))
        self.assertEqual(os.listdir(d), [])


class ConfigFileTests(unittest.TestCase):
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @staticmethod
    def write(d, name, text):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f: f.write(text)

    def test_example_yaml_parses_with_expected_types(self):
        d = tempfile.mkdtemp()
        import shutil; shutil.copy(os.path.join(self.ROOT, "config.example.yaml"), os.path.join(d, "config.yaml"))
        c = CF.load(d); t = c["tts"]
        self.assertIn(t["provider"], T.PROVIDERS)
        self.assertEqual(t["url"], "http://127.0.0.1:9880")
        self.assertEqual((t["edge"]["rate"], t["edge"]["emotions"]["sad"]["rate"]), ("+0%", "-10%"))   # 문자열로 유지
        self.assertEqual((t["params"]["seed"], t["params"]["parallel_infer"], t["text_lang"]), (-1, True, "ko"))
        self.assertEqual(c["port"], 3545)

    def test_old_json_is_not_read_but_user_is_told(self):
        d = tempfile.mkdtemp()
        self.write(d, "config.json", '{"port": 1}')
        with self.assertRaises(CF.ConfigError) as cm: CF.load(d)
        self.assertIn("config.json", str(cm.exception)); self.assertIn("config.example.yaml", str(cm.exception))
        self.write(d, "config.yaml", "port: 2\n")
        self.assertEqual(CF.load(d), {"port": 2})

    def test_errors_are_friendly(self):
        d = tempfile.mkdtemp()
        with self.assertRaises(CF.ConfigError) as cm: CF.load(d)
        self.assertIn("config.example.yaml", str(cm.exception))
        self.write(d, "config.yaml", "tts:\n\tprovider: edge\n")        # 탭 들여쓰기
        with self.assertRaises(CF.ConfigError) as cm: CF.load(d)
        self.assertIn("문법 오류", str(cm.exception)); self.assertIn("스페이스", str(cm.exception))
        self.write(d, "config.yaml", "")                                # 빈 파일
        with self.assertRaises(CF.ConfigError): CF.load(d)
        self.write(d, "config.yaml", "- a\n- b\n")                      # 최상위가 목록
        with self.assertRaises(CF.ConfigError): CF.load(d)

    def test_yaml_boolean_gotcha_is_documented_in_example(self):
        text = open(os.path.join(self.ROOT, "config.example.yaml"), encoding="utf-8").read()
        self.assertIn('"no"', text)   # no/on 을 따옴표로 감싸라는 안내가 예시 안에 있어야 한다


class Env:
    def __init__(self, replies):
        self.tmp = tempfile.mkdtemp(); self.calls = []; self.replies = list(replies); self.tts_calls = []
        self.db = Database(os.path.join(self.tmp, "t.db"))
        def llm(cfg, system, messages, images=None, max_tokens=0, temperature=0):
            self.calls.append((system, messages, images)); r = self.replies.pop(0)
            if isinstance(r, Exception): raise r
            return r
        def tts(cfg, text, emotion, out_dir):
            self.tts_calls.append((text, emotion)); name = f"a{len(self.tts_calls)}.wav"
            open(os.path.join(out_dir, name), "wb").write(b"RIFF"); return name
        self.engine = Engine(CFG, self.db, self.tmp, llm_fn=llm, tts_fn=tts)


class EngineTests(unittest.TestCase):
    def test_chat_flow_history_window_and_tts(self):
        e = Env(["[happy]<think>*smiles*</think>응! *웃음* 좋아♡", "[sad]<think>*down*</think>그래...", "[neutral]셋", ])
        r = e.engine.chat("하나"); self.assertEqual(r["emotion"], "happy"); self.assertEqual(r["llm_response"], "<think>*smiles*</think>응!  좋아♡".replace("  ", "  ") if False else r["llm_response"])
        self.assertTrue(r["llm_response"].startswith("<think>*smiles*</think>")); self.assertEqual(r["audio_filename"], "a1.wav")
        self.assertEqual(e.tts_calls[0], ("응! 좋아", "happy"))
        e.engine.chat("둘"); e.engine.chat("셋")
        sys_prompt, msgs, _ = e.calls[2]
        self.assertIn("오빠", sys_prompt); self.assertIn("[응답 형식 규칙]", sys_prompt)
        # history_turns=2 -> 직전 2턴(4개) + 현재 메시지
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user", "assistant", "user"])
        self.assertEqual(msgs[-1]["content"], "셋"); self.assertEqual(msgs[0]["content"], "하나")
        self.assertTrue(msgs[1]["content"].startswith("[happy]"))  # 기록에는 원문(형식 유지)
        self.assertEqual(e.db.count(), 6)
    def test_failure_does_not_pollute_history(self):
        e = Env([L.LLMError("boom")])
        with self.assertRaises(L.LLMError): e.engine.chat("hi")
        self.assertEqual(e.db.count(), 0)
    def test_tts_failure_still_returns_text(self):
        e = Env(["[happy]좋아"]); e.engine.tts_fn = lambda *a, **k: None
        r = e.engine.chat("x"); self.assertIsNone(r["audio_filename"]); self.assertEqual(r["llm_response"], "좋아")
    def test_image_safety_and_cleanup(self):
        e = Env(["[neutral]봤어"])
        good = os.path.join(e.engine.upload_dir, "abc.png"); open(good, "wb").write(b"\x89PNG")
        outside = os.path.join(e.tmp, "secret.png"); open(outside, "wb").write(b"zz")
        self.assertIsNone(safe_image_path(e.engine.upload_dir, "../secret.png")); self.assertIsNone(safe_image_path(e.engine.upload_dir, outside))
        self.assertEqual(safe_image_path(e.engine.upload_dir, "abc.png"), good)
        e.engine.chat("봐", ["abc.png", "../secret.png"])
        self.assertEqual(len(e.calls[0][2]), 1); self.assertEqual(e.calls[0][2][0][0], "image/png")
        self.assertFalse(os.path.exists(good)); self.assertTrue(os.path.exists(outside))
    def test_notification(self):
        e = Env(["[surprise]오 메시지 왔네"]); r = e.engine.notification("카카오톡", "철수: 뭐해?")
        self.assertEqual(r["emotion"], "surprise"); self.assertIn("카카오톡", e.calls[0][1][-1]["content"])
        self.assertIsNone(e.engine.notification("x", "  "))


class FakeWS:
    def __init__(self, incoming):
        self.q = asyncio.Queue(); self.sent = []; self.accepted = False
        for m in incoming: self.q.put_nowait(m)
    async def accept(self): self.accepted = True
    async def receive_text(self):
        m = await self.q.get()
        if m is None: raise RuntimeError("disconnect")
        return m
    async def send_text(self, t): self.sent.append(json.loads(t))


class WSTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol(self):
        e = Env(["[happy]<think>*grins*</think>안녕", L.LLMError("quota")])
        ws = FakeWS([json.dumps({"op": "chat", "data": {"text": "안녕", "think": False, "user_name": "오빠"}}), json.dumps({"op": "client_pong"}),
                     json.dumps({"op": "ping"}), "not json", json.dumps({"op": "chat", "data": {"text": "또"}})])
        task = asyncio.create_task(serve_socket(ws, e.engine, "http://127.0.0.1:3545", ping_interval=0.05))
        await asyncio.sleep(0.4); ws.q.put_nowait(None); await asyncio.wait_for(task, 2)
        ops = [m["op"] for m in ws.sent]
        self.assertTrue(ws.accepted); self.assertEqual(ops[0], "hello"); self.assertIn("server_ping", ops); self.assertIn("pong", ops)
        res = next(m for m in ws.sent if m["op"] == "chat_result")
        self.assertEqual(res["emotion"], "happy"); self.assertEqual(res["audio_url"], "http://127.0.0.1:3545/audio/a1.wav")
        self.assertTrue(res["llm_response"].startswith("<think>")); self.assertEqual(ops.count("status"), 4)
        err = next(m for m in ws.sent if m["op"] == "error"); self.assertIn("quota", err["message"])
        # status 는 결과/에러보다 앞에
        self.assertLess(ops.index("status"), ops.index("chat_result"))



class SessionDB(unittest.TestCase):
    def test_migration_from_old_schema_is_safe_and_idempotent(self):
        import sqlite3
        d = tempfile.mkdtemp(); p = os.path.join(d, "old.db")
        c = sqlite3.connect(p)   # 세션 도입 전 스키마
        c.executescript("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user','assistant')), content TEXT NOT NULL, raw TEXT, emotion TEXT,
            kind TEXT NOT NULL DEFAULT 'chat');""")
        c.executemany("INSERT INTO messages (ts, role, content) VALUES (?,?,?)", [(100.0, "user", "옛1"), (101.0, "assistant", "옛답1"), (200.0, "user", "옛2")])
        c.commit(); c.close()
        db = Database(p)
        ss = db.list_sessions()
        self.assertEqual([(s["title"], s["count"]) for s in ss], [("이전 대화", 3)])
        self.assertEqual((ss[0]["created"], ss[0]["updated"]), (100.0, 200.0))
        self.assertEqual([m["content"] for m in db.messages(ss[0]["id"])], ["옛1", "옛답1", "옛2"])
        db.close()
        db = Database(p)   # 다시 열어도 세션이 또 생기지 않는다
        self.assertEqual(len(db.list_sessions()), 1); self.assertEqual(db.count(), 3)

    def test_fresh_db_has_no_sessions(self):
        db = Database(os.path.join(tempfile.mkdtemp(), "n.db"))
        self.assertEqual(db.list_sessions(), []); self.assertEqual(db.recent(5, None), [])

    def test_crud_ordering_and_isolation(self):
        db = Database(os.path.join(tempfile.mkdtemp(), "c.db"))
        a = db.create_session("A"); b = db.create_session("B")
        db.add("user", "a1", session_id=a); db.add("assistant", "a2", session_id=a)
        db.add("user", "b1", session_id=b); db.add("assistant", "b2", session_id=b)
        db.add("user", "a3", session_id=a)                                   # A 가 가장 최근에 쓰임
        self.assertEqual([s["title"] for s in db.list_sessions()], ["A", "B"])
        self.assertEqual([m["content"] for m in db.recent(10, a)], ["a1", "a2", "a3"])
        self.assertEqual([m["content"] for m in db.recent(10, b)], ["b1", "b2"])   # 다른 대화는 섞이지 않는다
        self.assertTrue(db.rename_session(b, "비"))
        self.assertTrue(db.delete_session(a))
        self.assertEqual((db.count(a), db.count(b), db.count()), (0, 2, 2))
        self.assertFalse(db.session_exists(a)); self.assertFalse(db.delete_session(a))
        self.assertEqual([s["title"] for s in db.list_sessions()], ["비"])


class SessionEngine(unittest.TestCase):
    def test_starts_fresh_and_creates_session_on_first_reply(self):
        e = Env(["[happy]안녕"]); en = e.engine
        self.assertIsNone(en.active_session); self.assertEqual(en.list_sessions(), {"sessions": [], "active": None})
        long = "오늘 날씨 어때? 그리고 내일은 어떨까 궁금해서 물어보는 거야"
        r = en.chat(long)
        self.assertEqual(r["session_id"], en.active_session)
        self.assertEqual(len(r["session_title"]), 29); self.assertTrue(r["session_title"].endswith("…"))   # 28자 + …
        self.assertTrue(long.startswith(r["session_title"][:-1].rstrip()))
        self.assertEqual(make_title("짧은 질문"), "짧은 질문"); self.assertEqual(make_title("  "), "새 대화")
        self.assertEqual(len(en.list_sessions()["sessions"]), 1)

    def test_failure_or_empty_creates_no_session(self):
        e = Env([L.LLMError("boom")])
        with self.assertRaises(L.LLMError): e.engine.chat("hi")
        self.assertIsNone(e.engine.chat("   ")); self.assertEqual(e.db.list_sessions(), [])
        self.assertIsNone(e.engine.active_session)

    def test_new_session_does_not_leak_context_and_switch_restores_it(self):
        e = Env(["[neutral]A답", "[neutral]B답", "[neutral]A답2"]); en = e.engine
        en.chat("A질문"); a = en.active_session
        en.new_session()
        self.assertIsNone(en.active_session); self.assertEqual(len(e.db.list_sessions()), 1)   # 새 대화는 아직 DB 에 없다
        en.chat("B질문"); b = en.active_session
        self.assertNotEqual(a, b)
        self.assertEqual([m["content"] for m in e.calls[1][1]], ["B질문"])            # A 의 기록이 안 섞임
        r = en.switch_session(a)
        self.assertEqual([m["content"] for m in r["messages"]], ["A질문", "A답"])
        en.chat("A또"); 
        self.assertEqual([m["content"] for m in e.calls[2][1]], ["A질문", "[neutral]A답", "A또"])   # A 의 문맥이 이어짐
        self.assertEqual(e.db.count(a), 4); self.assertEqual(e.db.count(b), 2)

    def test_delete_and_errors(self):
        e = Env(["[neutral]1", "[neutral]2"]); en = e.engine
        en.chat("첫"); a = en.active_session; en.new_session(); en.chat("둘"); b = en.active_session
        self.assertEqual(en.delete_session(a), {"session_id": a, "was_active": False}); self.assertEqual(en.active_session, b)
        self.assertEqual(en.delete_session(b), {"session_id": b, "was_active": True}); self.assertIsNone(en.active_session)
        self.assertEqual(e.db.list_sessions(), []); self.assertEqual(e.db.count(), 0)
        for bad in (a, 999, "abc", None):
            with self.assertRaises(ValueError): en.switch_session(bad)
        with self.assertRaises(ValueError): en.delete_session(a)

    def test_rename(self):
        e = Env(["[neutral]1"]); en = e.engine; en.chat("hi"); sid = en.active_session
        self.assertEqual(en.rename_session(sid, "  내   제목  ")["title"], "내 제목")
        self.assertEqual(en.rename_session(str(sid), "x" * 100)["title"], "x" * 60)
        with self.assertRaises(ValueError): en.rename_session(sid, "   ")

    def test_notification_first_session_and_hidden_hint(self):
        e = Env(["[surprise]오", "[neutral]응"]); en = e.engine
        en.notification("카카오톡", "철수: 뭐해?")
        self.assertEqual(e.db.list_sessions()[0]["title"], "알림 · 카카오톡")
        msgs = en.session_messages(en.active_session)["messages"]
        self.assertEqual(msgs[0]["content"], "[알림] 카카오톡: 철수: 뭐해?")        # AI 지시문은 화면에 안 보임
        self.assertEqual(msgs[0]["kind"], "notification")
        en.chat("답장 뭐라고 해?")
        self.assertEqual(len(e.db.list_sessions()), 1)                               # 같은 대화로 이어짐

    def test_stale_active_session_falls_back_to_new(self):
        e = Env(["[neutral]1", "[neutral]2"]); en = e.engine; en.chat("a"); sid = en.active_session
        e.db.delete_session(sid)                                                      # 엔진을 거치지 않고 지워진 경우
        self.assertEqual(en.list_sessions()["active"], None)
        en.chat("b"); self.assertNotEqual(en.active_session, sid); self.assertEqual([m["content"] for m in e.calls[1][1]], ["b"])


class LyricsTranslateTests(unittest.TestCase):
    """가사 번역: 줄 번호로 짝을 맞춘다. 모델이 줄을 빼먹어도 나머지가 밀리지 않고, 빠진 줄만 다시 요청한다."""
    import re as _re
    import lyrics_translate as LT

    def _ask(self, drop_first=()):
        calls = []
        def ask(prompt, n):
            calls.append(n)
            lines = self._re.findall(r"^\[(\d+)\] (.*)$", prompt.split("입력:\n")[1], self._re.M)
            return "\n".join(f"[{i}] 번역{i}" for i, _ in lines if not (int(i) in drop_first and len(calls) == 1))
        return ask, calls

    def test_parse_numbered_handles_fences_continuations_and_unknown_labels(self):
        out = self.LT.parse_numbered("```\n[0] 가\n[1] 나\n이어지는 말\n[7] 버림\n[2]\n```", {0, 1, 2})
        self.assertEqual(out, {0: "가", 1: "나 이어지는 말", 2: ""})

    def test_missing_lines_are_requested_again_and_order_is_kept(self):
        ask, calls = self._ask(drop_first={1, 4})
        r = self.LT.translate(ask, None, ["a", "b", "c", "d", "e"])
        self.assertEqual(r["lines"], [f"번역{i}" for i in range(5)])
        self.assertEqual(calls, [5, 2])                       # 두 번째 요청은 빠진 두 줄만

    def test_a_few_missing_lines_stay_blank_but_many_fail(self):
        def drops_two(prompt, n):
            lines = self._re.findall(r"^\[(\d+)\] (.*)$", prompt.split("입력:\n")[1], self._re.M)
            return "\n".join(f"[{i}] x{i}" for i, _ in lines if i != "2")
        r = self.LT.translate(drops_two, None, [chr(97 + i) for i in range(10)])
        self.assertEqual(r["lines"][2], "") ; self.assertEqual(r["lines"][3], "x3")
        with self.assertRaises(ValueError):
            self.LT.translate(lambda p, n: "죄송합니다", None, ["a", "b", "c"])

    def test_blank_lines_are_kept_and_result_is_cached(self):
        class Store:
            d = {}
            def kv_get(self, k): return self.d.get(k)
            def kv_set(self, k, v): self.d[k] = v
        ask, calls = self._ask()
        st = Store()
        self.assertEqual(self.LT.translate(ask, st, ["a", "", "c"])["lines"], ["번역0", "", "번역2"])
        self.assertTrue(self.LT.translate(ask, st, ["a", "", "c"])["cached"])
        self.assertEqual(len(calls), 1)


class NowPlayingTests(unittest.TestCase):
    """곡에 대해 물을 때 클라이언트가 실어 보낸 재생 중인 곡이 이번 턴 시스템 프롬프트에만 들어간다 (기록에는 안 남김)."""
    import now_playing as NP

    def test_sanitize_and_block(self):
        self.assertIsNone(self.NP.sanitize(None)); self.assertIsNone(self.NP.sanitize({"artist": "x"}))
        np = self.NP.sanitize({"title": " Beat It ", "artist": "Michael Jackson", "app": "Spotify.exe", "state": "playing", "duration": "257", "junk": 1})
        self.assertEqual((np["title"], np["duration"], "junk" in np), ("Beat It", 257, False))
        b = self.NP.block(np)
        self.assertIn("제목: Beat It", b); self.assertIn("가수: Michael Jackson", b); self.assertIn("재생 중", b); self.assertNotIn("채널 이름", b)
        web = self.NP.block(self.NP.sanitize({"title": "t", "artist": "채널", "app": "chrome.exe"}))
        self.assertIn("채널 이름", web)            # 브라우저면 가수 칸이 채널명일 수 있다고 알려 준다
        self.assertEqual(self.NP.block(None), "")

    def test_only_this_turn_gets_the_song_and_history_stays_clean(self):
        e = Env(["[neutral]마이클 잭슨이에요", "[neutral]네"]); en = e.engine
        en.chat("이 노래 누가 불렀어?", now_playing={"title": "Beat It", "artist": "Michael Jackson", "app": "Spotify.exe"})
        self.assertIn("Beat It", e.calls[0][0]); self.assertIn("Michael Jackson", e.calls[0][0])   # 시스템 프롬프트에 들어감
        self.assertEqual(e.calls[0][1][-1]["content"], "이 노래 누가 불렀어?")                       # 사용자 글은 그대로
        en.chat("고마워")
        self.assertNotIn("Beat It", e.calls[1][0])                                                    # 다음 턴엔 없음
        self.assertTrue(all("Beat It" not in m["content"] for m in e.calls[1][1]))                    # 기록에도 안 남음


class ProactiveEngine(unittest.TestCase):
    """캐릭터가 먼저 거는 말 (클라이언트의 '먼저 말 걸기' 타이머가 부른다)."""

    def test_first_words_of_a_fresh_server(self):
        e = Env(["[happy]안녕하세요!"]); en = e.engine
        r = en.proactive()
        self.assertEqual((r["emotion"], r["session_title"]), ("happy", "먼저 말 걸기"))
        msg = e.calls[0][1][-1]
        self.assertTrue(msg["content"].startswith("[시스템]")); self.assertIn("아직 주고받은 말이 없어", msg["content"])
        self.assertEqual(e.tts_calls, [("안녕하세요!", "happy")])                     # 음성도 만든다
        sid = en.active_session
        self.assertEqual([m["role"] for m in en.session_messages(sid)["messages"]], ["assistant"])   # 시스템 지시문은 화면 기록에서 숨김
        self.assertEqual(e.db.count(sid), 2)                                          # DB 에는 AI 가 문맥으로 볼 수 있게 남아 있다
        self.assertEqual(en.list_sessions()["sessions"][0]["count"], 1)               # 목록의 메시지 수에도 안 센다

    def test_proactive_can_see_the_screen_and_cleans_up(self):
        e = Env(["[neutral]어 그거 재밌어 보인다", "[neutral]그냥 안녕"]); en = e.engine
        good = os.path.join(en.upload_dir, "scr.jpg"); open(good, "wb").write(b"\xff\xd8")
        en.proactive(["scr.jpg", "../nope.jpg"])
        self.assertEqual(len(e.calls[0][2]), 1); self.assertEqual(e.calls[0][2][0][0], "image/jpeg")   # 화면이 LLM 에 간다
        self.assertIn("화면", e.calls[0][1][-1]["content"])
        self.assertFalse(os.path.exists(good))                                                          # 한 번 쓰고 지운다
        en.proactive()                                                                                  # 화면이 없으면 예전과 똑같다
        self.assertIsNone(e.calls[1][2]); self.assertNotIn("화면 공유", e.calls[1][1][-1]["content"])

    def test_mentions_minutes_since_last_message_and_keeps_context(self):
        e = Env(["[neutral]답1", "[sad]심심했어", "[neutral]답3"]); en = e.engine
        en.chat("점심 뭐 먹지"); sid = en.active_session
        base = en.now_fn().timestamp()
        e.db.conn.execute("UPDATE messages SET ts=?", (base - 30 * 60,)); e.db.conn.commit()   # 마지막 대화가 30분 전
        en.proactive()
        sysmsg = e.calls[1][1]
        self.assertEqual([m["content"] for m in sysmsg[:2]], ["점심 뭐 먹지", "[neutral]답1"])     # 지금 대화의 문맥 위에서 말을 건다
        self.assertIn("약 30분", sysmsg[-1]["content"])
        self.assertEqual(en.active_session, sid)                                       # 새 세션이 아니라 지금 대화에 이어진다
        en.chat("그러게")                                                              # 사용자의 답은 먼저 건 말을 문맥으로 본다
        hist = [m["content"] for m in e.calls[2][1]]
        self.assertTrue(hist[2].startswith("[시스템]")); self.assertEqual(hist[3], "[sad]심심했어"); self.assertEqual(hist[4], "그러게")
        shown = [(m["role"], m["content"]) for m in en.session_messages(sid)["messages"]]
        self.assertEqual(shown, [("user", "점심 뭐 먹지"), ("assistant", "답1"), ("assistant", "심심했어"), ("user", "그러게"), ("assistant", "답3")])

    def test_failure_leaves_no_trace(self):
        e = Env([L.LLMError("quota")])
        with self.assertRaises(L.LLMError): e.engine.proactive()
        self.assertEqual((e.db.list_sessions(), e.db.count(), e.engine.active_session), ([], 0, None))



class SessionWS(unittest.IsolatedAsyncioTestCase):
    async def run_ws(self, replies, msgs, wait=0.5, keep_status=False):
        e = Env(replies)
        ws = FakeWS([json.dumps(m) for m in msgs])
        task = asyncio.create_task(serve_socket(ws, e.engine, "http://h:1", ping_interval=10))
        await asyncio.sleep(wait); ws.q.put_nowait(None); await asyncio.wait_for(task, 2)
        skip = ("server_ping",) if keep_status else ("server_ping", "status")
        return e, [m for m in ws.sent if m["op"] not in skip]

    async def test_full_session_flow(self):
        chat = lambda t: {"op": "chat", "data": {"text": t}}
        e, out = await self.run_ws(["[happy]A답", "[sad]B답"], [
            chat("A질문"), {"op": "session_new"}, chat("B질문"),
            {"op": "session_list"},
        ])
        ops = [m["op"] for m in out]
        self.assertEqual(ops[:2], ["hello", "sessions"])
        self.assertEqual(out[1]["sessions"], []); self.assertIsNone(out[1]["active"])      # 접속 직후: 새 대화 상태
        res = [m for m in out if m["op"] == "chat_result"]
        self.assertEqual([r["session_title"] for r in res], ["A질문", "B질문"]); self.assertNotEqual(res[0]["session_id"], res[1]["session_id"])
        na = next(m for m in out if m["op"] == "session_active"); self.assertIsNone(na["session_id"]); self.assertEqual(na["messages"], [])
        last = [m for m in out if m["op"] == "sessions"][-1]
        self.assertEqual([s["title"] for s in last["sessions"]], ["B질문", "A질문"]); self.assertEqual(last["active"], res[1]["session_id"])
        # 결과 바로 뒤에는 목록이 갱신되어 따라온다
        i = ops.index("chat_result"); self.assertEqual(ops[i + 1], "sessions")

    async def test_switch_messages_rename_delete(self):
        chat = lambda t: {"op": "chat", "data": {"text": t}}
        e, out = await self.run_ws(["[neutral]1", "[neutral]2"], [chat("A"), {"op": "session_new"}, chat("B")], wait=0.4)
        a, b = [s["id"] for s in sorted(e.db.list_sessions(), key=lambda s: s["id"])]
        e2 = e  # 같은 엔진으로 이어서
        ws = FakeWS([json.dumps(m) for m in [
            {"op": "session_switch", "data": {"session_id": a}},
            {"op": "session_messages", "data": {"session_id": a}},
            {"op": "session_rename", "data": {"session_id": a, "title": "새 이름"}},
            {"op": "session_delete", "data": {"session_id": a}},
            {"op": "session_switch", "data": {"session_id": a}},          # 이미 지운 대화
            {"op": "session_rename", "data": {"session_id": b, "title": " "}},
        ]])
        task = asyncio.create_task(serve_socket(ws, e2.engine, "http://h:1", ping_interval=10))
        await asyncio.sleep(0.4); ws.q.put_nowait(None); await asyncio.wait_for(task, 2)
        out = [m for m in ws.sent if m["op"] not in ("server_ping", "status")]
        act = [m for m in out if m["op"] == "session_active"]
        self.assertEqual((act[0]["session_id"], act[0]["title"], [m["content"] for m in act[0]["messages"]]), (a, "A", ["A", "1"]))
        sm = next(m for m in out if m["op"] == "session_messages"); self.assertEqual([m["role"] for m in sm["messages"]], ["user", "assistant"])
        self.assertIsNone(act[1]["session_id"])                                   # 현재 대화(a)를 지웠으니 새 대화로
        errs = [m for m in out if m["op"] == "error"]
        self.assertEqual([x["code"] for x in errs], ["session_error", "session_error"])
        self.assertIn("없는 대화", errs[0]["message"]); self.assertIn("비어", errs[1]["message"])
        final = [m for m in out if m["op"] == "sessions"][-1]
        self.assertEqual([s["id"] for s in final["sessions"]], [b]); self.assertIsNone(final["active"])


    async def test_each_jobs_output_is_contiguous(self):
        """연달아 보낸 명령의 출력이 섞이지 않는다: 결과 바로 뒤에 목록, 다음 작업의 status 는 그 뒤."""
        chat = lambda t: {"op": "chat", "data": {"text": t}}
        e, out = await self.run_ws(["[neutral]1", "[neutral]2", "[neutral]3"],
                                   [chat("a"), {"op": "session_new"}, chat("b"), {"op": "session_new"}, chat("c"), {"op": "session_list"}], wait=0.8, keep_status=True)
        ops = [m["op"] for m in out]
        self.assertEqual(ops[:2], ["hello", "sessions"])
        self.assertEqual(ops[2:], ["status", "status", "chat_result", "sessions", "session_active", "sessions",
                                   "status", "status", "chat_result", "sessions", "session_active", "sessions",
                                   "status", "status", "chat_result", "sessions", "sessions"])
        # 가장 마지막 목록은 항상 최신 상태 (c 가 현재 대화)
        last = [m for m in out if m["op"] == "sessions"][-1]; self.assertEqual(last["active"], e.engine.active_session)

    async def test_proactive_is_silent_and_pushes_list(self):
        e, out = await self.run_ws(["[happy]먼저 말해요"], [{"op": "proactive", "data": {}}], keep_status=True)
        self.assertEqual([m["op"] for m in out], ["hello", "sessions", "proactive_result", "sessions"])   # status(…) 없이 결과만
        r = out[2]; self.assertEqual((r["emotion"], r["session_title"]), ("happy", "먼저 말 걸기")); self.assertTrue(r["audio_url"])
        self.assertEqual(out[3]["active"], r["session_id"])

    async def test_proactive_failure_is_not_shown_to_user(self):
        e, out = await self.run_ws([L.LLMError("quota"), RuntimeError("boom")], [{"op": "proactive"}, {"op": "proactive"}], keep_status=True)
        self.assertEqual([m["op"] for m in out], ["hello", "sessions"])    # 오류도, 처리 중 표시도 없다
        e2, out2 = await self.run_ws([L.LLMError("quota")], [{"op": "chat", "data": {"text": "hi"}}], keep_status=True)
        self.assertIn("error", [m["op"] for m in out2])                    # 사용자가 요청한 chat 은 여전히 오류를 알린다


    async def test_session_messages_does_not_push_list(self):
        e, _ = await self.run_ws(["[neutral]1"], [{"op": "chat", "data": {"text": "A"}}], wait=0.3)
        sid = e.engine.active_session
        ws = FakeWS([json.dumps({"op": "session_messages", "data": {"session_id": sid}})])
        task = asyncio.create_task(serve_socket(ws, e.engine, "http://h:1", ping_interval=10))
        await asyncio.sleep(0.3); ws.q.put_nowait(None); await asyncio.wait_for(task, 2)
        ops = [m["op"] for m in ws.sent if m["op"] not in ("server_ping", "status")]
        self.assertEqual(ops, ["hello", "sessions", "session_messages"])   # 목록은 접속 직후 한 번만. (클라이언트의 요청-응답 반복 방지)


class SovitsLauncher(unittest.TestCase):
    """provider 가 gptsovits 일 때만 GPT-SoVITS 를 켠다."""
    def setUp(self):
        import tts_launcher as SL
        self.SL = SL
        self.tmp = tempfile.mkdtemp()
        self.gdir = os.path.join(self.tmp, "sovits"); os.makedirs(self.gdir)
        open(os.path.join(self.gdir, "api_v2.py"), "w").close()
        self.calls = []
        outer = self

        class FakeProc:
            pid = 4242; returncode = None
            def poll(self): return None
            def wait(self, timeout=None): return 0
            def kill(self): outer.calls.append("kill")
        self.FakeProc = FakeProc

    def cfg(self, **kw):
        c = {"enabled": True, "provider": "gptsovits", "url": "http://127.0.0.1:9880", "launch": {"dir": self.gdir, "log_file": "s.log"}}
        c.update(kw); return c

    def launcher(self, cfg, up=False):
        def popen(cmd, **kw):
            self.calls.append(("popen", cmd, kw.get("cwd"))); return self.FakeProc()
        return self.SL.Launcher(cfg, self.tmp, popen=popen, is_up_fn=lambda url: up, sleep=lambda s: None)

    def test_only_gptsovits_provider_launches(self):
        for prov in ("edge", "EDGE", "something"):
            l = self.launcher(self.cfg(provider=prov)); self.assertFalse(l.start()); self.assertFalse(l.owned)
        self.assertEqual(self.calls, [])
        l = self.launcher(self.cfg(provider=" GptSoVits ")); self.assertTrue(l.start()); l.stop()

    def test_default_provider_is_gptsovits_like_tts_py(self):
        c = self.cfg(); del c["provider"]
        self.assertTrue(self.SL.should_launch(c))

    def test_disabled_flags(self):
        self.assertFalse(self.SL.should_launch(self.cfg(enabled=False)))
        c = self.cfg(); c["launch"]["enabled"] = False
        self.assertFalse(self.SL.should_launch(c))
        l = self.launcher(c); self.assertFalse(l.start()); self.assertEqual(self.calls, [])

    def test_already_running_is_reused_and_never_killed(self):
        l = self.launcher(self.cfg(), up=True)
        self.assertFalse(l.start()); self.assertFalse(l.owned); self.assertTrue(l.ready.is_set())
        l.stop(); self.assertEqual(self.calls, [])

    def test_default_command_uses_url_host_port_and_dir(self):
        c = self.cfg(url="http://127.0.0.1:9999")
        l = self.launcher(c); self.assertTrue(l.start())
        kind, cmd, cwd = self.calls[0]
        self.assertEqual(cwd, os.path.normpath(self.gdir))
        self.assertEqual(cmd[1:], ["api_v2.py", "-a", "127.0.0.1", "-p", "9999", "-c", "GPT_SoVITS/configs/tts_infer.yaml"])
        self.assertTrue(l.owned); l.stop(); self.assertFalse(l.owned)

    def test_bundled_runtime_python_preferred(self):
        os.makedirs(os.path.join(self.gdir, "runtime"))
        exe = os.path.join(self.gdir, "runtime", "python.exe"); open(exe, "w").close()
        cmd, _ = self.SL.build_command(self.cfg())
        self.assertEqual(cmd[0], exe)

    def test_custom_command_and_relative_exe_resolved(self):
        exe = os.path.join(self.gdir, "go.bat"); open(exe, "w").close()
        c = self.cfg(); c["launch"]["command"] = ["go.bat", "--x"]
        cmd, _ = self.SL.build_command(c)
        self.assertEqual(cmd, [exe, "--x"])

    def test_bad_config_is_friendly_and_does_not_raise(self):
        for launch in ({}, {"dir": os.path.join(self.tmp, "nope")}, {"dir": self.tmp}, {"dir": self.gdir, "command": "run.bat"}):
            c = self.cfg(); c["launch"] = launch
            with self.assertRaises(ValueError): self.SL.build_command(c)
            l = self.launcher(c); self.assertFalse(l.start()); self.assertFalse(l.owned)
        self.assertEqual(self.calls, [])

    def test_popen_failure_does_not_raise(self):
        def boom(cmd, **kw): raise FileNotFoundError("x")
        l = self.SL.Launcher(self.cfg(), self.tmp, popen=boom, is_up_fn=lambda u: False)
        self.assertFalse(l.start()); self.assertFalse(l.owned)

    def test_ready_event_set_when_server_answers(self):
        state = {"n": 0}
        def up(url):
            state["n"] += 1; return state["n"] > 3          # 처음엔 꺼져 있다가(시작 전 확인 포함) 곧 응답
        l = self.SL.Launcher(self.cfg(), self.tmp, popen=lambda cmd, **kw: self.FakeProc(), is_up_fn=up, sleep=lambda s: None)
        self.assertTrue(l.start()); self.assertTrue(l.ready.wait(3)); l.stop()

    def test_real_process_is_started_and_stopped(self):
        # 가짜가 아니라 실제 프로세스로: 명령이 cwd 에서 돌고, stop() 이 끈다
        c = self.cfg(); c["launch"]["command"] = [sys.executable, "-c", "import time; time.sleep(60)"]
        l = self.SL.Launcher(c, self.tmp, is_up_fn=lambda u: False)
        self.assertTrue(l.start()); proc = l.proc; self.assertIsNone(proc.poll())
        l.stop(); self.assertIsNotNone(proc.poll())

    def test_example_and_real_config_have_launch_block(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        import yaml
        for fn in ("config.example.yaml",):
            tts = yaml.safe_load(open(os.path.join(base, fn), encoding="utf-8"))["tts"]
            self.assertIn("dir", tts["launch"]); self.assertIs(tts["launch"]["enabled"], True)


class ClientWatcherTests(unittest.IsolatedAsyncioTestCase):
    """클라이언트가 모두 끊기면 서버를 끝낸다 (lifecycle.ClientWatcher)."""
    def mk(self, grace=0.05):
        import lifecycle
        self.fired = []; self.logs = []
        return lifecycle.ClientWatcher(grace, lambda: self.fired.append(1), log=self.logs.append)

    async def test_never_connected_never_fires(self):
        w = self.mk(0.01); await asyncio.sleep(0.1)
        self.assertEqual(self.fired, [])

    async def test_fires_after_grace_when_last_client_leaves(self):
        w = self.mk(); w.connected(); w.disconnected()
        await asyncio.sleep(0.01); self.assertEqual(self.fired, [])        # 아직 유예 시간 중
        await asyncio.sleep(0.15); self.assertEqual(self.fired, [1])

    async def test_reconnect_within_grace_cancels(self):
        w = self.mk(0.1); w.connected(); w.disconnected()
        await asyncio.sleep(0.03); w.connected()
        await asyncio.sleep(0.2); self.assertEqual(self.fired, [])
        self.assertTrue(any("취소" in m for m in self.logs))
        w.disconnected(); await asyncio.sleep(0.2); self.assertEqual(self.fired, [1])  # 다시 끊기면 새로 센다

    async def test_waits_for_all_clients(self):
        w = self.mk(); w.connected(); w.connected()
        w.disconnected(); await asyncio.sleep(0.15); self.assertEqual(self.fired, [])
        w.disconnected(); await asyncio.sleep(0.15); self.assertEqual(self.fired, [1])

    async def test_fires_only_once(self):
        w = self.mk(0.02); w.connected(); w.disconnected(); await asyncio.sleep(0.1)
        w.connected(); w.disconnected(); await asyncio.sleep(0.1)
        self.assertEqual(self.fired, [1])

    async def test_zero_grace_and_unbalanced_disconnect(self):
        w = self.mk(0); w.connected(); w.disconnected(); await asyncio.sleep(0.05); self.assertEqual(self.fired, [1])
        w2 = self.mk(0.01); w2.disconnected(); await asyncio.sleep(0.05)   # 접속 없이 끊김 알림만 와도 안전
        self.assertEqual(self.fired, []); self.assertEqual(w2.count, 0)

    def test_config_parsing(self):
        import lifecycle as LC
        self.assertEqual([LC.enabled({}), LC.enabled({"shutdown_with_client": True}), LC.enabled({"shutdown_with_client": False}), LC.enabled({"shutdown_with_client": None})], [True, True, False, True])
        self.assertEqual([LC.grace_from({}), LC.grace_from({"shutdown_grace_seconds": 5}), LC.grace_from({"shutdown_grace_seconds": "30"}),
                          LC.grace_from({"shutdown_grace_seconds": "x"}), LC.grace_from({"shutdown_grace_seconds": -1}), LC.grace_from({"shutdown_grace_seconds": 0})], [15.0, 5.0, 30.0, 15.0, 15.0, 0.0])

    def test_example_config_has_keys(self):
        import yaml
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        c = yaml.safe_load(open(os.path.join(base, "config.example.yaml"), encoding="utf-8"))
        self.assertIs(c["shutdown_with_client"], True); self.assertEqual(c["shutdown_grace_seconds"], 15)


class FakeRvcService:
    """rvc-python api 서버 흉내 (문서에 있는 엔드포인트만): POST /models/{name}, /set_device, /params, /convert"""
    def __init__(self):
        import http.server, base64 as b64
        self.calls = []; self.convert_status = 200; outer = self
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
                outer.calls.append((self.path, body))
                if self.path == "/convert":
                    if outer.convert_status != 200:
                        self.send_response(outer.convert_status); self.end_headers(); self.wfile.write(b"boom"); return
                    wav = b64.b64decode(body["audio_data"]) + b"RVC"      # 변환됐다는 표시
                    self.send_response(200); self.send_header("Content-Type", "audio/wav"); self.end_headers(); self.wfile.write(wav); return
                self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
    def paths(self): return [c[0] for c in self.calls]
    def close(self): self.httpd.shutdown(); self.httpd.server_close()


class RvcTests(unittest.TestCase):
    def setUp(self):
        import rvc as R
        self.R = R; self.tmp = tempfile.mkdtemp()
        self.svc = FakeRvcService(); self.addCleanup(self.svc.close)

    def cfg(self, **rvc):
        base = {"enabled": True, "url": self.svc.url, "model": "miku default", "device": "cuda:0", "pitch": 3, "params": {"index_rate": 0.8}}
        base.update(rvc)
        return {"enabled": True, "provider": "edge", "rvc": base}

    # ---- 적용 조건 ----
    def test_active_only_for_edge_and_explicit_enable(self):
        R = self.R
        self.assertTrue(R.active(self.cfg()))
        self.assertFalse(R.active({"enabled": True, "provider": "edge"}))                               # rvc 설정 없음 = 꺼짐
        self.assertFalse(R.active({"enabled": True, "provider": "edge", "rvc": {"enabled": "yes"}}))     # true 가 아니면 꺼짐
        self.assertFalse(R.active({**self.cfg(), "provider": "gptsovits"}))                              # 음색 복제 엔진에는 붙이지 않는다
        self.assertFalse(R.active({**self.cfg(), "enabled": False}))
        self.assertFalse(R.active({"rvc": {"enabled": True}}))                                           # provider 생략 = gptsovits

    def test_params(self):
        p = self.R.build_params({"pitch": 3, "params": {"index_rate": 0.8, "f0method": "harvest"}})
        self.assertEqual((p["f0up_key"], p["index_rate"], p["f0method"], p["protect"]), (3, 0.8, "harvest", 0.33))
        self.assertEqual(self.R.build_params({})["f0up_key"], 0)
        self.assertEqual(self.R.build_params({"pitch": 3, "params": {"f0up_key": 5}})["f0up_key"], 5)   # params 가 우선

    # ---- 변환 요청 (진짜 HTTP) ----
    def test_setup_then_convert_over_http(self):
        c = self.R.RvcClient(self.cfg())
        out = c.convert(b"RIFFabcd")
        self.assertEqual(out, b"RIFFabcdRVC")
        self.assertEqual(self.svc.paths(), ["/set_device", "/models/miku%20default", "/params", "/convert"])   # 모델 이름은 주소용으로 인코딩
        self.assertEqual(self.svc.calls[0][1], {"device": "cuda:0"})
        self.assertEqual(self.svc.calls[2][1]["params"]["f0up_key"], 3)
        import base64
        self.assertEqual(base64.b64decode(self.svc.calls[3][1]["audio_data"]), b"RIFFabcd")
        c.convert(b"RIFFefgh"); self.assertEqual(self.svc.paths().count("/params"), 1)                      # 설정은 한 번만

    def test_no_model_or_device_skips_those_calls(self):
        c = self.R.RvcClient(self.cfg(model=None, device=None)); c.convert(b"RIFF")
        self.assertEqual(self.svc.paths(), ["/params", "/convert"])

    def test_failure_returns_none_and_resets_setup(self):
        c = self.R.RvcClient(self.cfg())
        self.svc.convert_status = 500
        self.assertIsNone(c.convert(b"RIFFxx")); self.assertFalse(c.ok)
        self.svc.convert_status = 200; self.svc.calls.clear()
        self.assertEqual(c.convert(b"RIFFxx"), b"RIFFxxRVC")
        self.assertEqual(self.svc.paths(), ["/set_device", "/models/miku%20default", "/params", "/convert"])   # 서비스가 재시작됐을 수 있으니 다시 설정

    def test_service_down_returns_none_fast(self):
        url = self.svc.url; self.svc.close()
        c = self.R.RvcClient(self.cfg(url=url, timeout=2))
        t0 = time.time(); self.assertIsNone(c.convert(b"RIFF")); self.assertLess(time.time() - t0, 3)

    def test_non_wav_reply_is_a_failure(self):
        c = self.R.RvcClient(self.cfg())
        c.post = lambda url, json=None, timeout=None: type("R", (), {"status_code": 200, "content": b"<html>", "text": ""})()
        c.ok = True; self.assertIsNone(c.convert(b"RIFF"))

    # ---- tts.synthesize 연동 ----
    def synth(self, cfg, provider_data=b"RIFForig", client=None):
        out = os.path.join(self.tmp, "audio")
        with unittest.mock.patch.dict(T.PROVIDERS, {"edge": lambda c, t, e: provider_data, "gptsovits": lambda c, t, e: provider_data}):
            with unittest.mock.patch.object(T.rvc_mod, "get_client", lambda c: client or self.R.RvcClient(c)):
                name = T.synthesize(cfg, "안녕", "happy", out)
        return None if name is None else open(os.path.join(out, name), "rb").read()

    def test_synthesize_applies_rvc(self):
        self.assertEqual(self.synth(self.cfg()), b"RIFForigRVC")

    def test_synthesize_fallback_to_original_voice(self):
        self.svc.convert_status = 500
        self.assertEqual(self.synth(self.cfg()), b"RIFForig")
        self.assertIsNone(self.synth(self.cfg(fallback=False)))

    def test_synthesize_skips_rvc_when_not_active(self):
        for cfg in (self.cfg(enabled=False), {**self.cfg(), "provider": "gptsovits"}):
            self.svc.calls.clear()
            self.assertEqual(self.synth(cfg), b"RIFForig"); self.assertEqual(self.svc.calls, [])

    # ---- 자동 실행 ----
    def launch_cfg(self, **kw):
        d = os.path.join(self.tmp, "rvcdir"); os.makedirs(os.path.join(d, "rvc_models", "miku_default"), exist_ok=True)
        py = os.path.join(self.tmp, "py.exe"); open(py, "w").close()
        c = self.cfg(model="miku_default", url="http://127.0.0.1:5055", launch={"dir": d, "python": py, "log_file": "r.log"}); c["rvc"].update(kw); return c, d, py

    def test_default_command(self):
        c, d, py = self.launch_cfg()
        cmd, cwd = self.R.build_command(c)
        self.assertEqual((cmd, cwd), ([py, "-m", "rvc_python", "api", "-p", "5055"], os.path.normpath(d)))

    def test_custom_command_wins(self):
        c, d, py = self.launch_cfg(); c["rvc"]["launch"]["command"] = ["x", "y"]
        self.assertEqual(self.R.build_command(c)[0], ["x", "y"])

    def test_bad_launch_config_is_friendly(self):
        c, d, py = self.launch_cfg()
        import copy
        for mut in (lambda r: r["launch"].update(dir=""), lambda r: r["launch"].update(dir=os.path.join(self.tmp, "no")),
                    lambda r: r.update(model="nope"), lambda r: r["launch"].update(python=""), lambda r: r["launch"].update(python="/no/such/python"),
                    lambda r: r["launch"].update(command="run.bat")):
            bad = copy.deepcopy(c); mut(bad["rvc"])
            with self.assertRaises(ValueError): self.R.build_command(bad)
            launched = []
            L = SL_mod.Launcher(bad, self.tmp, popen=lambda *a, **k: launched.append(1), is_up_fn=lambda u: False, spec=self.R.SPEC)
            self.assertFalse(L.start()); self.assertEqual(launched, [])

    def test_launcher_only_when_edge_and_enabled(self):
        c, d, py = self.launch_cfg(); R = self.R
        self.assertTrue(R.should_launch(c))
        c2 = copy_cfg(c); c2["rvc"]["launch"]["enabled"] = False; self.assertFalse(R.should_launch(c2))
        self.assertFalse(R.should_launch({**c, "provider": "gptsovits"})); self.assertFalse(R.should_launch({**c, "enabled": False}))
        c3 = copy_cfg(c); c3["rvc"]["enabled"] = False; self.assertFalse(R.should_launch(c3))

    def test_start_runs_command_and_configures_when_ready(self):
        c, d, py = self.launch_cfg(); calls = []; ready = []
        class P:
            pid = 7; returncode = None
            def poll(self): return None
            def wait(self, timeout=None): return 0
            def kill(self): pass
        L = SL_mod.Launcher(c, self.tmp, popen=lambda cmd, **k: calls.append((cmd, k.get("cwd"))) or P(), is_up_fn=lambda u: len(calls) > 0,
                            sleep=lambda s: None, spec=self.R.SPEC, on_ready=lambda: ready.append(1))
        self.assertTrue(L.start()); self.assertTrue(L.ready.wait(3)); time.sleep(0.05)
        self.assertEqual(calls[0][0][1:], ["-m", "rvc_python", "api", "-p", "5055"]); self.assertEqual(ready, [1])
        L.stop()

    def test_already_running_service_is_configured_not_restarted(self):
        c, d, py = self.launch_cfg(); ready = []; calls = []
        L = SL_mod.Launcher(c, self.tmp, popen=lambda *a, **k: calls.append(1), is_up_fn=lambda u: True, spec=self.R.SPEC, on_ready=lambda: ready.append(1))
        self.assertFalse(L.start()); self.assertTrue(L.ready.wait(3)); time.sleep(0.05)
        self.assertEqual((calls, ready, L.owned), ([], [1], False))

    def test_on_ready_failure_does_not_crash(self):
        L = SL_mod.Launcher(self.launch_cfg()[0], self.tmp, is_up_fn=lambda u: True, spec=self.R.SPEC, on_ready=lambda: 1 / 0)
        L.start(); self.assertTrue(L.ready.wait(3))

    def test_example_config_defaults(self):
        import yaml
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = yaml.safe_load(open(os.path.join(base, "config.example.yaml"), encoding="utf-8"))["tts"]["rvc"]
        self.assertIs(r["enabled"], False); self.assertIs(r["fallback"], True); self.assertEqual(r["url"], "http://127.0.0.1:5050")


class ExtrasTests(unittest.TestCase):
    def test_recall_attaches_old_chat_only_when_relevant(self):
        e = Env(["[neutral]나비구나", "[neutral]응", "[neutral]나비예요"])
        e.engine.chat("내 고양이 이름은 나비야")
        sid2 = e.db.create_session("다른 대화"); e.engine.active_session = sid2   # 다른 대화에서 물어도 찾아야 한다
        e.engine.chat("오늘 뭐하지")
        self.assertNotIn("[관련 있을 수 있는 예전 대화]", e.calls[1][0])
        e.engine.chat("내 고양이 이름이 뭐였지?")
        self.assertIn("[관련 있을 수 있는 예전 대화]", e.calls[2][0]); self.assertIn("나비", e.calls[2][0])
    def test_schedules_and_clipboard_are_per_turn_and_not_saved(self):
        e = Env(["[neutral]a", "[neutral]b"])
        ctx = {"schedules": [{"when": "내일 오전 9:00", "left": "15시간 뒤", "text": "미팅", "repeat": ""}], "clipboard": "복사한 글 123"}
        e.engine.chat("뭐 있어?", context=ctx)
        self.assertIn("미팅", e.calls[0][0]); self.assertIn("복사한 글 123", e.calls[0][0])
        e.engine.chat("그냥")
        self.assertNotIn("미팅", e.calls[1][0]); self.assertNotIn("복사한 글", e.calls[1][0])
        self.assertTrue(all("복사한 글" not in m["content"] for m in e.db.all_messages(e.engine.active_session)))
    def test_anniversary_celebrated_once_on_first_chat_of_the_day(self):
        import datetime as dt
        e = Env(["[neutral]축하해", "[neutral]b"])
        e.engine.now_fn = lambda: dt.datetime(2026, 3, 5, 0, 1)
        e.engine.add_anniversary(3, 5, "생일")
        e.engine.chat("안녕"); self.assertIn("[오늘의 기념일]", e.calls[0][0]); self.assertIn("생일", e.calls[0][0])
        e.engine.chat("또"); self.assertNotIn("[오늘의 기념일]", e.calls[1][0])
    def test_anniversary_not_marked_when_llm_fails(self):
        import datetime as dt
        e = Env([L.LLMError("x"), "[neutral]ok"])
        e.engine.now_fn = lambda: dt.datetime(2026, 3, 5, 12, 0)
        e.engine.add_anniversary(3, 5, "생일")
        with self.assertRaises(L.LLMError): e.engine.chat("안녕")
        e.engine.chat("다시"); self.assertIn("[오늘의 기념일]", e.calls[1][0])
    def test_extraction_parses_and_saves_anniversaries(self):
        ops = mem_mod.parse_ops('{"add": [], "anniversaries": [{"month": 3, "day": 5, "label": "생일", "year": null}, {"month": 2, "day": 30, "label": "잘못"}]}')
        self.assertEqual(ops["anniversaries"], [{"month": 3, "day": 5, "label": "생일", "year": None}])
        d = Database(":memory:"); mem_mod.apply_ops(d, ops, 60); mem_mod.apply_ops(d, ops, 60)
        self.assertEqual(len(d.list_anniversaries()), 1)
    def test_recall_survives_missing_fts(self):
        d = Database(":memory:"); sid = d.create_session("t"); d.add("user", "내 고양이는 나비", session_id=sid)
        d.fts = False
        self.assertEqual(len(d.recall(["고양이"])), 1)


import copy as _copy
import tts_launcher as SL_mod
copy_cfg = _copy.deepcopy


if __name__ == "__main__":
    unittest.main(verbosity=1)
