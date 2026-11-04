// 가사 직접 고르기 패널: 평소엔 앱이 알아서 고르지만, 알송에 같은 곡이 여러 개 있거나 엉뚱한 가사가 잡혔을 때 사용자가 후보 중에서 고른다.
// 열기: /가사 선택, 트레이 메뉴, (설정 > 단축키에서 정한) 단축키.
//   handlers: candidates(검색어) -> { ok, track:{title,artist,duration}, items:[{key,provider,title,artist,album,playtime,likely,current}], chosen, message }
//             preview(key) -> { ok, lines:[글], count, lastT, playtime, duration, message }
//             current() -> { title, artist, duration } | null  (선택) 열자마자 지금 곡을 바로 보여 주려고. 검색이 끝나기 전에도 곡 줄이 채워진다
//             choose(key) -> { ok, message },  auto() -> { ok, message },  onDone({ ok, message }) 선택/되돌리기가 끝나면 (안내 띄우기용)
const PROVIDER_NAME = { alsong: '알송', lrclib: 'LRCLIB' };

export function fmtTime(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// 고른 가사가 지금 곡과 같은 버전 같은지 알려 주는 한 줄. playtime: 가사 DB 가 적어 둔 곡 길이, lastT: 마지막 가사 줄 시각, duration: 지금 곡 길이
export function lengthNote({ playtime = 0, lastT = 0, duration = 0 } = {}) {
  if (!(duration > 0)) return '';
  if (playtime > 0) {
    const d = Math.round(Math.abs(playtime - duration));
    return d <= 3 ? `곡 길이와 거의 같아요 (${fmtTime(playtime)})` : `⚠ 곡 길이와 ${d}초 달라요 (${fmtTime(playtime)} / 지금 ${fmtTime(duration)}) — 다른 버전일 수 있어요`;
  }
  if (lastT > duration + 10) return `⚠ 마지막 가사(${fmtTime(lastT)})가 곡 길이(${fmtTime(duration)})보다 뒤예요 — 다른 버전일 수 있어요`;
  return `마지막 가사 ${fmtTime(lastT)} / 지금 곡 ${fmtTime(duration)}`;
}

export class LyricsPicker {
  constructor(root, handlers = {}) {
    this.root = root;
    this.doc = root.ownerDocument;
    this.h = handlers;
    this.isOpen = false;
    this.seq = 0;                 // 늦게 도착한 응답을 버리려고 요청마다 올린다
    this.items = [];
    this.selected = '';
    this.previews = new Map();    // key -> preview 결과
    this.track = null;
    this.chosen = false;          // 지금 곡이 직접 고른 가사를 쓰는 중인가
    this.msg = '';
    this.busy = false;
    this._build();
  }

  _el(tag, cls, text) {
    const e = this.doc.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // 같은 글이면 건드리지 않는다 (다시 그릴 때마다 바꾸면 사용자가 드래그해 둔 선택이 풀려서 복사할 수 없다)
  _setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
  }

  // 글자를 드래그해서 선택한 상태인가 (그때의 클릭은 후보를 고르는 게 아니라 복사하려는 것)
  _hasSelection() {
    const w = this.doc.defaultView;
    const sel = w && w.getSelection ? w.getSelection() : null;
    return !!sel && !sel.isCollapsed && String(sel).length > 0;
  }

  _build() {
    this.titleEl = this._el('div', 'lp-title', '가사 직접 고르기');
    const x = this._el('button', 'lp-x', '✕'); x.title = '닫기';
    x.addEventListener('click', () => this.close());
    const head = this._el('div', 'lp-head'); head.append(this.titleEl, x);
    this.trackEl = this._el('div', 'lp-track');
    this.input = this._el('input', 'lp-input');
    this.input.placeholder = '검색어 (예: 아이유 - 라일락). 비우면 지금 곡으로 다시 검색';
    this.input.addEventListener('keydown', (e) => {
      e.stopPropagation();   // 글자 입력 중에 다른 단축키가 끼어들지 않게
      if (e.key === 'Escape') { this.close(); return; }
      if (e.key === 'Enter' && !e.isComposing) this.search();
    });
    const go = this._el('button', '', '검색'); go.addEventListener('click', () => this.search());
    const row = this._el('div', 'lp-search'); row.append(this.input, go);
    this.listEl = this._el('div', 'lp-list');
    this.previewEl = this._el('div', 'lp-preview');
    this.msgEl = this._el('div', 'lp-msg');
    this.chooseBtn = this._el('button', 'primary', '이 가사로 선택');
    this.chooseBtn.addEventListener('click', () => this.choose());
    this.autoBtn = this._el('button', '', '자동으로 되돌리기');
    this.autoBtn.title = '직접 고른 걸 버리고 앱이 알아서 다시 고르게 해요';
    this.autoBtn.addEventListener('click', () => this.auto());
    const close = this._el('button', '', '닫기'); close.addEventListener('click', () => this.close());
    const foot = this._el('div', 'lp-foot'); foot.append(this.chooseBtn, this.autoBtn, close);
    this.root.replaceChildren(head, this.trackEl, row, this.listEl, this.previewEl, this.msgEl, foot);
  }

  open() {
    this.isOpen = true;
    this.root.style.display = 'flex';
    this.items = []; this.selected = ''; this.previews.clear(); this.input.value = '';
    this.render();
    this._showCurrent();   // 검색 결과를 기다리지 않고 지금 곡부터 보여 준다
    return this.search();
  }

  // 지금 곡 줄을 바로 채운다 (검색은 서버 사정에 따라 몇 초 걸릴 수 있다)
  async _showCurrent() {
    if (!this.h.current) return;
    let t = null;
    try { t = await this.h.current(); } catch { /* 못 받아도 검색 결과가 채워 준다 */ }
    if (!this.isOpen || !t || !t.title) return;
    if (!this.track || !this.track.title) { this.track = t; this.render(); }
  }

  // 패널이 열려 있는 동안 곡이 바뀌면 곡 줄을 갱신한다 (목록은 그대로 두고, 다시 검색하라고 안내)
  onTrackChanged(t) {
    if (!this.isOpen || !t) return;
    const same = this.track && this.track.title === t.title && this.track.artist === t.artist;
    this.track = { title: t.title, artist: t.artist || '', duration: Math.round(t.duration || 0) };
    this.latest = this.track;
    if (!same) {   // 검색 중이어도 기다리지 않고 바로 새 곡으로 다시 찾는다 (search() 가 번호를 올려서 낡은 응답은 버려진다)
      this.items = []; this.selected = ''; this.previews.clear();
      this.msg = '곡이 바뀌었어요. 다시 검색하는 중…';
      this.render();
      this.search();
    } else this.render();
  }

  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    this.seq++;   // 진행 중이던 요청의 응답은 버린다
    this.root.style.display = 'none';
  }

  contains(x, y) {
    if (!this.isOpen) return false;
    const r = this.root.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }

  async search() {
    const my = ++this.seq;
    this.busy = true; this.msg = '찾는 중…'; this.selected = ''; this.previews.clear(); this.render();
    let r;
    try { r = await this.h.candidates(this.input.value.trim()); }
    catch (e) { r = { ok: false, message: String((e && e.message) || e) }; }
    if (my !== this.seq) return;
    this.busy = false;
    if (r && r.track && (!this.latest || (this.latest.title === r.track.title && this.latest.artist === r.track.artist))) this.track = r.track;   // 실패했어도 지금 곡 줄은 보여 준다
    else if (r && r.track && this.latest) return this.search();   // 검색하는 사이 곡이 바뀌었다: 새 곡으로 다시
    if (!r || !r.ok) { this.items = []; this.msg = (r && r.message) || '찾지 못했어요.'; this.render(); return; }
    this.chosen = !!r.chosen; this.items = r.items || [];
    this.msg = this.items.length ? `${this.items.length}개를 찾았어요. 고른 뒤 미리보기를 확인하세요.` : '검색 결과가 없어요. 다른 검색어로 찾아 보세요.';
    if (r.warning) this.msg += `\n⚠ ${r.warning}`;
    const cur = this.items.find((i) => i.current);
    this.render();
    if (cur) this.select(cur.key);   // 지금 쓰는 가사를 미리 골라 둔다
  }

  async select(key) {
    this.selected = key;
    this.render();
    if (this.previews.has(key)) return;
    const my = ++this.seq;   // 미리보기 요청도 번호를 올려 늦은 응답을 버린다 (검색 도중 선택하면 검색이 취소되지는 않게 busy 일 땐 건너뜀)
    let r;
    try { r = await this.h.preview(key); }
    catch (e) { r = { ok: false, message: String((e && e.message) || e) }; }
    if (!this.isOpen || my !== this.seq && this.selected !== key) return;
    this.previews.set(key, r || { ok: false, message: '미리보기를 못 받았어요.' });
    this.render();
  }

  async choose() {
    const key = this.selected;
    const pv = this.previews.get(key);
    if (!key || !pv || !pv.ok || this.busy) return;
    this.busy = true; this.msg = '적용하는 중…'; this.render();
    let r;
    try { r = await this.h.choose(key); } catch (e) { r = { ok: false, message: String((e && e.message) || e) }; }
    this.busy = false;
    if (r && r.ok) { this.close(); if (this.h.onDone) this.h.onDone(r); return; }
    this.msg = (r && r.message) || '적용하지 못했어요.'; this.render();
  }

  async auto() {
    if (this.busy) return;
    this.busy = true; this.msg = '다시 찾는 중…'; this.render();
    let r;
    try { r = await this.h.auto(); } catch (e) { r = { ok: false, message: String((e && e.message) || e) }; }
    this.busy = false;
    this.close();
    if (this.h.onDone) this.h.onDone(r || { ok: false, message: '다시 찾지 못했어요.' });
  }

  render() {
    const t = this.track;
    this._setText(this.trackEl, t ? `지금 곡: ${[t.artist, t.title].filter(Boolean).join(' - ')}${t.duration ? ` (${fmtTime(t.duration)})` : ''}${this.chosen ? '  ·  직접 고른 가사 사용 중' : ''}` : '');
    this.listEl.replaceChildren(...this.items.map((it) => this._row(it)));
    const pv = this.selected ? this.previews.get(this.selected) : null;
    if (!this.selected) this.previewEl.replaceChildren(this._el('div', 'lp-hint', '목록에서 가사를 고르면 첫 줄들과 길이를 보여줘요. 같은 곡이 여러 개면 곡 길이가 비슷한 걸 고르세요.'));
    else if (!pv) this.previewEl.replaceChildren(this._el('div', 'lp-hint', '미리보기 불러오는 중…'));
    else if (!pv.ok) this.previewEl.replaceChildren(this._el('div', 'lp-warn', pv.message || '미리보기를 못 받았어요.'));
    else {
      const note = lengthNote(pv);
      this.previewEl.replaceChildren(
        ...pv.lines.map((l) => this._el('div', 'lp-line', l)),
        this._el('div', note.startsWith('⚠') ? 'lp-warn' : 'lp-hint', `가사 ${pv.count}줄${note ? ` · ${note}` : ''}`),
      );
    }
    this._setText(this.msgEl, this.msg);
    const ready = !!pv && pv.ok && !this.busy;
    this.chooseBtn.disabled = !ready;
    this.autoBtn.disabled = this.busy;
  }

  _row(it) {
    const row = this._el('div', `lp-item${it.key === this.selected ? ' selected' : ''}`);
    const top = this._el('div', 'lp-top');
    top.append(this._el('span', 'lp-badge', PROVIDER_NAME[it.provider] || it.provider), this._el('span', 'lp-name', [it.artist, it.title].filter(Boolean).join(' - ') || '(제목 없음)'));
    const sub = [it.album, it.playtime ? fmtTime(it.playtime) : '', it.current ? '지금 사용 중' : '', it.likely ? '' : '제목/가수가 많이 달라요'].filter(Boolean).join(' · ');
    row.append(top);
    if (sub) row.append(this._el('div', 'lp-sub', sub));
    row.addEventListener('click', () => { if (!this._hasSelection()) this.select(it.key); });
    return row;
  }
}
