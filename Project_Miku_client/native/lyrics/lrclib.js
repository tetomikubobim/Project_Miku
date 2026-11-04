// LRCLIB (https://lrclib.net) 조회: 무료, 키 없음, 줄 단위 싱크 가사. 알송이 안 될 때의 보조 소스.
//   search(title, artist, opts) -> [{ id, title, artist, album, playtime(초), lyricRaw }]  (싱크 가사가 있는 것만)
//   fetchLyric(item) -> item 그대로 (검색 결과에 가사가 이미 들어 있다)
//   opts: { fetchImpl, timeoutMs, retries, retryDelayMs }
// LRCLIB 은 개인이 운영하는 서버라 503/429/502 같은 일시 오류가 자주 난다 -> 짧게 두세 번 다시 시도하고,
// 같은 검색은 잠깐 기억해 둬서(성공한 것만) 후보 목록을 다시 열 때 서버를 또 두드리지 않는다.
const BASE = 'https://lrclib.net/api';
const USER_AGENT = 'ProjectMiku/0.1 (personal desktop assistant)';
const RETRY_STATUS = new Set([408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524]);
const CACHE_TTL_MS = 2 * 60 * 1000;
const CACHE_MAX = 60;

const memo = new Map();      // url -> { at, list }
const inflight = new Map();  // url -> Promise  (같은 검색이 동시에 여러 번 나가지 않게)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Retry-After 헤더(초 또는 날짜)를 ms 로. 없으면 0
function retryAfterMs(res) {
  const h = res && res.headers && typeof res.headers.get === 'function' ? res.headers.get('retry-after') : null;
  if (!h) return 0;
  const n = Number(h);
  if (Number.isFinite(n)) return Math.max(0, n * 1000);
  const t = Date.parse(h);
  return Number.isFinite(t) ? Math.max(0, t - Date.now()) : 0;
}

async function once(url, doFetch, timeoutMs) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    return await doFetch(url, { headers: { 'User-Agent': USER_AGENT, Accept: 'application/json' }, signal: ctl.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function request(url, opts) {
  const doFetch = opts.fetchImpl || globalThis.fetch;
  const timeoutMs = opts.timeoutMs || 8000;
  const retries = opts.retries == null ? 2 : opts.retries;
  const base = opts.retryDelayMs == null ? 500 : opts.retryDelayMs;
  let lastErr = null;
  for (let i = 0; i <= retries; i++) {
    if (i > 0) await sleep(Math.min(base * 2 ** (i - 1), 3000));
    let res;
    try {
      res = await once(url, doFetch, timeoutMs);
    } catch (e) {
      lastErr = e && e.name === 'AbortError' ? new Error('LRCLIB 응답 시간 초과') : e;   // 시간 초과/네트워크 끊김도 다시 시도
      continue;
    }
    if (res.status === 404) return [];
    if (res.ok) {
      const list = await res.json();
      return Array.isArray(list) ? list : [];
    }
    lastErr = new Error(`LRCLIB 응답 ${res.status}`);
    if (!RETRY_STATUS.has(res.status)) break;   // 400 같은 건 다시 해도 소용없다
    const wait = retryAfterMs(res);
    if (wait > 0 && wait <= 3000) await sleep(wait);
  }
  throw lastErr || new Error('LRCLIB 요청 실패');
}

async function search(title, artist, opts = {}) {
  const params = new URLSearchParams();
  if (artist) { params.set('track_name', title || ''); params.set('artist_name', artist); }
  else params.set('q', title || '');
  const url = `${BASE}/search?${params}`;
  const useCache = !opts.fetchImpl;   // 테스트(가짜 fetch)에서는 기억하지 않는다
  if (useCache) {
    const hit = memo.get(url);
    if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.list;
    if (inflight.has(url)) return inflight.get(url);
  }
  const job = request(url, opts).then((raw) => {
    const list = raw
      .filter((r) => r && r.syncedLyrics && !r.instrumental)
      .map((r) => ({
        id: String(r.id),
        title: String(r.trackName || r.name || ''),
        artist: String(r.artistName || ''),
        album: String(r.albumName || ''),
        playtime: Math.round(Number(r.duration) || 0),
        lyricRaw: String(r.syncedLyrics),
      }));
    if (useCache) {
      memo.set(url, { at: Date.now(), list });
      if (memo.size > CACHE_MAX) memo.delete(memo.keys().next().value);
    }
    return list;
  });
  if (!useCache) return job;
  inflight.set(url, job);
  try { return await job; } finally { inflight.delete(url); }
}

async function fetchLyric(item) { return item; }

const provider = { name: 'lrclib', search, fetchLyric };
module.exports = { provider, search, fetchLyric };
