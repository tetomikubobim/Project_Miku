"""Project Miku 경량 서버 (FastAPI + SQLite). 실행: python server.py"""
import asyncio
import atexit
import os
import sys
import time
import uuid

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import configfile
import health
import lifecycle
import lyrics_query
import lyrics_translate
from db import Database
from engine import Engine
import rvc
import stt
import websearch
from tts_launcher import Launcher
from ws_handler import serve_socket

BASE = os.path.dirname(os.path.abspath(__file__))
MAX_UPLOAD = 15 * 1024 * 1024
MAX_AUDIO = 30 * 1024 * 1024
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")
LAUNCHER_ENV = "MIKU_LAUNCHER"   # 통합 런처가 서버를 켤 때 1 로 준다: 클라이언트가 끊겨도 서버를 끄지 않는다 (끄는 건 런처가 한다)


def load_config():
    try:
        return configfile.load(BASE)
    except configfile.ConfigError as e:
        print(e)
        sys.exit(1)


def create_app(cfg=None, engine=None, on_idle=None, launchers=None):
    """on_idle: 서버를 종료시키는 함수 (클라이언트가 모두 끊긴 뒤 grace 초가 지났을 때, 또 런처의 /admin/shutdown 요청 때 부른다).
    launchers: {"sovits": Launcher, "rvc": Launcher} - /health 가 GPT-SoVITS/RVC 상태를 알려 줄 때 쓴다."""
    cfg = cfg or load_config()
    own_engine = engine is None
    if engine is None:
        db = Database(os.path.join(BASE, cfg.get("paths", {}).get("db", "miku.db")))
        engine = Engine(cfg, db, BASE)
        if websearch.settings(cfg)["enabled"] is not False and not websearch.available():
            print("[search] 웹 검색을 쓰려면 `pip install -U ddgs` 를 실행하세요 (설치 전에는 검색 없이 동작해요)")
    app = FastAPI(title="Project Miku Server")
    lock = asyncio.Lock()
    # 음성 인식(faster-whisper): 서버가 직접 처리한다. 서버를 켤 때 모델을 미리 불러와 첫 인식이 느려지지 않게 한다.
    transcriber = stt.Transcriber(cfg, BASE)
    app.state.stt = transcriber
    if own_engine:
        ok, why = transcriber.available()
        if ok:
            transcriber.preload()
        elif stt.settings(cfg)["enabled"]:
            print(f"[stt] {why}")
    launched = bool(os.environ.get(LAUNCHER_ENV))
    watcher = lifecycle.ClientWatcher(lifecycle.grace_from(cfg), on_idle) if on_idle and lifecycle.enabled(cfg) and not launched else None
    if launched and own_engine:
        print("[server] 런처에서 실행 중이에요. 클라이언트가 끊겨도 서버를 끄지 않아요 (런처에서 끄세요).")
    started, state = time.time(), {"clients": 0}

    app.mount("/audio", StaticFiles(directory=engine.audio_dir), name="audio")

    # ---- 런처용: 상태 확인과 관리 (관리 요청은 이 컴퓨터에서 온 것만 받는다) ----
    @app.get("/health")
    async def get_health():
        body = await asyncio.to_thread(health.build, cfg, transcriber, launchers or {}, state["clients"], started, launched_by_launcher=launched)
        return JSONResponse(body)

    def _local_only(request):
        return (request.client.host if request.client else "") in LOCAL_HOSTS

    @app.post("/admin/reload")
    async def admin_reload(request: Request):
        """config.yaml 을 다시 읽어서 실행 중인 설정에 반영한다 (런처가 설정을 저장한 뒤 부른다). 서버를 다시 켜야 하는 항목은 그대로 둔다."""
        if not _local_only(request):
            return JSONResponse({"ok": False, "error": "이 컴퓨터에서만 쓸 수 있어요"}, status_code=403)
        try:
            new = await asyncio.to_thread(configfile.load, BASE)
        except configfile.ConfigError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)   # 문법이 틀렸으면 지금 설정을 그대로 둔다
        for target in {id(t): t for t in (engine.cfg, cfg)}.values():
            configfile.replace_inplace(target, new)
        print("[server] 설정을 다시 읽었어요 (config.yaml)")
        return JSONResponse({"ok": True})

    @app.post("/admin/shutdown")
    async def admin_shutdown(request: Request):
        """서버를 정상 종료한다 (GPT-SoVITS/RVC 도 같이 끈다). 런처의 '중지' 버튼이 부른다."""
        if not _local_only(request):
            return JSONResponse({"ok": False, "error": "이 컴퓨터에서만 쓸 수 있어요"}, status_code=403)
        if on_idle is None:
            return JSONResponse({"ok": False, "error": "이 서버는 종료 요청을 받지 않아요"}, status_code=501)
        print("[server] 종료 요청을 받았어요.")
        asyncio.get_running_loop().call_later(0.2, on_idle)   # 응답을 먼저 돌려보낸 뒤 끈다
        return JSONResponse({"ok": True})

    @app.post("/upload/image")
    async def upload_image(file: UploadFile = File(...)):
        ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"지원하지 않는 이미지 형식: {ext}")
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "이미지가 너무 커요 (15MB 제한)")
        image_id = f"{uuid.uuid4()}{ext}"
        with open(os.path.join(engine.upload_dir, image_id), "wb") as f:
            f.write(data)
        return JSONResponse({"ok": True, "image_id": image_id})

    # 음성 -> 글자. OpenAI 호환 경로(/v1/audio/transcriptions)라서 클라이언트의 stt.url 로 그대로 쓸 수 있다.
    @app.get("/stt/status")
    async def stt_status():
        ok, why = transcriber.available()
        return JSONResponse({"available": ok, "reason": why, "ready": transcriber.ready, "loading": transcriber.loading, "error": transcriber.last_error})

    @app.post("/stt")
    @app.post("/v1/audio/transcriptions")
    async def transcribe_audio(file: UploadFile = File(...), model: str = Form(None), language: str = Form(None),
                               prompt: str = Form(None), response_format: str = Form("json")):
        ok, why = transcriber.available()
        if not ok:
            raise HTTPException(503, why)
        data = await file.read()
        if not data:
            raise HTTPException(400, "받은 음성이 비어 있어요")
        if len(data) > MAX_AUDIO:
            raise HTTPException(413, "음성이 너무 커요 (30MB 제한)")
        try:
            res = await asyncio.to_thread(transcriber.transcribe, data, language, prompt)   # 계산 중에도 다른 요청/대화가 막히지 않게
        except stt.SttError as e:
            raise HTTPException(500, str(e)) from e
        if response_format == "text":
            return PlainTextResponse(res["text"])
        return JSONResponse({"text": res["text"], "language": res["language"], "duration": res["duration"]})

    @app.post("/lyrics/normalize")
    async def lyrics_normalize(request: Request):
        """가사 검색어 보정: 제목/가수를 LLM 으로 정리하고 검색 후보를 돌려준다 (클라이언트가 가사를 못 찾았을 때만 부른다)."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON 이 필요해요")
        title = str((body or {}).get("title") or "").strip()
        artist = str((body or {}).get("artist") or "").strip()
        if not title:
            raise HTTPException(400, "title 이 비어 있어요")

        def ask(prompt):
            return engine.llm_fn(engine._aux_llm(), lyrics_query.SYSTEM, [{"role": "user", "content": prompt}], None,
                                 max_tokens=400, temperature=0.1)
        try:
            res = await asyncio.to_thread(lyrics_query.normalize, ask, engine.db, title, artist)
        except ValueError as e:
            raise HTTPException(502, str(e)) from e
        except Exception as e:   # LLMError 등
            raise HTTPException(502, f"LLM 보정 실패: {e}") from e
        return JSONResponse(res)

    @app.post("/lyrics/translate")
    async def lyrics_translate_ep(request: Request):
        """가사 번역: 원문 줄 목록을 LLM 으로 번역해 같은 개수의 줄로 돌려준다 (클라이언트가 곡마다 한 번만 부르고 저장한다)."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON 이 필요해요")
        body = body or {}
        lines = body.get("lines")
        if not isinstance(lines, list) or not lines:
            raise HTTPException(400, "lines 가 비어 있어요")
        lang = str(body.get("lang") or "ko").strip().lower()
        if lang not in lyrics_translate.LANG_NAMES:
            raise HTTPException(400, f"지원하지 않는 언어예요: {lang}")

        def ask(prompt, n):
            return engine.llm_fn(engine._aux_llm(), lyrics_translate.SYSTEM, [{"role": "user", "content": prompt}], None,
                                 max_tokens=min(8192, 400 + 90 * n), temperature=0.2)
        try:
            res = await asyncio.to_thread(lyrics_translate.translate, ask, engine.db, lines, lang,
                                          body.get("title") or "", body.get("artist") or "", bool(body.get("force")))
        except ValueError as e:
            raise HTTPException(502, str(e)) from e
        except Exception as e:   # LLMError 등
            raise HTTPException(502, f"LLM 번역 실패: {e}") from e
        res["model"] = str((engine.cfg.get("llm") or {}).get("model") or "")
        return JSONResponse(res)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        host = cfg.get("public_host") or websocket.headers.get("host") or f"127.0.0.1:{cfg.get('port', 3545)}"
        if watcher:
            watcher.connected()
        state["clients"] += 1
        try:
            await serve_socket(websocket, engine, f"http://{host}", lock=lock)
        finally:
            state["clients"] -= 1
            if watcher:
                watcher.disconnected()

    return app


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):   # 런처가 파이프로 읽어도 줄 단위로 바로 나오고 한글이 깨지지 않게
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass
    config = load_config()
    # tts.provider 가 gptsovits 일 때만 GPT-SoVITS 를 같이 켠다 (edge 등이면 아무것도 하지 않음). 서버가 꺼지면 같이 꺼진다.
    sovits = Launcher(config.get("tts", {}), BASE)
    # tts.provider 가 edge 이고 tts.rvc.enabled 일 때만 RVC(목소리 변환)도 같이 켠다
    rvc_proc = rvc.make_launcher(config.get("tts", {}), BASE)
    for p in (sovits, rvc_proc):
        atexit.register(p.stop)  # 콘솔 창을 닫거나 예외로 끝나도 정리
        p.start()
    server = None

    def stop_server():  # 클라이언트가 모두 끊기면: uvicorn 을 정상 종료시키고, 아래 finally 에서 GPT-SoVITS 도 끈다
        if server is not None:
            server.should_exit = True

    try:
        server = uvicorn.Server(uvicorn.Config(
            create_app(config, on_idle=stop_server, launchers={"sovits": sovits, "rvc": rvc_proc}), host=config.get("host", "127.0.0.1"), port=config.get("port", 3545), log_level="warning"))
        server.run()
    finally:
        sovits.stop()
        rvc_proc.stop()
