// 번역 없는 가사에 번역줄 붙이기. 번역은 서버(LLM)가 하고(POST <서버>/lyrics/translate), 결과는 캐시 항목의 tr 에 저장한다.
//   줄 객체: { t, text, subs, tr? }   tr 는 렌더러가 subs 가 비었을 때만 아랫줄로 쓴다 (subs 는 알송이 주는 발음/번역이라 섞지 않는다)
const { httpBaseOf } = require('./server-correct');

const MIN_LINES = 3;
const MAX_LINES = 300;
const HAVE_SUBS_RATIO = 0.3;   // 같은 시각에 든 줄이 이만큼 이상이면 가사에 번역(또는 발음)이 이미 있는 것으로 본다

// ----- 언어 설정: translateLang(도착 하나) / translateFrom(출발: all 또는 여러 개) -----
const LANGS = ['ko', 'en', 'ja', 'zh'];
const ALIASES = { 한국어: 'ko', 한글: 'ko', korean: 'ko', 영어: 'en', english: 'en', 일본어: 'ja', 일어: 'ja', japanese: 'ja', 중국어: 'zh', 중어: 'zh', chinese: 'zh', 전부: 'all', 모두: 'all', 전체: 'all' };
const toCode = (w) => { const k = String(w || '').trim().toLowerCase(); return ALIASES[k] || k; };
function parseTarget(v) { const c = toCode(v); return LANGS.includes(c) ? c : 'ko'; }
function parseFrom(v) {
  const list = String(v == null || v === '' ? 'all' : v).split(/[,\s/+>]+/).map(toCode).filter(Boolean);
  if (!list.length || list.includes('all')) return 'all';
  const ok = list.filter((c) => LANGS.includes(c));
  return ok.length ? [...new Set(ok)] : 'all';
}
function langOptions(cfg = {}) { return { to: parseTarget(cfg.translateLang), from: parseFrom(cfg.translateFrom) }; }

// 가사가 어느 나라 글자로 쓰였나: 'ko' | 'ja' | 'zh' | 'en'(로마자) | 'other'
function detectLang(lines) {
  const letters = lines.map((l) => l.text || '').join('').match(/\p{L}/gu) || [];
  if (!letters.length) return 'other';
  const n = (re) => letters.filter((c) => re.test(c)).length / letters.length;
  if (n(/[\u3040-\u30FF]/) >= 0.1) return 'ja';          // 가나가 조금이라도 섞이면 일본어
  if (n(/[\uAC00-\uD7A3]/) >= 0.3) return 'ko';
  if (n(/[\u4E00-\u9FFF]/) >= 0.3) return 'zh';
  if (n(/[A-Za-z\u00C0-\u024F]/) >= 0.5) return 'en';
  return 'other';
}

function hasProvidedSubs(lines) {
  const texts = lines.filter((l) => l.text);
  if (!texts.length) return false;
  return texts.filter((l) => l.subs && l.subs.length).length / texts.length >= HAVE_SUBS_RATIO;
}

// opts: { from: 'all' | ['ja', ...], to: 'ko' }.  도착 언어와 같은 언어로 쓰인 가사는 항상 건너뛴다. 'all' 은 감지하지 못한 언어(other)도 포함
function needsTranslation(lines, opts = {}) {
  if (!Array.isArray(lines)) return false;
  const { from = 'all', to = 'ko' } = opts;
  const n = lines.filter((l) => l.text).length;
  if (n < MIN_LINES || n > MAX_LINES) return false;
  if (hasProvidedSubs(lines)) return false;
  const d = detectLang(lines);
  if (d === to) return false;
  return from === 'all' || from.includes(d);
}

// 글자가 있는 줄만 골라 보낸다 (쉼 줄은 빼고, 돌아온 번역을 인덱스에 맞춰 되돌려 놓는다)
function pickLines(lines) {
  const idx = []; const texts = [];
  lines.forEach((l, i) => { if (l.text) { idx.push(i); texts.push(l.text); } });
  return { idx, texts };
}
function expandTranslation(idx, out, total) {
  const arr = new Array(total).fill('');
  idx.forEach((i, k) => { arr[i] = String(out[k] || '').trim(); });
  return arr;
}
function mergeTranslation(lines, tr) {
  if (!Array.isArray(tr) || tr.length !== lines.length) return lines;
  return lines.map((l, i) => (tr[i] ? { ...l, tr: tr[i] } : l));
}

async function requestTranslate(serverUrl, { title, artist, lang, texts, force }, fetchImpl = globalThis.fetch, timeoutMs = 120000) {
  const base = httpBaseOf(serverUrl);
  if (!base) throw new Error('서버 주소(serverUrl)를 알 수 없어요');
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetchImpl(`${base}/lyrics/translate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title || '', artist: artist || '', lang, lines: texts, force: !!force }),
      signal: ctl.signal,
    });
    const raw = await res.text();
    if (!res.ok) {
      let why = raw;
      try { why = JSON.parse(raw).detail || raw; } catch { /* JSON 아님 */ }
      throw new Error(`서버 응답 ${res.status}: ${String(why).slice(0, 120)}`);
    }
    const j = JSON.parse(raw);
    if (!Array.isArray(j.lines) || j.lines.length !== texts.length) throw new Error('번역 줄 수가 원문과 달라요');
    return { lines: j.lines, model: String(j.model || ''), cached: !!j.cached };
  } catch (e) {
    if (e && e.name === 'AbortError') throw new Error('서버 응답 시간 초과');
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { needsTranslation, pickLines, expandTranslation, mergeTranslation, requestTranslate, detectLang, langOptions, parseTarget, parseFrom, hasProvidedSubs, LANGS };
