// 곡 정보 -> 싱크 가사. 순서: 캐시 -> (정리한 검색어 x 가사 소스) -> 못 찾으면 LLM 보정 후 다시.
//   new LyricsResolver({ getProviders(), cache, correct(track) -> {isMusic, candidates}, log })
//   resolve(track, { direct, force, llm, isStale, keyTrack }) ->   keyTrack: 캐시 키로 쓸 곡 (직접 검색할 때 지금 재생 곡)
//     { ok: true, key, lines, provider, info, from }  |  { ok: false, reason: 'none' | 'not-music' | 'stale' | 'error', error? }
//   track: { title, artist, duration(초, 0 이면 모름) }
const { cleanQuery, keyOf } = require('./titleClean');
const { parseLrc } = require('./lrc');

// 가수 정보가 믿을 만한 재생기(스포티파이 같은 음악 앱)인가. 브라우저는 채널명이 가수 칸에 들어와서 엄격하게 비교하면 맞는 곡도 놓친다
const BROWSER_RE = /chrome|msedge|edge|firefox|brave|opera|vivaldi|whale|browser|iexplore|safari/i;

const NEG_TTL_MS = 5 * 60 * 1000;        // 못 찾은 곡을 다시 찾기 전 쉬는 시간
const NOT_MUSIC_TTL_MS = 60 * 60 * 1000;
const MAX_DETAIL_FETCH = 3;              // 후보 중 상세(가사 본문)를 받아 볼 개수

// 비교용 정규화: 호환 문자 통일, 소문자, 공백/기호 제거
const norm = (s) => String(s || '').normalize('NFKC').toLowerCase().replace(/[\s\p{P}\p{S}_]+/gu, '');
function bigrams(s) { const out = new Map(); for (let i = 0; i < s.length - 1; i++) { const g = s.slice(i, i + 2); out.set(g, (out.get(g) || 0) + 1); } return out; }
function dice(a, b) {
  if (!a || !b) return 0;
  if (a === b) return 1;
  if (a.length < 2 || b.length < 2) return 0;
  const A = bigrams(a); const B = bigrams(b);
  let inter = 0;
  for (const [g, n] of A) inter += Math.min(n, B.get(g) || 0);
  return (2 * inter) / (a.length - 1 + b.length - 1);
}

// 검색 결과 한 줄이 우리가 찾는 곡과 얼마나 비슷한지. ok=false 면 다른 곡일 가능성이 커서 쓰지 않는다.
//   q.artist 가 비어 있어도(제목만으로 검색) q.ref(원래 가수)가 있으면 그 가수와 비교한다.
//   q.strict: 가수를 아는데 다르면 제목이 완전히 같아도 쓰지 않는다 (제목만 같은 다른 가수의 곡을 걸러낸다)
function scoreItem(item, q) {
  const it = norm(item.title); const iq = norm(q.title);
  const ia = norm(item.artist); const qa = norm(q.artist || q.ref);
  let title = 0; let exactTitle = false;
  if (it && it === iq) { title = 50; exactTitle = true; }
  else if (it && iq && (it.includes(iq) || iq.includes(it)) && Math.min(it.length, iq.length) >= 2) title = 25;
  else { const d = dice(it, iq); if (d >= 0.7) title = 20 * d; }
  let artist = 0; let artistKnown = !!qa;
  if (qa && ia) {
    if (ia === qa) artist = 30;
    else if (ia.includes(qa) || qa.includes(ia)) artist = 15;
    else { const d = dice(ia, qa); if (d >= 0.5) artist = 10 * d; }
  }
  const artistMismatch = artistKnown && ia && artist === 0;
  const ok = title > 0 && (!artistMismatch || (exactTitle && !q.strict));
  return { score: title + artist + (artistMismatch ? -10 : 0), ok };
}

// 후보 -> 가장 맞는 가사. 길이(playtime)와 마지막 가사 시각으로 엉뚱한 버전을 거른다.
async function pickBest(provider, list, query, duration, opts, log) {
  const all = list.map((item) => ({ item, ...scoreItem(item, query) }));
  const scored = all.filter((x) => x.ok).sort((a, b) => b.score - a.score);
  if (query.strict) {   // 제목은 같은데 가수가 달라서 버린 것을 로그로 남긴다
    const skipped = all.filter((x) => !x.ok && norm(x.item.title) === norm(query.title)).slice(0, 2);
    for (const x of skipped) log(`[lyrics] 가수가 달라서 건너뜀: ${x.item.artist} - ${x.item.title} (찾는 곡: ${query.artist || query.ref} - ${query.title})`);
  }
  if (!scored.length) return null;
  const top = scored[0].score;
  const finalists = scored.filter((x) => x.score >= top - 20).slice(0, MAX_DETAIL_FETCH);
  let best = null;
  for (const f of finalists) {
    let detail;
    try { detail = await provider.fetchLyric(f.item, opts); } catch (e) { log(`[lyrics] ${provider.name} 상세 실패: ${e.message}`); continue; }
    if (!detail) continue;
    const parsed = parseLrc(detail.lyricRaw);
    if (!parsed) continue;
    const lastT = parsed.lines[parsed.lines.length - 1].t;
    if (duration > 0 && lastT > duration + 30) continue;                 // 곡보다 훨씬 긴 가사: 다른 버전
    let final = f.score;
    const pt = detail.playtime || f.item.playtime || 0;
    if (duration > 0 && pt > 0) final -= Math.min(Math.abs(pt - duration), 30) * 0.8;
    if (!best || final > best.final) best = { final, detail, parsed, matched: f.item };
  }
  return best;
}

class LyricsResolver {
  constructor({ getProviders, cache, correct, log } = {}) {
    this.getProviders = getProviders || (() => []);
    this.cache = cache || null;
    this.correct = correct || null;
    this.log = log || (() => {});
    this.neg = new Map();   // key -> 풀리는 시각(ms)
  }

  keyOf(track) { return keyOf(track.title, track.artist); }

  async _search(variants, track, isStale, state, strict = false) {
    for (const p of this.getProviders()) {
      const vs = variants.slice(0, p.maxVariants || variants.length);
      for (const v of vs) {
        if (isStale()) return { stale: true };
        let list;
        try { list = await p.search(v.title, v.artist, {}); }
        catch (e) { state.errors++; this.log(`[lyrics] ${p.name} 검색 실패 (${v.artist} - ${v.title}): ${e.message}`); continue; }
        if (!list.length) continue;
        const best = await pickBest(p, list, { ...v, strict }, track.duration || 0, {}, this.log);
        if (best) return { hit: { provider: p.name, best, query: v } };
      }
    }
    return null;
  }

  async resolve(track, { direct = false, force = false, llm = true, isStale = () => false, keyTrack = null } = {}) {
    const key = this.keyOf(keyTrack || track);   // 직접 검색한 결과도 지금 곡의 키로 저장해서 다음부터 그대로 쓴다
    if (!force) {
      const c = this.cache && this.cache.get(key);
      if (c) return { ok: true, key, lines: c.lines, provider: c.provider || 'cache', info: c.info || {}, from: 'cache', chosen: !!c.chosen };
      const until = this.neg.get(key);
      if (until && until > Date.now()) return { ok: false, key, reason: this.negReason?.get(key) || 'none' };
    }
    const state = { errors: 0 };
    const q = cleanQuery(track.title, track.artist);
    const isDirect = direct || q.direct;

    const strict = !isDirect && !BROWSER_RE.test(String(track.app || ''));   // 음악 앱이 알려 준 가수는 믿는다: 가수가 다르면 제목이 같아도 건너뛴다
    let r = await this._search(q.variants, track, isStale, state, strict);
    if (r && r.stale) return { ok: false, key, reason: 'stale' };

    if (!r && llm && !isDirect && this.correct) {
      try {
        const fix = await this.correct(track);
        if (isStale()) return { ok: false, key, reason: 'stale' };
        if (!fix.isMusic) { this._neg(key, 'not-music', NOT_MUSIC_TTL_MS); return { ok: false, key, reason: 'not-music' }; }
        const extra = [];
        for (const c of fix.candidates) { extra.push({ title: c.title, artist: c.artist || '', ref: c.artist || '' }); if (c.artist) extra.push({ title: c.title, artist: '', ref: c.artist }); }
        this.log(`[lyrics] LLM 보정 후보: ${fix.candidates.map((c) => `${c.artist} - ${c.title}`).join(' | ')}`);
        r = await this._search(extra, track, isStale, state, strict);
        if (r && r.stale) return { ok: false, key, reason: 'stale' };
      } catch (e) {
        this.log(`[lyrics] LLM 보정 실패: ${e.message}`);
      }
    }

    if (!r || !r.hit) {
      if (state.errors) return { ok: false, key, reason: 'error' };   // 네트워크 문제일 수 있으니 오래 기억하지 않는다
      this._neg(key, 'none', NEG_TTL_MS);
      return { ok: false, key, reason: 'none' };
    }
    const { provider, best, query } = r.hit;
    const info = { title: best.detail.title || best.matched.title, artist: best.detail.artist || best.matched.artist, album: best.detail.album || '', id: String(best.detail.id || best.matched.id || ''), query: `${query.artist} - ${query.title}` };
    this.neg.delete(key);
    if (this.cache) this.cache.set(key, { provider, info, lines: best.parsed.lines, duration: (keyTrack || track).duration || 0 });
    this.log(`[lyrics] ${provider} 에서 찾음: ${info.artist} - ${info.title} (검색어: ${info.query})`);
    return { ok: true, key, lines: best.parsed.lines, provider, info, from: 'net' };
  }

  _neg(key, reason, ttl) {
    this.neg.set(key, Date.now() + ttl);
    (this.negReason || (this.negReason = new Map())).set(key, reason);
  }

  forget(track) {
    const key = this.keyOf(track);
    this.neg.delete(key);
    if (this.cache) this.cache.remove(key);
  }
}

module.exports = { LyricsResolver, scoreItem, norm, dice };
