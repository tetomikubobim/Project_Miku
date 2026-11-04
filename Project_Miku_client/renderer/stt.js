// 음성 -> 글자. OpenAI 호환 /v1/audio/transcriptions 엔드포인트를 쓴다.
//
// 기본은 Project Miku 서버(serverUrl)가 직접 처리한다 (서버의 config.yaml 의 stt: faster-whisper). 따로 설정할 것이 없다.
// 다른 STT 서버(faster-whisper-server/speaches, whisper.cpp server, OpenAI 등)를 쓰려면 config.json 에 적는다:
//   "stt": { "url": "http://127.0.0.1:8000/v1/audio/transcriptions", "model": "Systran/faster-whisper-small",
//            "language": "ko", "apiKey": "", "prompt": "", "timeout": 120 }
// url 을 비워 두면 serverUrl(ws://127.0.0.1:3545/ws)에서 http://127.0.0.1:3545/v1/audio/transcriptions 를 만들어 쓴다.

// serverUrl(ws://host:port/ws) -> http://host:port/v1/audio/transcriptions
export function deriveSttUrl(serverUrl) {
  try {
    const u = new URL(serverUrl);
    const proto = u.protocol === 'wss:' ? 'https:' : u.protocol === 'ws:' ? 'http:' : u.protocol;
    if (proto !== 'http:' && proto !== 'https:') return '';
    return `${proto}//${u.host}/v1/audio/transcriptions`;
  } catch { return ''; }
}

// 실제로 쓸 STT 설정: stt.url 이 있으면 그대로, 없으면 서버 주소에서 만든다. 처음 모델을 불러오는 중일 수 있어서 시간 제한은 넉넉하게.
export function effectiveStt(config = {}) {
  const s = { ...(config.stt || {}) };
  if (!s.url) s.url = deriveSttUrl(config.serverUrl);
  if (!s.timeout) s.timeout = 120;
  return s;
}

export async function transcribe(blob, cfg = {}) {
  if (!cfg.url) throw new Error('음성 인식 주소를 알 수 없어요 (config.json 의 serverUrl 또는 stt.url 을 확인하세요)');
  // 앱에서는 메인 프로세스가 대신 보낸다: 렌더러에서 직접 fetch 하면 서버의 CORS 설정에 막혀 "Failed to fetch" 만 뜬다
  const api = typeof window !== 'undefined' && window.api;
  if (api && api.sttTranscribe) {
    const r = await api.sttTranscribe(cfg, await blob.arrayBuffer());
    if (!r || !r.ok) throw new Error((r && r.error) || 'STT 요청에 실패했어요');
    return String(r.text || '').trim();
  }
  const fd = new FormData();
  fd.append('file', blob, 'speech.wav');
  fd.append('model', cfg.model || 'whisper-1');
  if (cfg.language) fd.append('language', cfg.language);
  if (cfg.prompt) fd.append('prompt', cfg.prompt);
  fd.append('response_format', 'json');

  const headers = {};
  if (cfg.apiKey) headers.Authorization = `Bearer ${cfg.apiKey}`;
  const res = await fetch(cfg.url, { method: 'POST', body: fd, headers });
  if (!res.ok) throw new Error(`STT 서버 응답 ${res.status}`);
  const raw = await res.text();
  try { return String(JSON.parse(raw).text || '').trim(); } catch { return raw.trim(); }
}

// 두 문장이 얼마나 비슷한지 0~1 (띄어쓰기/문장부호/대소문자는 무시, 글자 단위 편집 거리). 목소리 등록 때 안내 문장대로 읽었는지 확인하는 데 쓴다.
export function textSimilarity(a, b) {
  const norm = (t) => Array.from(String(t || '').toLowerCase().replace(/[^\p{L}\p{N}]/gu, ''));
  const x = norm(a), y = norm(b);
  if (!x.length || !y.length) return 0;
  let prev = Array.from({ length: y.length + 1 }, (_, j) => j);
  for (let i = 1; i <= x.length; i++) {
    const cur = [i];
    for (let j = 1; j <= y.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x[i - 1] === y[j - 1] ? 0 : 1));
    prev = cur;
  }
  return 1 - prev[y.length] / Math.max(x.length, y.length);
}

// Whisper 계열은 조용한 구간에서 엉뚱한 문장을 지어내는 경우가 많다. 흔한 것만 걸러낸다.
const GHOST = /^(?:시청해\s*주셔서\s*감사합니다|구독과?\s*좋아요|자막\s*제공|감사합니다\.?$|MBC\s*뉴스|한글자막|thanks for watching)/i;
export const isGhostText = (t) => !t || t.replace(/[\s.,!?~…]/g, '').length < 2 || GHOST.test(t.trim());
