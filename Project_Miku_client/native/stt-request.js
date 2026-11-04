// 음성 -> 글자 요청 (OpenAI 호환 /v1/audio/transcriptions). 메인 프로세스에서 보낸다.
// 렌더러(브라우저)에서 fetch 하면 서버가 CORS 를 허용해야만 되고, 안 되면 원인을 알 수 없는 "Failed to fetch" 만 뜬다.
// 메인 프로세스(Node)에서 보내면 CORS 와 무관하고, 서버 응답의 실제 오류 내용도 그대로 알려줄 수 있다.
//
//   transcribeRequest(cfg, audioBuffer) -> '인식된 글자'    (실패하면 읽기 쉬운 메시지의 Error)
//   cfg: { url, model, language, prompt, apiKey, timeout(초, 기본 30) }

function describeErrorBody(raw) {
  try {
    const j = JSON.parse(raw);
    const m = (j.error && (j.error.message || j.error)) || j.message || j.detail;
    if (m) return typeof m === 'string' ? m : JSON.stringify(m);
  } catch { /* JSON 이 아님 */ }
  return String(raw || '').replace(/\s+/g, ' ').trim().slice(0, 160);
}

async function transcribeRequest(cfg, audio, fetchImpl = globalThis.fetch) {
  if (!cfg || !cfg.url) throw new Error('config.json 의 stt.url 이 비어 있어요');
  const fd = new FormData();
  fd.append('file', new Blob([audio], { type: 'audio/wav' }), 'speech.wav');
  fd.append('model', cfg.model || 'whisper-1');
  if (cfg.language) fd.append('language', cfg.language);
  if (cfg.prompt) fd.append('prompt', cfg.prompt);
  fd.append('response_format', 'json');

  const headers = {};
  if (cfg.apiKey) headers.Authorization = `Bearer ${cfg.apiKey}`;

  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), Math.max(Number(cfg.timeout) || 30, 0.05) * 1000);
  try {
    const res = await fetchImpl(cfg.url, { method: 'POST', body: fd, headers, signal: ctl.signal });
    const raw = await res.text();
    if (!res.ok) {
      const why = describeErrorBody(raw);
      throw new Error(`STT 서버 응답 ${res.status}${why ? `: ${why}` : ''}`);
    }
    try { return String(JSON.parse(raw).text || '').trim(); } catch { return raw.trim(); }
  } catch (e) {
    if (e && e.name === 'AbortError') throw new Error('STT 서버 응답 시간 초과');
    // Node 의 fetch 는 연결 실패를 그냥 "fetch failed" 로 던지고 진짜 이유는 cause 에 들어 있다 (IPv4/IPv6 를 둘 다 시도하면 cause.errors 에 묶여 온다)
    const cause = e && e.cause;
    const why = cause && (cause.code || (cause.errors && cause.errors[0] && cause.errors[0].code) || cause.message);
    if (why) throw new Error(`STT 서버에 연결하지 못했어요 (${why}). stt.url 과 서버가 켜져 있는지 확인하세요.`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { transcribeRequest, describeErrorBody };
