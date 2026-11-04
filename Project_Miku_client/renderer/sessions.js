// 대화 목록 패널 (웹 LLM 의 사이드바처럼): 대화 목록 / 새 대화 / 이름 바꾸기 / 삭제 / 검색 / 내보내기 + 현재 대화 기록 보기.
// 서버와의 통신은 하지 않는다. 사용자가 누른 동작을 handlers 로 알려 주고, 화면 갱신은 setSessions/setActive/setMessages 로 받는다.
//
//   handlers: { onNew(), onSwitch(id), onDelete(id), onDeleteAll(), onRename(id, title), onSearch(query), onExport(id|null), onSettings(), onOpen(), onClose() }

export function relTime(ts, now = Date.now() / 1000) {
  const d = now - ts;
  if (d < 60) return '방금';
  if (d < 3600) return `${Math.floor(d / 60)}분 전`;
  if (d < 86400) return `${Math.floor(d / 3600)}시간 전`;
  if (d < 172800) return '어제';
  const dt = new Date(ts * 1000);
  return `${dt.getMonth() + 1}/${dt.getDate()}`;
}

export const metaText = (s, now) => `메시지 ${s.count}개 · ${relTime(s.updated, now)}`;

// 기록 화면용 한 줄 분류: 'user' | 'ai' | 'note'(알림)
export function messageView(m) {
  if (m.kind === 'notification' && m.role === 'user') return { cls: 'note', text: `📩 ${String(m.content).replace(/^\[알림\]\s*/, '')}` };
  return { cls: m.role === 'user' ? 'user' : 'ai', text: String(m.content) };
}

const CONFIRM_MS = 3000;
const SEARCH_DELAY_MS = 250;

export class SessionPanel {
  constructor(root, handlers = {}) {
    this.root = root;
    this.h = handlers;
    this.doc = root.ownerDocument;
    this.sessions = [];
    this.activeId = null;
    this.activeTitle = '';
    this.messages = [];
    this.counts = new Map();   // id -> 서버가 알려준 메시지 수 (기록을 다시 불러올지 판단하는 데 씀)
    this.confirmId = null;     // 삭제 확인 중인 대화
    this.confirmAll = false;   // 전체 삭제 확인 중
    this.confirmTimer = null;
    this.renamingId = null;
    this.searchQuery = '';     // 비어 있으면 평소 목록, 있으면 검색 결과를 보여 준다
    this.searchResults = null; // null = 결과를 기다리는 중
    this.searchTimer = null;
    this._build();
  }

  _el(tag, cls, text) {
    const e = this.doc.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  _build() {
    const head = this._el('div', 'sp-head');
    this.btnNew = this._el('button', 'primary', '＋ 새 대화');
    this.btnNew.addEventListener('click', () => this.h.onNew && this.h.onNew());
    const close = this._el('button', '', '✕');
    close.title = '닫기 (Esc)';
    close.addEventListener('click', () => this.close());
    this.btnAll = this._el('button', 'sp-all', '전체 삭제');
    this.btnAll.addEventListener('click', () => {
      if (!this.sessions.length) return;
      if (this.confirmAll) { this._clearConfirm(); this._renderList(); this.h.onDeleteAll && this.h.onDeleteAll(); return; }
      this._clearConfirm();
      this.confirmAll = true;
      this.confirmTimer = setTimeout(() => { this.confirmAll = false; this.confirmTimer = null; this._renderList(); }, CONFIRM_MS);
      this._renderList();
    });
    const gear = this._el('button', '', '⚙');
    gear.title = '설정 (서버/화면/기억)';
    gear.addEventListener('click', () => this.h.onSettings && this.h.onSettings());
    head.append(this._el('span', 'sp-title', '대화'), this.btnNew, this.btnAll, gear, close);

    // 검색 + 내보내기
    this.searchEl = this._el('input', 'sp-search');
    this.searchEl.placeholder = '🔍 대화 검색 (제목/내용)';
    this.searchEl.maxLength = 60;
    this.searchEl.addEventListener('input', () => this._onSearchInput());
    this.searchEl.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Escape') { this.clearSearch(); }
      else if (e.key === 'Enter' && !e.isComposing) { this._searchNow(); }
    });
    this.btnExportOne = this._el('button', 'sp-ex', '⬇ 이 대화');
    this.btnExportOne.title = '지금 보고 있는 대화를 글 파일로 저장';
    this.btnExportOne.addEventListener('click', () => { if (this.activeId != null && this.h.onExport) this.h.onExport(this.activeId); });
    this.btnExportAll = this._el('button', 'sp-ex', '⬇ 전체');
    this.btnExportAll.title = '모든 대화를 글 파일 하나로 저장';
    this.btnExportAll.addEventListener('click', () => { if (this.sessions.length && this.h.onExport) this.h.onExport(null); });
    const bar = this._el('div', 'sp-bar');
    bar.append(this.searchEl, this.btnExportOne, this.btnExportAll);

    this.listEl = this._el('div', 'sp-list');
    this.logTitle = this._el('div', 'sp-sep');
    this.logEl = this._el('div', 'sp-log');
    this.root.replaceChildren(head, bar, this.listEl, this.logTitle, this.logEl);
    this.render();
  }

  // ---------- 열기/닫기 ----------
  get isOpen() { return this.root.style.display === 'flex'; }
  open() { if (this.isOpen) return; this.root.style.display = 'flex'; this.render(); this.h.onOpen && this.h.onOpen(); }
  close() {
    if (!this.isOpen) return;
    this.root.style.display = 'none';
    this.renamingId = null; this._clearConfirm(); this.clearSearch();
    this.h.onClose && this.h.onClose();
  }
  toggle() { this.isOpen ? this.close() : this.open(); }

  contains(x, y) {
    if (!this.isOpen) return false;
    const r = this.root.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }

  // 모델 옆(오른쪽, 자리가 없으면 왼쪽)에 붙인다
  position(bounds, winW, winH) {
    const pw = 320;
    const ph = Math.min(640, Math.max(winH - 24, 240));
    let left = bounds.x + bounds.width + 12;
    if (left + pw > winW - 8) left = bounds.x - pw - 12;
    left = Math.min(Math.max(left, 8), Math.max(winW - pw - 8, 8));
    const top = Math.min(Math.max(bounds.y, 12), Math.max(winH - ph - 12, 12));
    Object.assign(this.root.style, { left: `${left}px`, top: `${top}px`, height: `${ph}px` });
  }

  // ---------- 서버에서 온 데이터 ----------
  countOf(id) { return this.counts.has(id) ? this.counts.get(id) : null; }

  setSessions(list, activeId) {
    const next = activeId == null ? null : activeId;
    if (next !== this.activeId && next == null) this.messages = [];   // 새 대화 상태로 바뀌면 이전 기록은 지운다
    this.sessions = Array.isArray(list) ? list : [];
    this.activeId = next;
    this.counts = new Map(this.sessions.map((s) => [s.id, s.count]));
    const cur = this.sessions.find((s) => s.id === next);
    this.activeTitle = cur ? cur.title : '';
    if (this.renamingId != null && !this.sessions.some((s) => s.id === this.renamingId)) this.renamingId = null;
    this.render();
  }

  setActive(id, title, messages) {
    this.activeId = id == null ? null : id;
    this.activeTitle = title || '';
    this.messages = Array.isArray(messages) ? messages : [];
    this.render();
  }

  setMessages(id, messages) {
    if (id !== this.activeId) return;   // 그 사이 다른 대화로 옮겼으면 버린다
    this.messages = Array.isArray(messages) ? messages : [];
    this._renderLog();
  }

  // ---------- 검색 ----------
  _onSearchInput() {
    clearTimeout(this.searchTimer);
    this.searchTimer = null;
    const q = this.searchEl.value.trim();
    if (!q) { this.clearSearch(); return; }
    this.searchTimer = setTimeout(() => this._searchNow(), SEARCH_DELAY_MS);
  }

  _searchNow() {
    clearTimeout(this.searchTimer);
    this.searchTimer = null;
    const q = this.searchEl.value.trim();
    if (!q) { this.clearSearch(); return; }
    this.searchQuery = q;
    this.searchResults = null;
    this._renderList();
    this.h.onSearch && this.h.onSearch(q);
  }

  clearSearch() {
    clearTimeout(this.searchTimer);
    this.searchTimer = null;
    const had = this.searchQuery !== '';
    this.searchEl.value = '';
    this.searchQuery = '';
    this.searchResults = null;
    if (had) this._renderList();
  }

  // 서버가 돌려준 검색 결과. 그 사이 검색어를 바꿨거나 지웠으면 버린다.
  setSearchResults(query, results) {
    if (!this.searchQuery || query !== this.searchQuery) return;
    this.searchResults = Array.isArray(results) ? results : [];
    this._renderList();
  }

  _resultItem(r) {
    const row = this._el('div', `sp-item${r.session_id === this.activeId ? ' active' : ''}`);
    const main = this._el('div', 'sp-main');
    const who = r.role === 'user' ? '나: ' : r.role === 'assistant' ? 'AI: ' : '';
    main.append(this._el('div', 'sp-name', r.title || '(제목 없음)'), this._el('div', 'sp-meta', r.snippet ? who + r.snippet : '제목이 일치해요'));
    row.append(main);
    row.addEventListener('click', () => {
      const id = r.session_id;
      this.clearSearch();
      if (id !== this.activeId) this.h.onSwitch && this.h.onSwitch(id);
    });
    return row;
  }

  // ---------- 그리기 ----------
  render() { this._renderList(); this._renderLog(); }

  _clearConfirm() { clearTimeout(this.confirmTimer); this.confirmTimer = null; this.confirmId = null; this.confirmAll = false; }

  _renderList() {
    const rows = [];
    this.btnExportOne.disabled = this.activeId == null;
    this.btnExportAll.disabled = !this.sessions.length;
    for (const b of [this.btnExportOne, this.btnExportAll]) b.style.opacity = b.disabled ? '0.45' : '1';
    if (this.searchQuery) {   // 검색 중: 결과만 보여 준다
      if (this.searchResults === null) rows.push(this._el('div', 'sp-empty', '검색 중…'));
      else if (!this.searchResults.length) rows.push(this._el('div', 'sp-empty', `"${this.searchQuery}" 검색 결과가 없어요`));
      else for (const r of this.searchResults) rows.push(this._resultItem(r));
      this.listEl.replaceChildren(...rows);
      this.btnNew.disabled = this.activeId == null;
      this.btnNew.style.opacity = this.btnNew.disabled ? '0.45' : '1';
      this.btnAll.disabled = !this.sessions.length;
      this.btnAll.style.opacity = this.btnAll.disabled ? '0.45' : '1';
      return;
    }
    if (this.activeId == null) {   // 아직 저장 전인 새 대화
      const row = this._el('div', 'sp-item active');
      const main = this._el('div', 'sp-main');
      main.append(this._el('div', 'sp-name', '새 대화'), this._el('div', 'sp-meta', '첫 메시지를 보내면 목록에 저장돼요'));
      row.append(main);
      rows.push(row);
    }
    for (const s of this.sessions) rows.push(this._item(s));
    this.listEl.replaceChildren(...rows);
    this.btnNew.disabled = this.activeId == null;   // 이미 새 대화면 또 만들 필요 없다
    this.btnNew.style.opacity = this.btnNew.disabled ? '0.45' : '1';
    if (!this.sessions.length) this.confirmAll = false;
    this.btnAll.disabled = !this.sessions.length;   // 지울 대화가 없으면 못 누른다
    this.btnAll.style.opacity = this.btnAll.disabled ? '0.45' : '1';
    this.btnAll.textContent = this.confirmAll ? '정말 전부?' : '전체 삭제';
    this.btnAll.title = this.confirmAll ? '한 번 더 누르면 모든 대화를 지워요' : '모든 대화 지우기';
    this.btnAll.className = this.confirmAll ? 'sp-all danger' : 'sp-all';
  }

  _item(s) {
    const active = s.id === this.activeId;
    const confirming = this.confirmId === s.id;
    const row = this._el('div', `sp-item${active ? ' active' : ''}${confirming ? ' confirm' : ''}`);
    row.dataset.id = String(s.id);
    const main = this._el('div', 'sp-main');

    if (this.renamingId === s.id) {
      const inp = this._el('input', 'sp-rename');
      inp.value = s.title;
      inp.maxLength = 60;
      let done = false;
      const finish = (commit) => {
        if (done) return;
        done = true;
        this.renamingId = null;
        const v = inp.value.trim();
        if (commit && v && v !== s.title) this.h.onRename && this.h.onRename(s.id, v);
        this._renderList();
      };
      inp.addEventListener('keydown', (e) => {
        e.stopPropagation();
        if (e.key === 'Enter' && !e.isComposing) finish(true);
        else if (e.key === 'Escape') finish(false);
      });
      inp.addEventListener('blur', () => finish(true));
      main.append(inp);
      row.append(main);
      setTimeout(() => { inp.focus(); inp.select(); }, 0);
      return row;
    }

    main.append(this._el('div', 'sp-name', s.title || '(제목 없음)'), this._el('div', 'sp-meta', metaText(s)));
    const edit = this._el('button', 'sp-btn', '✎');
    edit.title = '이름 바꾸기';
    edit.addEventListener('click', (e) => { e.stopPropagation(); this._clearConfirm(); this.renamingId = s.id; this._renderList(); });
    const del = this._el('button', `sp-btn${confirming ? ' danger' : ''}`, confirming ? '삭제?' : '🗑');
    del.title = confirming ? '한 번 더 누르면 지워요' : '이 대화 지우기';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      if (this.confirmId === s.id) { this._clearConfirm(); this.h.onDelete && this.h.onDelete(s.id); return; }
      this._clearConfirm();
      this.confirmId = s.id;
      this.confirmTimer = setTimeout(() => { this.confirmId = null; this._renderList(); }, CONFIRM_MS);
      this._renderList();
    });
    row.append(main, edit, del);
    row.addEventListener('click', () => { if (!active && this.renamingId == null) this.h.onSwitch && this.h.onSwitch(s.id); });
    return row;
  }

  _renderLog() {
    this.logTitle.textContent = `${this.activeTitle || '새 대화'} · 기록`;
    if (!this.messages.length) {
      this.logEl.replaceChildren(this._el('div', 'sp-empty', this.activeId == null ? '아직 대화가 없어요. 아무 말이나 걸어 보세요.' : '기록이 없어요.'));
      return;
    }
    this.logEl.replaceChildren(...this.messages.map((m) => {
      const v = messageView(m);
      return this._el('div', `msg ${v.cls}`, v.text);
    }));
    this.logEl.scrollTop = this.logEl.scrollHeight;
  }
}
