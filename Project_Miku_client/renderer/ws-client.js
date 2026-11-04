// Project Miku 서버(Ui/ws_server.py) 프로토콜 어댑터.
//
// 서버 -> 클라이언트 (JSON 텍스트 프레임, {op: ..., ...})
//   hello               { client_id }
//   server_ping         { ts }                 1초마다 -> client_pong 으로 응답 (60초 무응답 시 서버가 끊음)
//   status              { stage }              chat 처리 단계 (fetch_context | processing)
//   chat_result         { llm_response, emotion, audio_filename, audio_url }
//   observe_result      { should_speak, llm_response, reason, emotion, audio_url, ... }
//   notification_result observe_result 와 같은 형태
//   proactive_result    캐릭터가 먼저 거는 말 (같은 형태). 서버 처리가 실패해도 오류를 보내지 않는다
//   error               { code, message }
//   interrupted         { stream_id }          interrupt 에 대한 응답 (바로 온다). 멈춘 대화는 서버가 저장하지 않는다
//   stream_start / stream_search {query} / stream_meta {emotion, think} / stream_text {delta} / stream_audio {seq, audio_url, text}
//                       답이 만들어지는 동안 (chat 에 stream:true 를 붙였을 때). 끝에 chat_result{streamed:true} 가 온다 (음성은 이미 stream_audio 로 받음)
//   session_search_result {query, results:[{session_id,title,updated,role,snippet}]}
//   session_export      {filename, text, count}
//   memories            {memories:[{id,text,created,updated}]}
//   sessions            { sessions: [{id,title,created,updated,count}], active }   접속 직후와 대화/세션이 바뀔 때마다
//   session_active      { session_id, title, messages }   현재 대화가 바뀜 (새 대화/전환/지움). session_id 가 null 이면 새 대화
//   session_messages    { session_id, messages }          loadMessages() 의 응답
//
// 클라이언트 -> 서버: { op: 'chat', data: { text, think, user_name, ... } }
//   중단: interrupt   (지금 생각/말하는 중인 대답을 멈춘다)
//   먼저 말 걸기: proactive   (사용자가 말하지 않았는데 캐릭터가 먼저 말하게 한다)
//   세션: session_list / session_new / session_switch / session_delete / session_delete_all / session_rename / session_messages / session_search / session_export
//   프로필: profile_set {model, persona, emotions}   (지금 띄운 모델의 persona.txt 내용과 감정 태그. 접속 직후/모델 변경/저장 때 보냄)
//   기억: memories_list / memory_add / memory_update / memory_delete / memories_clear
//
// 오디오는 WebSocket 으로 오지 않고 audio_url 로 HTTP 다운로드한다 (wav).
//
// 앱 내부에서 쓰는 표준 이벤트:
//   { type: 'text',    text }
//   { type: 'emotion', emotion }
//   { type: 'audio',   data: ArrayBuffer, mime }
//   { type: 'status',  stage }
//   { type: 'error',   message }
//   { type: 'sessions', sessions, active }
//   { type: 'session_active', id, title, messages }
//   { type: 'session_messages', id, messages }

// ---------- 서버 -> 앱 ----------
import { repairWav } from './wav.js';

// 서버가 알려준 주소에서 음성을 받아 'audio' 이벤트로 만든다. 못 받으면 null (텍스트/표정은 그대로 보여준다)
async function fetchAudio(d, opts) {
  const url = resolveAudioUrl(d, opts);
  if (!url) return null;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const raw = await res.arrayBuffer();
    const declared = Number(res.headers && res.headers.get && res.headers.get('content-length')) || 0;
    const { buffer: data, note } = repairWav(raw);
    // url 은 로그용: 이 주소를 브라우저에 붙여 넣으면 서버가 만든 파일 자체가 끊겼는지 확인할 수 있다
    return { type: 'audio', data, mime: guessAudioMime(data), url, declared, note };
  } catch (e) {
    console.warn('오디오 다운로드 실패', url, e);
    return null;
  }
}

async function adaptResult(d, opts) {
  // observe/notification 은 말할지 서버가 판단해서 should_speak 로 알려준다
  if (d.should_speak === false) return [];

  const events = [];
  if (d.emotion) events.push({ type: 'emotion', emotion: String(d.emotion) });

  const text = d.llm_response ?? d.message;
  if (typeof text === 'string' && text) events.push({ type: 'text', text, streamed: d.streamed === true });

  const audio = await fetchAudio(d, opts);
  if (audio) events.push(audio);
  return events;
}

// audio_url 의 호스트는 서버 입장 주소라 클라이언트에서 안 맞을 수 있다.
// config.audioBaseUrl 이 있으면 audio_filename 과 합쳐서 쓴다. (예: http://127.0.0.1:9000)
function resolveAudioUrl(d, opts) {
  if (opts.audioBaseUrl && d.audio_filename) {
    return opts.audioBaseUrl.replace(/\/+$/, '') + '/' + d.audio_filename;
  }
  if (!d.audio_url) return null;
  try {
    const u = new URL(d.audio_url);
    // 서버가 0.0.0.0 등으로 알려주는 경우 접속에 쓴 호스트로 바꾼다
    if (['0.0.0.0', '::', '[::]', ''].includes(u.hostname)) u.hostname = new URL(opts.wsUrl).hostname;
    return u.href;
  } catch {
    return d.audio_url;
  }
}

export async function adapt(msg, opts) {
  const d = msg.data && typeof msg.data === 'object' ? msg.data : msg;
  switch (msg.op) {
    case 'chat_result':
    case 'observe_result':
    case 'notification_result':
    case 'proactive_result':
      return adaptResult(d, opts);
    case 'status':
      return [{ type: 'status', stage: d.stage }];
    case 'interrupted':
      return [{ type: 'interrupted', id: d.stream_id == null ? null : d.stream_id }];
    case 'error':
      return [{ type: 'error', message: d.message || '서버 오류', code: d.code }];
    case 'sessions':
      return [{ type: 'sessions', sessions: Array.isArray(d.sessions) ? d.sessions : [], active: d.active == null ? null : d.active }];
    case 'session_active':
      return [{ type: 'session_active', id: d.session_id == null ? null : d.session_id, title: d.title || '', messages: Array.isArray(d.messages) ? d.messages : [] }];
    case 'session_messages':
      return [{ type: 'session_messages', id: d.session_id, messages: Array.isArray(d.messages) ? d.messages : [] }];
    case 'stream_start':
      return [{ type: 'stream_start', id: d.stream_id }];
    case 'stream_meta':
      return [{ type: 'stream_meta', id: d.stream_id, emotion: d.emotion ? String(d.emotion) : '', think: typeof d.think === 'string' ? d.think : '' }];
    case 'stream_search':   // 답을 만들기 전에 웹 검색 중
      return [{ type: 'stream_search', id: d.stream_id, query: typeof d.query === 'string' ? d.query : '' }];
    case 'stream_text':
      return typeof d.delta === 'string' && d.delta ? [{ type: 'stream_text', id: d.stream_id, delta: d.delta }] : [];
    case 'stream_audio': {
      const audio = await fetchAudio(d, opts);   // 문장마다 도착하는 대로 받아 순서대로 재생 대기열에 넣는다 (메시지 처리 순서는 chain 이 보장)
      return audio ? [{ ...audio, streamId: d.stream_id, seq: d.seq }] : [];
    }
    case 'session_search_result':
      return [{ type: 'session_search_result', query: d.query || '', results: Array.isArray(d.results) ? d.results : [] }];
    case 'session_export':
      return [{ type: 'session_export', filename: d.filename || 'miku.md', text: typeof d.text === 'string' ? d.text : '', count: d.count || 0 }];
    case 'memories':
      return [{ type: 'memories', memories: Array.isArray(d.memories) ? d.memories : [] }];
    case 'anniversaries':
      return [{ type: 'anniversaries', anniversaries: Array.isArray(d.anniversaries) ? d.anniversaries : [] }];
    default:
      return []; // hello, pong, monitoring, feedback_result ...
  }
}

// ---------- 앱 -> 서버 ----------
function encodeChat(text, opts, imageIds, nowPlaying, context) {
  const data = { text, think: false };
  if (context) data.context = context;   // 이번 턴에만 쓰는 부가 정보 (일정, 클립보드)
  if (nowPlaying) data.now_playing = nowPlaying;   // 곡에 대해 물은 메시지에만 (재생 중인 곡 정보)
  if (opts.streaming !== false) data.stream = true;   // 서버가 스트리밍을 못 하면 보통 응답으로 돌아온다
  if (imageIds && imageIds.length) data.image_ids = imageIds;
  if (opts.userName) data.user_name = opts.userName;
  return JSON.stringify({ op: 'chat', data });
}

function encodeProactive(opts, imageIds) {
  const data = {};
  if (imageIds && imageIds.length) data.image_ids = imageIds;   // 화면 공유 모드일 때 지금 화면
  if (opts.userName) data.user_name = opts.userName;
  return JSON.stringify({ op: 'proactive', data });
}

function encodeNotification(appName, content, opts) {
  const data = { app_name: appName, content };
  if (opts.userName) data.user_name = opts.userName;
  return JSON.stringify({ op: 'notification', data });
}

// ---------- 유틸 ----------
function guessAudioMime(buf) {
  const h = new Uint8Array(buf.slice(0, 4));
  const s = String.fromCharCode(...h);
  if (s === 'RIFF') return 'audio/wav';
  if (s === 'OggS') return 'audio/ogg';
  if (s.startsWith('ID3') || (h[0] === 0xff && (h[1] & 0xe0) === 0xe0)) return 'audio/mpeg';
  return 'audio/wav';
}

function stableClientId() {
  try {
    let id = localStorage.getItem('miku.clientId');
    if (!id) {
      id = 'miku-' + Math.random().toString(16).slice(2, 10);
      localStorage.setItem('miku.clientId', id);
    }
    return id;
  } catch {
    return 'miku-' + Math.random().toString(16).slice(2, 10);
  }
}

// config.serverUrl 이 비었거나 ws:// 가 빠져 있어도 동작하게 다듬는다.
//   "127.0.0.1:3545" -> ws://127.0.0.1:3545/ws,  "http://host:3545" -> ws://host:3545/ws
export const DEFAULT_WS_URL = 'ws://127.0.0.1:3545/ws';
export function normalizeWsUrl(raw) {
  let s = typeof raw === 'string' ? raw.trim() : '';
  if (!s) { console.warn('[ws] serverUrl 이 비어 있어서 기본값을 씁니다:', DEFAULT_WS_URL); return DEFAULT_WS_URL; }
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = 'ws://' + s;
  s = s.replace(/^http:/i, 'ws:').replace(/^https:/i, 'wss:');
  try {
    const u = new URL(s);
    if (u.pathname === '' || u.pathname === '/') u.pathname = '/ws';
    return u.href;
  } catch {
    console.warn('[ws] serverUrl 이 올바르지 않아요:', JSON.stringify(raw), '-> 기본값', DEFAULT_WS_URL);
    return DEFAULT_WS_URL;
  }
}

export class MikuSocket {
  // opts: { onEvent, onStatus, userName, audioBaseUrl }
  constructor(url, opts) {
    this.url = normalizeWsUrl(url);
    this.opts = opts;
    this.onEvent = opts.onEvent;
    this.onStatus = opts.onStatus || (() => {});
    this.ws = null;
    this.closedByUser = false;
    this.retry = 0;
    this.chain = Promise.resolve(); // 메시지 처리 순서 보장 (오디오 다운로드가 끼어도 순서 유지)
    this.clientId = stableClientId();
  }

  connect() {
    this.closedByUser = false;
    this._open();
  }

  _wsUrl() {
    const u = new URL(this.url);
    if (!u.searchParams.has('client_id')) u.searchParams.set('client_id', this.clientId);
    return u.href;
  }

  _open() {
    try {
      this.ws = new WebSocket(this._wsUrl());
    } catch (e) {
      console.warn('WebSocket 생성 실패', e);
      this._scheduleRetry();
      return;
    }
    this.ws.onopen = () => { this.retry = 0; this.onStatus(true); };
    this.ws.onclose = () => { this.onStatus(false); if (!this.closedByUser) this._scheduleRetry(); };
    this.ws.onerror = () => { /* onclose 에서 처리 */ };
    this.ws.onmessage = (ev) => this._onMessage(ev.data);
  }

  _onMessage(raw) {
    if (typeof raw !== 'string') return; // 서버는 바이너리 프레임을 쓰지 않는다
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }

    // 하트비트는 즉시 처리 (큐에 쌓지 않는다)
    if (msg.op === 'server_ping') { this._send({ op: 'client_pong' }); return; }
    console.info('[ws<-]', msg.op, JSON.stringify(msg).slice(0, 300));

    const adaptOpts = { ...this.opts, wsUrl: this.url };
    this.chain = this.chain
      .then(() => adapt(msg, adaptOpts))
      .then((events) => { for (const e of events) this.onEvent({ ...e, op: msg.op }); })
      .catch((err) => console.warn('메시지 처리 실패', err));
  }

  _scheduleRetry() {
    const delay = Math.min(10000, 1000 * 2 ** this.retry++);
    setTimeout(() => { if (!this.closedByUser) this._open(); }, delay);
  }

  _send(obj) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(typeof obj === 'string' ? obj : JSON.stringify(obj));
    return true;
  }

  // imageIds: uploadImage() 가 돌려준 id 목록 (화면 캡처 등)
  // 지금 생각/말하는 중인 대답을 멈추게 한다 (서버는 interrupted 로 바로 답한다)
  interrupt() { return this._send({ op: 'interrupt' }); }

  sendText(text, imageIds, nowPlaying, context) {
    console.info('[ws->] chat', text, imageIds && imageIds.length ? `+이미지 ${imageIds.length}장` : '', nowPlaying ? `+재생 중인 곡(${nowPlaying.title})` : '',
      context && context.schedules ? `+일정 ${context.schedules.length}개` : '', context && context.clipboard ? `+클립보드(${context.clipboard.length}자)` : '');
    return this._send(encodeChat(text, this.opts, imageIds, nowPlaying, context));
  }

  // ---------- 대화(세션) ----------
  // 서버를 켤 때마다 새 대화로 시작하고, 첫 메시지를 보내면 목록에 저장된다. 대화마다 기록이 따로라 문맥이 섞이지 않는다.
  _session(op, data) {
    console.info('[ws->]', op, data ? JSON.stringify(data) : '');
    return this._send(data ? { op, data } : { op });
  }
  listSessions() { return this._session('session_list'); }
  newSession() { return this._session('session_new'); }
  switchSession(id) { return this._session('session_switch', { session_id: id }); }
  deleteSession(id) { return this._session('session_delete', { session_id: id }); }
  searchSessions(query) { return this._session('session_search', { query }); }
  exportSession(id, format = 'md') { return this._session('session_export', { session_id: id == null ? null : id, format }); }
  listMemories() { return this._session('memories_list'); }
  addMemory(text) { return this._session('memory_add', { text }); }
  updateMemory(id, text) { return this._session('memory_update', { id, text }); }
  deleteMemory(id) { return this._session('memory_delete', { id }); }
  clearMemories() { return this._session('memories_clear'); }
  listAnniversaries() { return this._session('anniv_list'); }
  addAnniversary(month, day, label, year) { return this._session('anniv_add', { month, day, label, year: year || null }); }
  deleteAnniversary(id) { return this._session('anniv_delete', { id }); }
  deleteAllSessions() { return this._session('session_delete_all'); }
  renameSession(id, title) { return this._session('session_rename', { session_id: id, title }); }
  loadMessages(id) { return this._session('session_messages', { session_id: id }); }

  // 서버는 이미지를 WebSocket 으로 받지 않고 POST /upload/image (multipart, 필드명 file) 로 받는다.
  // 주소는 ws 주소와 같은 호스트/포트. config.uploadUrl 로 바꿀 수 있다.
  uploadUrl() {
    if (this.opts.uploadUrl) return this.opts.uploadUrl;
    const u = new URL(this.url);
    u.protocol = u.protocol === 'wss:' ? 'https:' : 'http:';
    u.pathname = '/upload/image';
    u.search = '';
    return u.href;
  }

  async uploadImage(blob, filename = 'screen.jpg') {
    const form = new FormData();
    form.append('file', blob, filename);
    const res = await fetch(this.uploadUrl(), { method: 'POST', body: form });
    if (!res.ok) throw new Error(`업로드 실패 HTTP ${res.status}`);
    const j = await res.json();
    if (!j || !j.image_id) throw new Error('서버가 image_id 를 돌려주지 않았어요');
    console.info('[upload] 이미지 업로드', j.image_id, `${Math.round(blob.size / 1024)}KB`);
    return j.image_id;
  }

  // 일정 알림 등 클라이언트가 서버에 말을 시킬 때 사용
  sendNotification(appName, content) {
    return this._send(encodeNotification(appName, content, this.opts));
  }
  // 지금 띄운 모델의 persona/감정 태그를 서버에 알린다 (서버는 이걸로 프롬프트를 만든다). 연결이 없으면 false
  setProfile(profile) {
    console.info('[ws->] profile_set', profile && profile.model, `persona ${(profile && profile.persona || '').length}자`, profile && profile.emotions ? Object.keys(profile.emotions).join(',') : '(기본 감정)');
    return this._send({ op: 'profile_set', data: profile || {} });
  }
  // 먼저 말 걸기: 연결이 없어 보내지 못하면 false (호출한 쪽이 나중에 다시 시도한다)
  sendProactive(imageIds) {
    console.info('[ws->] proactive', imageIds && imageIds.length ? '(화면 포함)' : '');
    return this._send(encodeProactive(this.opts, imageIds));
  }
  get isOpen() { return !!this.ws && this.ws.readyState === WebSocket.OPEN; }

  setUrl(url) {
    this.url = normalizeWsUrl(url);
    this.close();
    this.connect();
  }

  close() {
    this.closedByUser = true;
    if (this.ws) this.ws.close();
  }
}
