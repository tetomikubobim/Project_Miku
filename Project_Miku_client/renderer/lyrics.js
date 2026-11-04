// 가사 표시 + 노래 입모양. 말풍선 자리에 2줄(윗줄=지금 줄, 아랫줄=다음 줄, 흐리게).
//   LyricsTimeline: 줄/재생 위치 계산 (순수 - DOM 없이 테스트 가능)
//   singLevel(view, t): 노래 부르는 입 벌림 0~1 (순수)
//   LyricsView: DOM 에 그리고, 말풍선이 떠 있으면 숨긴다
// 시간 단위는 초. 줄 t 는 곡 시작 기준.

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

// 한 줄이 '불리는' 시간: 글자 수에 비례 (가사엔 끝 시각이 없어서). 다음 줄 시작을 넘지 않는다.
export function singDuration(line, next) {
  const n = Array.from(line.text || '').filter((c) => !/\s/.test(c)).length;
  let d = clamp(n * 0.3, 1.0, 8);
  if (next) d = Math.min(d, Math.max(0.3, next.t - line.t));
  return d;
}
// 한 줄이 화면에 남는 시간: 불리는 시간보다 넉넉하게. 다음 줄이 바로 오면 그때까지.
export function showDuration(line, next) {
  const n = Array.from(line.text || '').filter((c) => !/\s/.test(c)).length;
  const d = Math.max(5, n * 0.45 + 2);
  return next ? Math.min(d, Math.max(0.3, next.t - line.t)) : d;
}

export class LyricsTimeline {
  constructor() {
    this.lines = [];
    this.offset = 0;      // 초. 양수 = 가사가 더 일찍 나온다
    this.anchor = null;   // { pos, at(ms), rate, playing }
  }

  setLines(lines, offset = 0) { this.lines = Array.isArray(lines) ? lines : []; this.offset = Number(offset) || 0; }
  setPlayback({ pos, at, rate, status }) { this.anchor = { pos: Number(pos) || 0, at: Number.isFinite(Number(at)) ? Number(at) : Date.now(), rate: Number(rate) > 0 ? Number(rate) : 1, playing: status === 'Playing' }; }
  get playing() { return !!(this.anchor && this.anchor.playing); }

  // 지금(nowMs) 곡의 재생 위치 + 싱크 보정
  timeAt(nowMs) {
    if (!this.anchor) return null;
    const a = this.anchor;
    const elapsed = a.playing ? Math.max(0, (nowMs - a.at) / 1000) * a.rate : 0;
    return a.pos + elapsed + this.offset;
  }

  // t 이하에서 가장 늦은 줄의 번호 (첫 줄 전이면 -1)
  indexAt(t) {
    let lo = 0; let hi = this.lines.length - 1; let ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (this.lines[mid].t <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  // 화면에 보일 상태. 가사가 없거나 끝난 뒤면 null.
  //   { t, idx, phase: 'intro'|'line'|'gap', line, next, nextText, sing: {line, dur} | null }
  view(nowMs) {
    const t = this.timeAt(nowMs);
    if (t == null || !this.lines.length) return null;
    const idx = this.indexAt(t);
    const lines = this.lines;
    const nextTextLine = (from) => { for (let i = from; i < lines.length; i++) if (lines[i].text) return lines[i]; return null; };
    if (idx < 0) {
      const first = nextTextLine(0);
      return { t, idx, phase: 'intro', line: null, next: first, sing: null };
    }
    const cur = lines[idx];
    const after = lines[idx + 1] || null;
    const next = nextTextLine(idx + 1);
    if (!cur.text) {
      // 쉼 표시 줄: 마지막이면 끝난 것
      if (!after) return null;
      return { t, idx, phase: 'gap', line: null, next, sing: null };
    }
    const shown = showDuration(cur, after);
    if (t - cur.t > shown) {
      if (!after && t - cur.t > shown + 4) return null;   // 마지막 줄이 한참 지났으면 사라진다
      return { t, idx, phase: 'gap', line: null, next, sing: null };
    }
    return { t, idx, phase: 'line', line: cur, next, sing: { line: cur, dur: singDuration(cur, after) } };
  }
}

// 노래 부르는 입 벌림. 음절 속도로 위아래로 흔들고, 줄 시작/끝은 부드럽게. 난수 없이 계산해서 같은 입력이면 같은 값.
// speed: 입 여닫는 속도 배율 (1=기본, 설정 > 가사 > 입 움직임 속도)
export function singLevel(view, playing, speed = 1) {
  if (!playing || !view || view.phase !== 'line' || !view.sing) return 0;
  const { line, dur } = view.sing;
  const el = view.t - line.t;
  if (el < 0 || el > dur) return 0;
  const chars = Array.from(line.text).filter((c) => !/\s/.test(c)).length;
  const rate = clamp((chars / dur) * 0.4, 1.0, 2.8) * clamp(Number(speed) || 1, 0.1, 3);   // 입 여닫는 속도(초당 번). 실제 음절 속도의 0.4배가 기본
  const env = Math.min(1, el / 0.12) * Math.min(1, (dur - el) / 0.25);  // 시작/끝 페이드
  const ph = 2 * Math.PI * rate * el + view.idx * 0.9;
  const wave = 0.5 + 0.5 * Math.sin(ph);                                // 0..1 (모음 열림)
  const wobble = 0.12 * Math.sin(ph * 0.37 + 1.1);                      // 박자가 기계적이지 않게
  return clamp(env * (0.15 + 0.6 * wave + wobble), 0, 1);
}

export class LyricsView {
  // el: #lyrics ( .l1 .l2 .badge 를 안에 만든다 ), isBubbleVisible: () => bool
  constructor(el, isBubbleVisible) {
    this.el = el;
    this.isBubbleVisible = isBubbleVisible || (() => false);
    this.timeline = new LyricsTimeline();
    this.trackId = null;
    this.title = '';
    this.opts = { enabled: true, layout: 'auto', fontSize: 0, line2Opacity: 0.45, background: true, lipSpeed: 1, hidePron: false, color1: '', color2: '', color3: '' };
    this.shownKey = null;
    this.flashUntil = 0;
    this.visible = false;
    this.lastView = null;
    this.preview = false;   // 편집 모드: 크기를 맞출 수 있게 견본을 항상 보여 준다

    this.l1 = document.createElement('div'); this.l1.className = 'l1';
    this.l2 = document.createElement('div'); this.l2.className = 'l2';
    this.l3 = document.createElement('div'); this.l3.className = 'l3';
    this.badge = document.createElement('div'); this.badge.className = 'badge';
    el.replaceChildren(this.badge, this.l1, this.l2, this.l3);
    this._applyStyle();
  }

  setOptions(o = {}) {
    this.opts = { ...this.opts, ...o };
    this._applyStyle();
    this.shownKey = null;
  }
  _applyStyle() {
    this.el.classList.toggle('nobg', this.opts.background === false);
    this.el.style.setProperty('--l2', String(clamp(Number(this.opts.line2Opacity) || 0.45, 0.05, 1)));
    for (const k of [1, 2, 3]) {   // 줄 색 (#rrggbb 만 받고, 비었거나 이상하면 기본 색)
      const c = String(this.opts[`color${k}`] || '');
      if (/^#[0-9a-fA-F]{6}$/.test(c)) this.el.style.setProperty(`--c${k}`, c); else this.el.style.removeProperty(`--c${k}`);
    }
  }

  // ----- 메인 프로세스에서 온 소식 -----
  onTrack(t) { this.trackId = t ? t.id : null; this.timeline.setLines([], 0); this.timeline.anchor = null; this.title = t ? t.title : ''; this.shownKey = null; }
  onLines(m) {
    if (!m || m.id !== this.trackId) return;
    this.timeline.setLines(m.lines || [], m.offset || 0);
    this.shownKey = null;
  }
  onPlayback(p) { if (p && p.id === this.trackId) this.timeline.setPlayback(p); }
  onOffset(m) { if (m && m.id === this.trackId) this.timeline.offset = Number(m.offset) || 0; }
  onIdle() { this.trackId = null; this.timeline.setLines([], 0); this.timeline.anchor = null; this.shownKey = null; }
  flash(text, ms = 1800) { this.badge.textContent = text; this.flashUntil = Date.now() + ms; this.shownKey = null; }

  // 가사가 켜져 있고 보여 줄 줄이 있는가 (말풍선이 가렸어도 true)
  isActive(nowMs = Date.now()) { return this.opts.enabled !== false && !!this.lastView; }

  // 노래가 재생 중이고 가사가 나오는 중인가 (먼저 말 걸기를 막는 데 쓴다)
  isPlaying() { return this.isActive() && this.timeline.playing; }

  // 지금 입을 얼마나 벌릴지 (말풍선이 가렸거나 가사가 꺼져 있으면 0)
  singLevel() { return this.visible ? singLevel(this.lastView, this.timeline.playing, this.opts.lipSpeed) : 0; }

  setPreview(on) { this.preview = !!on; this.shownKey = null; if (!on && this.visible) { this.el.style.display = 'none'; this.visible = false; } }

  // 80ms 마다 부른다
  update(nowMs = Date.now()) {
    if (this.preview) {
      if (!this.visible) { this.el.style.display = 'block'; this.visible = true; }
      this.badge.style.display = 'none';
      this.el.classList.toggle('full', true);
      this.l1.textContent = '가사 미리보기 (원문)'; this.l2.textContent = '발음 줄'; this.l3.textContent = '번역 줄';
      this.l3.style.display = 'block';
      this.shownKey = null;
      return;
    }
    const view = this.opts.enabled === false ? null : this.timeline.view(nowMs);
    this.lastView = view;
    const flashing = nowMs < this.flashUntil;
    const show = !!view && !this.isBubbleVisible();
    if (!show && !(flashing && !this.isBubbleVisible() && this.timeline.lines.length)) {
      if (this.visible) { this.el.style.display = 'none'; this.visible = false; this.shownKey = null; }
      return;
    }
    if (!this.visible) { this.el.style.display = 'block'; this.visible = true; this.shownKey = null; }
    this.badge.style.display = flashing ? 'block' : 'none';
    if (!flashing && this.badge.textContent) this.badge.textContent = '';

    // 3줄: 원문 / 같은 시각에 같이 든 줄(알송은 보통 발음, 번역 순서) 최대 2개. 같이 든 줄이 없으면 2줄: 원문 / 다음 줄
    // layout 'next' 면 항상 원문 / 다음 줄
    let a = '♪'; let b = ''; let c = ''; let full = false;   // full: 발음/번역 줄이라서 아랫줄도 흐리지 않게
    if (view) {
      if (view.phase === 'line') {
        a = view.line.text;
        let subs = view.line.subs || [];
        if (this.opts.hidePron && subs.length >= 2) subs = subs.slice(1);   // 발음 줄 숨기기: 같이 든 줄이 2개 이상일 때만 첫 줄(발음)을 뺀다
        if (this.opts.layout !== 'next' && subs.length) { b = subs[0]; c = subs[1] || ''; full = true; }
        else if (this.opts.layout !== 'next' && view.line.tr) { b = view.line.tr; full = true; }   // 번역이 없는 가사에 서버가 붙인 번역줄
        else if (view.next) b = view.next.text;
      } else if (view.next && (view.phase === 'intro' || view.next.t - view.t < 15)) b = view.next.text;
    }
    const key = `${view ? view.idx : 'x'}|${view ? view.phase : ''}|${a}|${b}|${c}|${full}`;
    if (key === this.shownKey) return;
    this.shownKey = key;
    this.l1.textContent = a;
    this.l2.textContent = b;
    this.l3.textContent = c;
    this.l3.style.display = c ? 'block' : 'none';
    this.el.classList.toggle('full', full);
    this.l1.classList.remove('pop'); void this.l1.offsetWidth; this.l1.classList.add('pop');
  }
}
