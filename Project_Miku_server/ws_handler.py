"""WebSocket 프로토콜 처리 (프레임워크와 무관: ws 는 accept/receive_text/send_text 만 가진 객체).

서버 -> 클라이언트: hello, server_ping(1초), status, chat_result / notification_result / proactive_result, error,
                   sessions {sessions, active}                 (접속 직후와 대화/세션이 바뀔 때마다)
                   session_active {session_id, title, messages} (새 대화/전환/지움으로 현재 대화가 바뀜. session_id 가 null 이면 새 대화)
                   session_messages {session_id, messages}      (session_messages 요청의 응답)
                   session_search_result {query, results}       (session_search 의 응답)
                   session_export {filename, text, count}       (session_export 의 응답)
                   memories {memories}                          (기억 목록: memories_list / memory_add / memory_update / memory_delete / memories_clear 의 응답)
                   stream_start / stream_meta {emotion, think} / stream_text {delta} / stream_audio {seq, audio_url, text}
                                                                (chat 에 stream:true 를 붙였을 때 답이 만들어지는 동안. 끝에 chat_result{streamed:true} 가 온다)
                   (모델이 바뀌면 대화 기록도 chat_log/<모델>/ 의 것으로 바뀌고, session_active{null} + sessions 가 온다)
클라이언트 -> 서버: profile_set {model, persona, emotions:{태그:설명}, voice:{gpt_weights_path, sovits_weights_path, ref_audio_path, prompt_text, prompt_lang, speed_factor(0.5~2.0 숫자)}} (지금 띄운 Live2D 모델의 persona/감정 태그/음성. 접속 직후와 모델/프로필이 바뀔 때 보냄),
                   chat, interrupt(지금 생각/말하는 중인 대답을 멈춘다. 중단된 대화는 저장하지 않고 interrupted{stream_id} 로 바로 알린다),
                   notification, proactive(먼저 말 걸기: 조용히 처리, 실패해도 오류를 보내지 않음), client_pong, ping,
                   session_list, session_new, session_switch {session_id}, session_delete {session_id},
                   session_rename {session_id, title}, session_messages {session_id}, session_delete_all
"""
import asyncio
import json
import threading
import time

import llm as llm_mod


class _Ctl:
    """진행 중이거나 순서를 기다리는 chat 하나를 밖에서 멈추기 위한 손잡이."""
    def __init__(self):
        self.cancel = threading.Event()   # set 되면 답 만들기를 멈춘다 (engine 이 이 이벤트를 본다)
        self.user = False                 # 사용자가 중단 버튼으로 멈춘 경우 (오류가 아니라서 조용히 끝낸다)
        self.stream_id = None


def _after_delete_all(res):
    """전체 삭제 뒤에는 항상 새 대화 상태로 돌아간다."""
    return "session_active", {"session_id": None, "title": "", "messages": []}


def _after_delete(res):
    """지운 대화가 지금 대화였다면 새 대화 상태로 돌아갔다고 알린다."""
    if res.get("was_active"):
        return "session_active", {"session_id": None, "title": "", "messages": []}
    return None


def _after_profile(res):
    """모델이 바뀌어 대화 기록이 그 모델의 것으로 바뀌었으면 새 대화 상태로 돌아갔다고 알린다 (목록은 run_session_job 이 다시 보낸다)."""
    if res.get("db_changed"):
        return "session_active", {"session_id": None, "title": "", "messages": []}
    return None


async def serve_socket(ws, engine, base_url, ping_interval=1.0, lock=None):
    """base_url: 클라이언트가 접속한 주소(http://host:port). 음성 파일 주소를 만드는 데 쓴다."""
    lock = lock or asyncio.Lock()
    send_lock = asyncio.Lock()

    async def send(obj):
        async with send_lock:
            await ws.send_text(json.dumps(obj, ensure_ascii=False))

    async def pinger():
        try:
            while True:
                await asyncio.sleep(ping_interval)
                await send({"op": "server_ping", "ts": time.time()})
        except Exception:
            pass

    async def push_sessions():
        await send({"op": "sessions", **(await asyncio.to_thread(engine.list_sessions))})

    async def run_session_job(fn, result_op=None, push=True, err_code="session_error"):
        """세션 관리 명령. 대화 처리와 같은 lock 을 써서, 답을 만드는 도중에 대화가 바뀌거나 지워지지 않게 한다.
        결과 전송과 목록 갱신도 lock 안에서 해야 한다: 밖에서 보내면 연달아 온 다른 명령의 출력과 순서가 뒤집혀
        낡은 목록이 최신 목록 뒤에 도착할 수 있다."""
        async with lock:
            try:
                res = await asyncio.to_thread(fn)
            except ValueError as e:  # 없는 대화, 빈 이름 등 -> 목록을 다시 보내 화면을 맞춘다
                await send({"op": "error", "code": err_code, "message": str(e)})
                await push_sessions()
                return
            except Exception as e:  # noqa: BLE001
                print(f"[server] 세션 명령 실패: {e!r}")
                await send({"op": "error", "code": "internal", "message": f"서버 오류: {e}"})
                return
            if callable(result_op):
                out = result_op(res)
                if out:
                    await send({"op": out[0], **out[1]})
            elif result_op:
                await send({"op": result_op, **res})
            if push:  # session_messages 는 목록을 다시 보내지 않는다 (클라이언트가 목록을 받고 또 요청하는 반복을 막기 위해)
                await push_sessions()

    async def run_stream_job(op, fn, result_op, ctl=None):
        """답을 만드는 동안 stream_* 를 보내고, 끝나면 chat_result{streamed:true} 를 보낸다.
        fn(emit) 은 스레드에서 돌고, emit 은 이벤트 루프에서 순서대로 보낸다."""
        loop = asyncio.get_running_loop()

        def emit(ev):
            if ctl is not None and ctl.stream_id is None:
                ctl.stream_id = ev.get("stream_id")
            name = ev.get("audio_filename")
            if ev.get("op") == "stream_audio" and name:
                ev = {**ev, "audio_url": f"{base_url}/audio/{name}"}
            asyncio.run_coroutine_threadsafe(send(ev), loop).result(timeout=30)

        async with lock:
            if ctl is not None and ctl.cancel.is_set():   # 순서를 기다리는 사이에 중단됨
                return
            try:
                await send({"op": "status", "stage": "fetch_context"})
                await send({"op": "status", "stage": "processing"})
                res = await asyncio.to_thread(fn, emit)
            except llm_mod.LLMError as e:
                if ctl is not None and ctl.user:
                    print(f"[server] {op} 사용자가 중단했어요")
                    return
                print(f"[server] {op} LLM 오류: {e}")
                await send({"op": "error", "code": "llm_error", "message": str(e)})
                return
            except Exception as e:  # noqa: BLE001
                if ctl is not None and ctl.user:
                    return
                print(f"[server] {op} 처리 실패: {e!r}")
                await send({"op": "error", "code": "internal", "message": f"서버 오류: {e}"})
                return
            if res is None or (ctl is not None and ctl.user):
                return
            res["audio_url"] = None
            await send({"op": result_op, **res})
            await push_sessions()

    async def run_job(op, fn, result_op, quiet=False, ctl=None):
        """quiet=True: 사용자가 요청하지 않은 작업(먼저 말 걸기). 처리 중 표시도, 실패 알림도 보내지 않고 서버 로그에만 남긴다."""
        async with lock:  # 한 번에 하나의 대화만 처리 (순서/기록 보장)
            if ctl is not None and ctl.cancel.is_set():   # 순서를 기다리는 사이에 중단됨
                return
            try:
                if not quiet:
                    await send({"op": "status", "stage": "fetch_context"})
                    await send({"op": "status", "stage": "processing"})
                res = await asyncio.to_thread(fn)
            except llm_mod.LLMError as e:
                print(f"[server] {op} LLM 오류: {e}")
                if not quiet:
                    await send({"op": "error", "code": "llm_error", "message": str(e)})
                return
            except Exception as e:  # noqa: BLE001
                print(f"[server] {op} 처리 실패: {e!r}")
                if not quiet:
                    await send({"op": "error", "code": "internal", "message": f"서버 오류: {e}"})
                return
            if res is None or (ctl is not None and ctl.user):   # 중단 버튼을 눌렀으면 이미 만든 답도 보내지 않는다
                return
            name = res.get("audio_filename")
            res["audio_url"] = f"{base_url}/audio/{name}" if name else None
            await send({"op": result_op, **res})
            await push_sessions()  # 제목/개수/순서가 바뀌었으니 목록을 갱신 (lock 안에서: 순서 보장)

    await ws.accept()
    await send({"op": "hello", "client_id": "server"})
    await push_sessions()
    ping_task = asyncio.create_task(pinger())
    jobs = set()
    ctls = set()   # 아직 끝나지 않은 chat 들 (interrupt 로 멈출 대상)
    try:
        while True:
            try:
                raw = await ws.receive_text()
            except Exception:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            op = msg.get("op")
            d = msg.get("data") if isinstance(msg.get("data"), dict) else {}
            if op == "chat":
                ids = list(d.get("image_ids") or [])
                if d.get("image_id"):
                    ids.append(d["image_id"])
                ctl = _Ctl()
                ctls.add(ctl)
                if d.get("stream") and engine.can_stream():
                    job = asyncio.create_task(run_stream_job(
                        op, lambda emit, d=d, ids=ids, ctl=ctl: engine.chat_stream(d.get("text"), ids, emit=emit, cancel=ctl.cancel, now_playing=d.get("now_playing"), context=d.get("context")),
                        "chat_result", ctl))
                else:
                    job = asyncio.create_task(run_job(
                        op, lambda d=d, ids=ids: engine.chat(d.get("text"), ids, now_playing=d.get("now_playing"), context=d.get("context")), "chat_result", ctl=ctl))
                job.add_done_callback(lambda _t, c=ctl: ctls.discard(c))
            elif op == "interrupt":
                hit = [c for c in list(ctls) if not c.cancel.is_set()]
                for c in hit:
                    c.user = True
                    c.cancel.set()
                # 멈춘 쪽이 정리되길 기다리지 않고 바로 알린다 (음성 만들던 중이면 몇 초 걸릴 수 있다). 할 일이 없었어도 항상 답한다.
                await send({"op": "interrupted", "stream_id": next((c.stream_id for c in hit if c.stream_id), None)})
                continue
            elif op == "notification":
                job = asyncio.create_task(run_job(
                    op, lambda d=d: engine.notification(d.get("app_name"), d.get("content"), d.get("image_id")),
                    "notification_result"))
            elif op == "proactive":
                pids = list(d.get("image_ids") or [])   # 화면 공유 모드일 때 클라이언트가 같이 올린 화면
                job = asyncio.create_task(run_job(
                    op, lambda d=d, pids=pids: engine.proactive(pids), "proactive_result", quiet=True))
            elif op == "profile_set":
                job = asyncio.create_task(run_session_job(lambda d=d: engine.set_profile(d), _after_profile, err_code="profile_error"))
            elif op == "session_list":
                job = asyncio.create_task(run_session_job(lambda: {}))
            elif op == "session_new":
                job = asyncio.create_task(run_session_job(engine.new_session, "session_active"))
            elif op == "session_switch":
                job = asyncio.create_task(run_session_job(lambda d=d: engine.switch_session(d.get("session_id")), "session_active"))
            elif op == "session_messages":
                job = asyncio.create_task(run_session_job(lambda d=d: engine.session_messages(d.get("session_id")), "session_messages", push=False))
            elif op == "session_rename":
                job = asyncio.create_task(run_session_job(lambda d=d: engine.rename_session(d.get("session_id"), d.get("title"))))
            elif op == "session_delete":
                job = asyncio.create_task(run_session_job(lambda d=d: engine.delete_session(d.get("session_id")), _after_delete))
            elif op == "session_delete_all":
                job = asyncio.create_task(run_session_job(engine.delete_all_sessions, _after_delete_all))
            elif op == "session_search":
                job = asyncio.create_task(run_session_job(lambda d=d: engine.search(d.get("query")), "session_search_result", push=False))
            elif op == "session_export":
                job = asyncio.create_task(run_session_job(
                    lambda d=d: engine.export(d.get("session_id"), d.get("format", "md")), "session_export", push=False))
            elif op == "memories_list":
                job = asyncio.create_task(run_session_job(engine.memories, "memories", push=False, err_code="memory_error"))
            elif op == "memory_add":
                job = asyncio.create_task(run_session_job(lambda d=d: engine.add_memory(d.get("text")), "memories", push=False, err_code="memory_error"))
            elif op == "memory_update":
                job = asyncio.create_task(run_session_job(
                    lambda d=d: engine.update_memory(d.get("id"), d.get("text")), "memories", push=False, err_code="memory_error"))
            elif op == "memory_delete":
                job = asyncio.create_task(run_session_job(lambda d=d: engine.delete_memory(d.get("id")), "memories", push=False, err_code="memory_error"))
            elif op == "memories_clear":
                job = asyncio.create_task(run_session_job(engine.clear_memories, "memories", push=False, err_code="memory_error"))
            elif op == "anniv_list":
                job = asyncio.create_task(run_session_job(engine.anniversaries, "anniversaries", push=False, err_code="memory_error"))
            elif op == "anniv_add":
                job = asyncio.create_task(run_session_job(
                    lambda d=d: engine.add_anniversary(d.get("month"), d.get("day"), d.get("label"), d.get("year")), "anniversaries", push=False, err_code="memory_error"))
            elif op == "anniv_delete":
                job = asyncio.create_task(run_session_job(lambda d=d: engine.delete_anniversary(d.get("id")), "anniversaries", push=False, err_code="memory_error"))
            elif op == "ping":
                await send({"op": "pong", "ts": time.time()})
                continue
            else:
                continue  # client_pong 등
            jobs.add(job)
            job.add_done_callback(jobs.discard)
    finally:
        ping_task.cancel()
