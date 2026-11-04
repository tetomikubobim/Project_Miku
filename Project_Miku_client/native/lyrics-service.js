// 가사 서비스: 재생 중인 곡 감시 -> 가사 찾기 -> 렌더러로 전달. 메인 프로세스에서 돈다.
//
// 렌더러로 보내는 메시지 (send(channel, payload)):
//   lyrics:track     { id, title, artist, album, app, duration }              새 곡 (가사는 아직 찾는 중)
//   lyrics:lines     { id, lines|null, provider, offset, reason, info }       찾은 가사 (못 찾았으면 lines=null 과 reason)
//   lyrics:playback  { id, status, pos, rate, at }                            재생 위치 기준점 (at = Date.now(), pos 는 그 시각의 위치)
//   lyrics:idle      {}                                                       재생 중인 곡이 없어짐
//   lyrics:flash     { text }                                                 잠깐 띄울 안내 (싱크 보정 값 등)
// 렌더러가 부르는 것: snapshot() 현재 상태, command(action, arg)
const path = require('path');
const { MediaWatcher } = require('./media-watch');
const { LyricsResolver, scoreItem } = require('./lyrics/resolver');
const { LyricsCache } = require('./lyrics/cache');
const { requestNormalize } = require('./lyrics/server-correct');
const alsong = require('./lyrics/alsong');
const lrclib = require('./lyrics/lrclib');
const tr = require('./lyrics/translate');
const { cleanQuery } = require('./lyrics/titleClean');
const { parseLrc } = require('./lyrics/lrc');

const DEBOUNCE_MS = 100;    // 곡이 바뀐 직후엔 제목/가수가 따로따로 갱신되므로 잠깐 기다렸다가 한 번만 찾는다
const OFFSET_STEP = 0.5;
const MAX_CANDIDATES = 40;   // 가사 직접 고르기 목록에 보여 줄 최대 개수
const TRANSLATE_RETRY_MS = 10 * 60 * 1000;   // 번역이 실패한 곡을 다시 시도하기 전 쉬는 시간

function providersFor(sources) {
  const a = { ...alsong.provider };
  const l = { ...lrclib.provider, maxVariants: 3 };   // 보조 소스는 후보 몇 개만 시도 (요청이 너무 많아지지 않게)
  if (sources === 'alsong') return [a];
  if (sources === 'lrclib') return [l];
  return [a, l];
}
const trackId = (s) => `${s.app}|${s.title}|${s.artist}`;
const fmtOffset = (o) => `${o > 0 ? '+' : ''}${o.toFixed(1)}초`;

class LyricsService {
  constructor({ root, getConfig, send, log, createWatcher, platform = process.platform, fetchImpl } = {}) {
    this.getConfig = getConfig;
    this.send = send || (() => {});
    this.log = log || (() => {});
    this.platform = platform;
    this.watcher = createWatcher ? createWatcher() : new MediaWatcher();
    this.cache = new LyricsCache(path.join(root, 'lyrics-cache'));
    this.resolver = new LyricsResolver({
      getProviders: () => providersFor((this._cfg().sources) || 'alsong+lrclib'),
      cache: this.cache,
      correct: (track) => requestNormalize(this.getConfig().serverUrl, track, fetchImpl),
      log: (m) => this.log(m),
    });
    this.fetchImpl = fetchImpl;
    this.translating = new Set();   // 번역 요청 중인 곡 키
    this.trFailed = new Map();      // 키 -> 다시 시도해도 되는 시각(ms)
    this.watcher.on('state', (s) => this._onState(s));
    this.watcher.on('problem', (m) => this._onProblem(m));
    this.runningKey = null;
    this.track = null;       // 지금 곡 { id, title, artist, album, app, duration }
    this.playback = null;    // { id, status, pos, rate, at }
    this.result = null;      // resolve 결과
    this.offset = 0;
    this.token = 0;          // 곡이 바뀌거나 다시 찾을 때마다 증가 (늦게 도착한 결과를 버리기 위해)
    this.pending = null;
    this.timer = null;
    this.lastProblem = '';
    this.skew = null;
  }

  _cfg() { return (this.getConfig() || {}).lyrics || {}; }

  // 설정이 바뀔 때마다 부른다: 켜짐/꺼짐, 앱 목록이 바뀌면 감시를 다시 띄운다
  configure() {
    const c = this._cfg();
    const on = c.enabled !== false && this.platform === 'win32';
    if (!on) {
      if (this.runningKey != null) { this.watcher.stop(); this.runningKey = null; this._reset(); this.send('lyrics:idle', {}); this.log('[lyrics] 꺼짐'); }
      return;
    }
    const key = String(c.apps || '');
    if (this.runningKey === key) return;
    this.runningKey = key;
    this._reset();
    this.watcher.start({ apps: c.apps || '', intervalMs: 250 });
    this.log(`[lyrics] 곡 감시 시작${c.apps ? ` (앱: ${c.apps})` : ''}`);
  }

  stop() { this.watcher.stop(); this.runningKey = null; clearTimeout(this.timer); }

  _reset() {
    this.token++;
    clearTimeout(this.timer);
    this.pending = null;
    this.track = null; this.playback = null; this.result = null; this.offset = 0;
  }

  _onProblem(msg) {
    if (msg === this.lastProblem) return;   // 같은 오류가 반복돼도 한 번만 남긴다
    this.lastProblem = msg;
    this.log(`[lyrics] 감시 오류: ${msg}`);
  }

  _onState(s) {
    this.lastProblem = '';
    if (!s.found || !s.title) {
      if (this.track || this.pending) { this.log(`[lyrics] 재생 중인 곡 정보가 없어 가사를 숨겨요 (${s.found ? '제목 없음' : '세션 없음'})`); this._reset(); this.send('lyrics:idle', {}); }
      return;
    }
    const id = trackId(s);
    if (this.track && this.track.id === id) {
      if (!this.track.duration && s.duration) this.track.duration = s.duration;
      if (!this._acceptPosition(s, id)) return;   // 재생 위치가 엉뚱하게 처음으로 튄 보고는 무시하고 우리 시계를 그대로 쓴다
      this._sendPlayback(s, id);
      return;
    }
    // 새 곡: 잠깐 기다렸다가(제목/가수가 따로 갱신되는 경우) 마지막 값으로 시작
    this.pending = s;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { const p = this.pending; this.pending = null; if (p) this._startTrack(p); }, DEBOUNCE_MS);
  }

  // 재생 위치 보고를 믿어도 되는가.
  //  - 곡 길이를 아는 소스(스포티파이, 대부분의 유튜브): 위치를 정확히 알려 주니 그대로 따른다 (되감기, 반복 재생, 건너뛰기 모두).
  //  - 길이를 모르는(0초) 소스: 브라우저가 '위치 0 + 마지막 갱신 시각'만 알려서, 갱신 이벤트가 생길 때마다 위치가 0 근처로 돌아가 보일 수 있다.
  //    이때 맨 앞으로 돌아간 보고는, 가사가 아직 안 끝났으면(우리 시계가 마지막 가사 줄 전) 가짜로 보고 우리 시계 + 보정값으로 따라간다.
  //    가사가 끝난 뒤(반복 재생)나 일시정지/재생, 곡 바뀜은 진짜로 보고 그대로 받는다. 보정한 위치가 곡보다 한참 길어지면 보정을 버린다.
  // s.pos 를 보정해서 돌려준다 (받으면 true). 위치가 크게 튄 일은 모두 로그에 남긴다 (원인 확인용).
  _acceptPosition(s, id) {
    const p = this.playback;
    if (s.duration > 0 || !p || p.id !== id || p.status !== 'Playing' || s.status !== 'Playing') {
      if (p && p.id === id && p.status === 'Playing' && s.status === 'Playing') this._logJump(s, p, '위치를 아는 소스라 그대로 따라가요');
      this.skew = null;
      return true;
    }
    const lines = this.result && this.result.ok ? this.result.lines : null;
    const lastT = lines && lines.length ? lines[lines.length - 1].t : 0;
    const k = this.skew;
    if (k) {
      const rawEst = k.raw.pos + ((s.at - k.raw.at) / 1000) * p.rate;
      if (Math.abs(s.pos - rawEst) <= 1.5) {   // 가짜 시계가 그대로 이어지는 중
        if (lastT > 0 && s.pos + k.delta > lastT + 20) { this.skew = null; this.log('[lyrics] 보정한 위치가 곡보다 길어져서 보정을 버려요'); return true; }
        k.raw = { pos: s.pos, at: s.at };
        s.pos += k.delta;
        return true;
      }
      this.skew = null;                          // 이어지지 않음: 아래에서 새로 판단
    }
    const est = p.pos + ((s.at - p.at) / 1000) * p.rate;
    if (Math.abs(s.pos - est) <= 1.5) return true;
    const songOver = lastT > 0 && est >= lastT - 3;
    const bogus = s.pos < 3 && est > 15 && !songOver;
    this._logJump(s, p, bogus ? `곡 중간인데 처음으로 돌아간 보고라서 +${(est - s.pos).toFixed(1)}초 보정해서 따라가요` : '그대로 따라가요 (반복/되감기)', est);
    if (bogus) { this.skew = { delta: est - s.pos, raw: { pos: s.pos, at: s.at } }; s.pos = est; }
    return true;
  }

  _logJump(s, p, what, est) {
    const e = est != null ? est : p.pos + ((s.at - p.at) / 1000) * p.rate;
    if (Math.abs(s.pos - e) <= 1.5) return;
    this.log(`[lyrics] 재생 위치가 튀었어요: 예상 ${e.toFixed(1)}초 -> 보고 ${s.pos.toFixed(1)}초 (${s.app}, 길이 ${Math.round(s.duration)}초${s.rawPos != null ? `, 원본 위치 ${s.rawPos}초/갱신 ${s.age}초 전` : ''}) - ${what}`);
  }

  _sendPlayback(s, id) {
    this.playback = { id, status: s.status, pos: s.pos, rate: s.rate, at: s.at };
    this.send('lyrics:playback', this.playback);
  }

  // 디바운스 대기 중인 새 곡이 있으면 기다리지 않고 바로 시작한다 (가사 고르기 패널을 열 때 최신 곡 정보를 쓰려고)
  flushPending() {
    if (!this.pending) return false;
    clearTimeout(this.timer);
    const p = this.pending; this.pending = null;
    this._startTrack(p);
    return true;
  }

  _startTrack(s) {
    const id = trackId(s);
    this.skew = null;
    this.track = { id, title: s.title, artist: s.artist, album: s.album, app: s.app, duration: s.duration };
    this.result = null;
    this.offset = 0;
    this.send('lyrics:track', this.track);
    this._sendPlayback(s, id);
    this.log(`[lyrics] 곡: ${s.artist} - ${s.title} (${s.app}, ${Math.round(s.duration)}초)`);
    this._find({});
  }

  // opts: { force, direct(검색어 글자) }
  async _find(opts) {
    const my = ++this.token;
    const track = this.track;
    if (!track) return null;
    const c = this._cfg();
    const searchTrack = opts.query ? { title: `??${opts.query}`, artist: '', duration: track.duration } : track;
    let r;
    try {
      r = await this.resolver.resolve(searchTrack, {
        force: !!opts.force,
        direct: !!opts.query,
        llm: c.llmFix !== false,
        keyTrack: track,
        isStale: () => my !== this.token || !this.track,
      });
    } catch (e) {
      this.log(`[lyrics] 찾기 실패: ${e.message}`);
      r = { ok: false, reason: 'error', error: e.message };
    }
    if (my !== this.token) return null;   // 그 사이 곡이 바뀜
    this.result = r;
    if (r.ok) {
      this._deliver(track, r, my);
    } else {
      this.log(`[lyrics] 가사 없음 (${r.reason})`);
      this.send('lyrics:lines', { id: track.id, lines: null, reason: r.reason });
    }
    return r;
  }

  // 찾은(또는 사용자가 고른) 가사를 화면으로 보낸다: 저장된 싱크 보정/번역을 붙이고, 번역이 없으면 번역을 시작
  _deliver(track, r, my) {
    this.offset = this.cache.getOffset(r.key, track.duration);
    const saved = this._savedTranslation(r.key);
    if (saved) r.lines = tr.mergeTranslation(r.lines, saved.lines);
    this.send('lyrics:lines', { id: track.id, lines: r.lines, provider: r.provider, offset: this.offset, info: r.info });
    if (!saved && this._translateOn() && (this.trFailed.get(r.key) || 0) <= Date.now() && tr.needsTranslation(r.lines, this._langOpts())) this._translate(track, r, my, false);
  }

  // ---------- 가사 직접 고르기 (설정이 아니라 필요할 때만 여는 패널) ----------
  // 지금 곡(또는 직접 친 검색어)으로 가사 소스들을 검색해 후보 목록을 돌려준다. 자동으로 고르는 것과 달리 걸러내지 않고 점수순으로 모두 보여 준다.
  async candidates(query) {
    this.flushPending();   // 곡이 막 바뀌어서 디바운스를 기다리는 중이면 기다리지 않고 지금 곡으로 바로 시작
    const track = this.track;
    if (this.platform !== 'win32') return { ok: false, message: '가사는 Windows 에서만 지원돼요.' };
    if (!track) return { ok: false, message: '재생 중인 곡이 없어요.' };
    const typed = String(query || '').trim();
    const q = typed ? cleanQuery(`??${typed}`, '') : cleanQuery(track.title, track.artist);
    const variants = q.variants.slice(0, 3);
    const found = new Map();
    const failed = new Map();   // 소스 이름 -> 마지막 오류 (소스별로 변형 검색이 다 실패했을 때만)
    const providers = this.resolver.getProviders();
    // 소스끼리도, 검색어 변형끼리도 동시에 보낸다 (예전엔 변형을 하나씩 기다려서 서버가 느리면 목록이 한참 늦게 떴다)
    await Promise.all(providers.map(async (p) => {
      const rs = await Promise.allSettled(variants.map((v) => p.search(v.title, v.artist, { timeoutMs: 8000 })));
      let okCount = 0; let lastErr = null;
      for (const r of rs) {
        if (r.status === 'fulfilled') {
          okCount++;
          for (const item of r.value) { const key = `${p.name}:${item.id}`; if (!found.has(key)) found.set(key, { key, provider: p, item }); }
        } else lastErr = r.reason;
      }
      if (!okCount && lastErr) failed.set(p.name, (lastErr && lastErr.message) || String(lastErr));
    }));
    const errors = [...failed].map(([n, m]) => `${n}: ${m}`);
    if (!this.track || this.track.id !== track.id) return { ok: false, stale: true, track: { title: track.title, artist: track.artist, duration: Math.round(track.duration || 0) }, message: '곡이 바뀌었어요.' };   // 검색하는 사이 곡이 바뀌었다: 후보 목록(this.cand)을 낡은 곡 것으로 덮어쓰지 않는다
    const ref = variants[0] || { title: track.title, artist: track.artist };
    const ranked = [...found.values()].map((c) => ({ ...c, ...scoreItem(c.item, { title: ref.title, artist: ref.artist || ref.ref || '' }) }))
      .sort((a, b) => (b.ok - a.ok) || (b.score - a.score)).slice(0, MAX_CANDIDATES);
    this.cand = new Map(ranked.map((c) => [c.key, c]));
    this.candTrackId = track.id;
    const cur = this.cache.get(this.resolver.keyOf(track));
    const curKey = cur && cur.provider && cur.info && cur.info.id ? `${cur.provider}:${cur.info.id}` : '';
    const items = ranked.map((c) => ({ key: c.key, provider: c.provider.name, title: c.item.title, artist: c.item.artist, album: c.item.album || '', playtime: c.item.playtime || 0, likely: !!c.ok, current: c.key === curKey }));
    this.log(`[lyrics] 가사 후보 ${items.length}개${errors.length ? ` (오류: ${errors.join(' / ')})` : ''}`);
    const track0 = { title: track.title, artist: track.artist, duration: Math.round(track.duration || 0) };
    if (!items.length && errors.length) {
      const all = failed.size >= providers.length;   // 모든 소스가 실패 = 서버 문제, 일부만 실패 = 나머지 소스에는 결과가 없는 것
      return { ok: false, track: track0, message: all ? `가사 서버에 연결하지 못했어요 (${errors.join(' / ')}). 잠시 뒤 다시 검색해 주세요.` : `결과가 없어요. (${errors.join(' / ')} - 이 소스는 지금 응답하지 않아요. 잠시 뒤 다시 검색해 보세요)` };
    }
    return { ok: true, track: track0, query: typed, items, chosen: !!(cur && cur.chosen), warning: errors.length ? `일부 소스가 응답하지 않아 결과가 적을 수 있어요 (${errors.join(' / ')})` : '' };
  }

  _candidate(key) {
    if (!this.track || this.candTrackId !== this.track.id) return null;   // 곡이 바뀌었으면 목록은 낡았다
    return (this.cand && this.cand.get(key)) || null;
  }

  async _candDetail(c) {
    if (c.detail) return c.detail;
    const d = await c.provider.fetchLyric(c.item, {});
    if (!d) throw new Error('가사를 받지 못했어요');
    const parsed = parseLrc(d.lyricRaw);
    if (!parsed) throw new Error('싱크가 있는 가사가 아니에요');
    c.detail = { d, parsed };
    return c.detail;
  }

  // 후보 하나의 첫 줄들과 길이를 보여 준다 (곡 길이와 마지막 가사 시각을 비교해서 다른 버전을 알아보게)
  async preview(key) {
    const c = this._candidate(key);
    if (!c) return { ok: false, message: '곡이 바뀌었거나 목록이 오래됐어요. 다시 검색해 주세요.' };
    try {
      const { d, parsed } = await this._candDetail(c);
      return {
        ok: true,
        lines: parsed.lines.filter((l) => l.text).slice(0, 6).map((l) => l.text),
        count: parsed.lines.length,
        lastT: parsed.lines[parsed.lines.length - 1].t,
        playtime: d.playtime || c.item.playtime || 0,
        duration: Math.round((this.track && this.track.duration) || 0),
      };
    } catch (e) { return { ok: false, message: e.message }; }
  }

  // 후보를 지금 곡의 가사로 저장하고 바로 적용한다 (이후 자동 검색이 덮어쓰지 않는다. 되돌리려면 command('auto'))
  async choose(key) {
    const c = this._candidate(key);
    if (!c) return { ok: false, message: '곡이 바뀌었거나 목록이 오래됐어요. 다시 검색해 주세요.' };
    try {
      const { d, parsed } = await this._candDetail(c);
      const track = this.track;
      const ckey = this.resolver.keyOf(track);
      const info = { title: d.title || c.item.title, artist: d.artist || c.item.artist, album: d.album || c.item.album || '', id: String(d.id || c.item.id), query: '(사용자 선택)' };
      this.cache.set(ckey, { provider: c.provider.name, info, lines: parsed.lines, duration: track.duration || 0, chosen: true });
      this.resolver.neg.delete(ckey);
      const r = { ok: true, key: ckey, lines: parsed.lines, provider: c.provider.name, info, from: 'chosen', chosen: true };
      const my = ++this.token;
      this.result = r;
      this._deliver(track, r, my);
      this.log(`[lyrics] 사용자가 고름: ${c.provider.name} ${info.artist} - ${info.title}`);
      return { ok: true, message: `🎵 이 가사로 정했어요: ${info.artist} - ${info.title}` };
    } catch (e) { return { ok: false, message: `가사를 받지 못했어요: ${e.message}` }; }
  }

  _translateOn() { return this._cfg().translate !== false; }
  _lang() { return tr.parseTarget(this._cfg().translateLang); }
  _langOpts() { return tr.langOptions(this._cfg()); }
  _savedTranslation(key) {
    const e = this.cache.get(key);
    return e && e.tr && e.tr.lang === this._lang() && Array.isArray(e.tr.lines) && e.tr.lines.length === e.lines.length ? e.tr : null;
  }

  // 원문만 있는 가사를 서버(LLM)로 번역해 캐시에 저장하고, 끝나면 번역줄이 붙은 가사를 다시 보낸다 (그 전까지는 원문만 나온다)
  // 돌려주는 값: { ok, message }
  async _translate(track, r, my, force) {
    const key = r.key;
    if (this.translating.has(key)) return { ok: false, message: '이미 번역하는 중이에요.' };
    const lyricId = (this.cache.get(key) || {}).lyricId;
    const sel = tr.pickLines(r.lines);
    if (!sel.texts.length || !lyricId) return { ok: false, message: '번역할 가사가 없어요.' };
    const lang = this._lang();
    this.translating.add(key);
    try {
      const res = await tr.requestTranslate(this.getConfig().serverUrl, { title: track.title, artist: track.artist, lang, texts: sel.texts, force }, this.fetchImpl || globalThis.fetch);
      const lines = tr.expandTranslation(sel.idx, res.lines, r.lines.length);
      const saved = this.cache.patch(key, { tr: { lang, model: res.model, at: Date.now(), lines } }, lyricId);
      this.trFailed.delete(key);
      this.log(`[lyrics] 번역 ${res.cached ? '(서버 캐시) ' : ''}${saved ? '저장' : '완료 (가사가 바뀌어서 저장은 건너뜀)'}: ${sel.texts.length}줄`);
      if (saved && my === this.token && this.result === r) {
        r.lines = tr.mergeTranslation(r.lines, lines);
        this.send('lyrics:lines', { id: track.id, lines: r.lines, provider: r.provider, offset: this.offset, info: r.info });
      }
      return { ok: true, message: `🎵 번역했어요 (${sel.texts.length}줄)` };
    } catch (e) {
      this.trFailed.set(key, Date.now() + TRANSLATE_RETRY_MS);
      this.log(`[lyrics] 번역 실패: ${e.message}`);
      return { ok: false, message: `🎵 번역 실패: ${e.message}` };
    } finally {
      this.translating.delete(key);
    }
  }

  // 곡에 대해 묻는 말에 AI 가 쓸 지금 곡 정보 (곡이 없으면 null). 가사를 찾았으면 가사 DB 의 제목/가수도 참고로 같이.
  nowPlaying() {
    const t = this.track;
    if (!t) return null;
    const info = this.result && this.result.ok && this.result.info ? this.result.info : {};
    return {
      title: t.title, artist: t.artist || '', album: t.album || '', app: t.app || '', duration: Math.round(t.duration || 0),
      status: this.playback ? this.playback.status : '',
      dbTitle: info.title || '', dbArtist: info.artist || '', dbAlbum: info.album || '',
    };
  }

  snapshot() {
    this.flushPending();
    return {
      enabled: this.runningKey != null,
      track: this.track,
      playback: this.playback,
      lyrics: this.result && this.result.ok ? { id: this.track && this.track.id, lines: this.result.lines, provider: this.result.provider, offset: this.offset, info: this.result.info } : null,
    };
  }

  // action: 'status' | 'refresh' | 'search'(arg 글자) | 'offset'(arg 초만큼 더함) | 'offset-reset'
  async command(action, arg) {
    if (this.platform !== 'win32') return { ok: false, message: '가사는 Windows 에서만 지원돼요.' };
    if (this.runningKey == null) return { ok: false, message: '가사 기능이 꺼져 있어요. (설정 > 가사)' };
    const t = this.track;
    if (action === 'status') {
      if (!t) return { ok: true, message: '🎵 재생 중인 곡이 없어요.' };
      const r = this.result;
      const head = `🎵 ${[t.artist, t.title].filter(Boolean).join(' - ')}`;
      if (!r) return { ok: true, message: `${head}\n가사를 찾는 중이에요…` };
      if (!r.ok) return { ok: true, message: `${head}\n가사를 못 찾았어요 (${{ none: '검색 결과 없음', 'not-music': '음악이 아닌 것 같아요', error: '가사 서버 오류' }[r.reason] || r.reason}).\n/가사 가수 - 제목  으로 직접 찾아볼 수 있어요.` };
      const st = this._savedTranslation(r.key);
      const trLine = st ? `\n번역: 저장됨 (${st.model ? `${st.model} 기계번역` : '기계번역'})` : this.translating.has(r.key) ? '\n번역: 하는 중…' : '';
      return { ok: true, message: `${head}\n출처: ${r.provider}${r.chosen ? ' (사용자 선택)' : ''}${r.info && r.info.title ? ` (${r.info.artist} - ${r.info.title})` : ''}\n싱크 보정: ${fmtOffset(this.offset)}${trLine}` };
    }
    if (!t) return { ok: false, message: '재생 중인 곡이 없어요.' };
    if (action === 'refresh' || action === 'search') {
      this.send('lyrics:lines', { id: t.id, lines: null, reason: 'searching' });
      const r = await this._find(action === 'search' ? { query: String(arg || '').trim(), force: true } : { force: true });
      if (!r) return { ok: false, message: '곡이 바뀌어서 중단했어요.' };
      return r.ok
        ? { ok: true, message: `🎵 찾았어요: ${r.info.artist || ''} - ${r.info.title || ''} (${r.provider})` }
        : { ok: false, message: '🎵 가사를 못 찾았어요.' };
    }
    if (action === 'auto') {   // 직접 고른 가사/저장된 결과를 버리고 앱이 알아서 다시 고르게
      const ck = this.resolver.keyOf(t);
      this.cache.remove(ck); this.resolver.neg.delete(ck);   // 저장된 결과(직접 고른 것 포함)를 버린다
      this.send('lyrics:lines', { id: t.id, lines: null, reason: 'searching' });
      const r = await this._find({});
      if (!r) return { ok: false, message: '곡이 바뀌어서 중단했어요.' };
      return r.ok ? { ok: true, message: `🎵 자동으로 골랐어요: ${r.info.artist || ''} - ${r.info.title || ''} (${r.provider})` } : { ok: false, message: '🎵 가사를 못 찾았어요.' };
    }
    if (action === 'translate') {
      if (!this.result || !this.result.ok) return { ok: false, message: '가사가 없어서 번역할 수 없어요.' };
      if (tr.hasProvidedSubs(this.result.lines) && !this.result.lines.some((l) => l.tr)) return { ok: false, message: '이 가사에는 번역(또는 발음)이 이미 들어 있어요.' };
      if (tr.detectLang(this.result.lines) === this._lang()) return { ok: false, message: '이미 번역할 언어로 된 가사예요.' };   // 직접 시키면 출발 언어 설정과 상관없이 번역한다
      this.send('lyrics:flash', { text: '번역 중…' });
      return this._translate(t, this.result, this.token, true);
    }
    if (action === 'offset' || action === 'offset-reset') {
      if (!this.result || !this.result.ok) return { ok: false, message: '가사가 없어서 싱크를 맞출 수 없어요.' };
      const d = Number(arg);
      if (action === 'offset' && !Number.isFinite(d)) return { ok: false, message: '숫자를 알려 주세요. 예: /가사 +0.5' };
      this.offset = action === 'offset-reset' ? 0 : Math.max(-30, Math.min(30, Math.round((this.offset + d) * 10) / 10));
      this.cache.setOffset(this.result.key, this.offset, t.duration);
      this.send('lyrics:offset', { id: t.id, offset: this.offset });
      this.send('lyrics:flash', { text: `싱크 ${fmtOffset(this.offset)}` });
      return { ok: true, message: `🎵 싱크 보정 ${fmtOffset(this.offset)} (양수 = 가사가 더 일찍 나와요)` };
    }
    return { ok: false, message: `알 수 없는 가사 명령: ${action}` };
  }

  nudge(dir) { return this.command('offset', dir * OFFSET_STEP); }
  resetOffset() { return this.command('offset-reset'); }
}

module.exports = { LyricsService, providersFor, trackId, OFFSET_STEP };
