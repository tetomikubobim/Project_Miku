// 서버(LLM)에게 제목/가수 보정을 부탁한다: POST <서버>/lyrics/normalize  -> { is_music, candidates: [{title, artist}] }
// 메인 프로세스에서 보낸다 (렌더러에서 보내면 CORS 문제가 생길 수 있어서 STT 와 같은 방식).
function httpBaseOf(serverUrl) {
  try {
    const u = new URL(String(serverUrl || ''));
    const proto = u.protocol === 'wss:' ? 'https:' : u.protocol === 'ws:' ? 'http:' : u.protocol;
    return `${proto}//${u.host}`;
  } catch { return ''; }
}

async function requestNormalize(serverUrl, track, fetchImpl = globalThis.fetch, timeoutMs = 25000) {
  const base = httpBaseOf(serverUrl);
  if (!base) throw new Error('서버 주소(serverUrl)를 알 수 없어요');
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetchImpl(`${base}/lyrics/normalize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: track.title || '', artist: track.artist || '' }),
      signal: ctl.signal,
    });
    const raw = await res.text();
    if (!res.ok) {
      let why = raw;
      try { why = JSON.parse(raw).detail || raw; } catch { /* JSON 아님 */ }
      throw new Error(`서버 응답 ${res.status}: ${String(why).slice(0, 120)}`);
    }
    const j = JSON.parse(raw);
    return { isMusic: j.is_music !== false, candidates: Array.isArray(j.candidates) ? j.candidates : [] };
  } catch (e) {
    if (e && e.name === 'AbortError') throw new Error('서버 응답 시간 초과');
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { requestNormalize, httpBaseOf };
