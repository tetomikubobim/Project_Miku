// 설정 창: 모델(persona/감정/음성) / 클라이언트(화면) 설정 / 장기 기억 관리. 트레이 메뉴 "설정…" 또는 대화 목록의 ⚙ 로 연다.
// (서버 설정 — LLM, 음성, GPT-SoVITS, 음성 인식 등 — 은 통합 런처가 서버의 config.yaml 로 관리한다.)
// 서버와의 통신/저장은 하지 않는다: 사용자가 누른 동작을 handlers 로 알리고, 서버가 돌려준 데이터는 setMemories 로 받는다.
//
//   handlers: { onOpen(), onClose(), onRefresh(), onSaveClient(patch, restartNeeded), onRelaunch(),
//               onMemoryAdd(text), onMemoryUpdate(id, text), onMemoryDelete(id), onMemoryClear(), getClientConfig(), isConnected(),
//               getProfile() -> {model, persona, rows:[{tag,expression,desc}], voice:{gpt_weights_path,…,speed_factor}, lyricsPose:{expression,motion}, poseLists:{expressions:[이름], motions:[이름]}, expressions:[이름]},
//               onSaveProfile(model, {persona, rows, voice, lyricsPose}) -> {ok, error}, onPreviewEmotion(이름),
//               loadHotkeys() -> {defaults, overrides, status}, onSaveHotkeys(overrides) -> {ok, status, error}, onHotkeyPause(true|false) (키를 눌러 정하는 동안 등록된 단축키를 잠시 풀기) }
import { normalizeGaze } from './gaze.js';
import { normalizeProactive } from './proactive.js';
import { cleanName } from './emotion.js';
import { HOTKEY_ACTIONS, readKeyEvent, joinAccel, checkAccel, displayAccel, effectiveMap, buildOverrides, findConflicts, normAccel, firstOf } from './hotkeys.js';

// 클라이언트(config.json) 설정 항목. norm: 값이 비었을 때 앱이 실제로 쓰는 기본값을 구하는 함수
export const CLIENT_FIELDS = [
  { path: 'serverUrl', label: '서버 주소', type: 'str', group: '연결', restart: true, hint: '예: ws://127.0.0.1:3545/ws', def: 'ws://127.0.0.1:3545/ws' },
  { path: 'audioBaseUrl', label: '음성 주소 덮어쓰기', type: 'str', group: '연결', hint: '서버가 알려주는 주소가 안 맞을 때만', def: '' },
  { path: 'streaming', label: '답을 만드는 대로 먼저 보여주기', type: 'bool', group: '연결', def: true },

  { path: 'scale', label: '모델 크기', type: 'float', min: 0.3, max: 2.5, group: '화면', def: 1 },
  { path: 'showBubble', label: '말풍선 표시', type: 'bool', group: '화면', def: true },
  { path: 'bubbleSeconds', label: '말풍선이 남는 시간(초)', type: 'float', min: 1, max: 60, group: '화면', def: 6 },
  { path: 'showThink', label: '행동 묘사(💭) 표시', type: 'bool', group: '화면', def: true },
  { path: 'showEmotionTag', label: '감정 태그 표시', type: 'bool', group: '화면', def: true },
  { path: 'idleMotion', label: '대기모션 자동재생', type: 'bool', group: '화면', restart: true, def: true },
  { path: 'artMeshColors', label: 'VTube Studio 아트메시 투명도/색 적용', type: 'bool', group: '화면', def: true, hint: '스튜디오에서 투명하게 만든 워터마크 등이 여기서도 투명해져요' },

  { path: 'lyrics.enabled', label: '실시간 가사 (재생 중인 곡 가사를 말풍선 자리에)', type: 'bool', group: '가사', def: true, hint: '스포티파이/유튜브 등이 윈도우에 알리는 곡 정보로 알송에서 가사를 찾아요 (Windows)' },
  { path: 'lyrics.lipSync', label: '노래할 때 입 움직이기 (음성이 나올 땐 음성 우선)', type: 'bool', group: '가사', def: true },
  { path: 'lyrics.llmFix', label: '못 찾으면 서버 LLM 으로 제목/가수 보정', type: 'bool', group: '가사', def: true, hint: '유튜브 제목이 지저분하거나 로마자일 때 서버의 LLM 으로 정리해서 다시 찾아요 (곡마다 한 번, 결과는 저장)' },
  { path: 'lyrics.translate', label: '번역 없는 가사는 서버 LLM 으로 번역해서 붙이기', type: 'bool', group: '가사', def: true, hint: '원문만 있는 가사에 번역줄을 덧붙여요 (곡마다 한 번, 결과는 저장). 원문 싱크를 그대로 따라가요. /가사 번역 으로 다시 번역' },
  { path: 'lyrics.translateLang', label: '번역할 언어 (도착)', type: 'select', options: ['ko', 'en', 'ja', 'zh'], group: '가사', def: 'ko', hint: 'ko=한국어 en=영어 ja=일본어 zh=중국어. 바꾸면 곡마다 새로 번역해요 (저장된 번역은 마지막 언어 하나만 남아요)' },
  { path: 'lyrics.translateFrom', label: '번역할 가사의 언어 (출발)', type: 'str', group: '가사', def: 'all', hint: 'all=도착 언어가 아닌 가사 전부. 일본어만이면 ja, 영어와 일본어면 en,ja (ko en ja zh 중에서, 한글로 일본어 처럼 써도 돼요). 반대로 한국어 가사를 일본어로: 출발 ko, 도착 ja' },
  { path: 'lyrics.sources', label: '가사 출처', type: 'select', options: ['alsong+lrclib', 'alsong', 'lrclib'], group: '가사', def: 'alsong+lrclib', hint: '알송이 막히거나 곡이 없을 때 LRCLIB 이 대신해요' },
  { path: 'lyrics.apps', label: '가사를 띄울 앱 (비우면 전부)', type: 'str', group: '가사', def: '', hint: '예: spotify, chrome, msedge', },
  { path: 'lyrics.layout', label: '가사 줄 구성', type: 'select', options: ['auto', 'next'], group: '가사', def: 'auto', hint: 'auto=원문 / 같이 든 줄(발음, 번역 최대 2줄), 없으면 다음 줄. next=항상 원문 / 다음 줄' },
  { path: 'lyrics.hidePron', label: '발음 줄 숨기기 (줄이 3개 이상일 때 2번째 줄 빼기)', type: 'bool', group: '가사', def: false, hint: '원문 / 발음 / 번역처럼 같이 든 줄이 2개 이상이면 첫 번째(발음)를 빼고 번역만 보여요. 같이 든 줄이 1개뿐이면(번역일 수 있어서) 그대로 보여요' },
  { path: 'lyrics.color1', label: '가사 1줄(원문) 글자 색', type: 'color', group: '가사', def: '', hint: '비워 두면 기본(흰색). 색 상자를 누르거나 R G B 를 0~255 로 입력. 지우기를 누르면 기본으로' },
  { path: 'lyrics.color2', label: '가사 2줄(발음/다음 줄) 글자 색', type: 'color', group: '가사', def: '', hint: '비워 두면 기본(흰색). 투명도는 "아랫줄 투명도" 설정을 따라요' },
  { path: 'lyrics.color3', label: '가사 3줄(번역) 글자 색', type: 'color', group: '가사', def: '', hint: '비워 두면 기본(흰색)' },
  { path: 'lyrics.lipSpeed', label: '노래 입 움직임 속도 (1=기본, 작을수록 느림)', type: 'float', min: 0.1, max: 3, group: '가사', def: 1 },
  { path: 'lyrics.line2Opacity', label: '아랫줄 투명도 - 다음 줄을 보여줄 때만 (0.1~1)', type: 'float', min: 0.1, max: 1, group: '가사', def: 0.45 },
  { path: 'nowPlayingContext', label: '곡에 대해 물으면 지금 재생 중인 곡을 AI 에게 알려주기', type: 'bool', group: '가사', def: true, hint: '"이 노래 누가 불렀어?" 같은 질문에만 지금 재생 중인 곡(제목/가수/앱)을 그 메시지와 함께 보내요. 곡이 바뀔 때마다 보내지는 않고, 대화 기록에도 남지 않아요' },
  { path: 'scheduleContext', label: '일정이 있으면 AI 에게 알려주기', type: 'bool', group: '대화 부가정보', def: true, hint: '일정 관리 창에 적어 둔 곧 있을 일정(최대 8개)을 매 대화에 같이 보내서 "오늘 일정 뭐야?"에 답할 수 있게 해요. 일정이 없으면 아무것도 안 보내요' },
  { path: 'clipboardContext', label: '"방금 복사한 거" 같은 말에 클립보드 읽어서 알려주기', type: 'bool', group: '대화 부가정보', def: true, hint: '"클립보드", "방금 복사한 거" 처럼 클립보드를 가리키는 말을 했을 때만 복사한 글자를 AI 에게 보내요. 항상 읽지는 않아요' },
  { path: 'clipboardMaxChars', label: '클립보드를 보낼 최대 글자 수', type: 'int', min: 100, max: 20000, group: '대화 부가정보', def: 2000, hint: '넘으면 앞부분만 보내요. 글자가 많을수록 토큰을 더 써요 (한글 2000자 ≈ 1000~1500토큰)' },
  { path: 'lyrics.item', label: '가사창 뜰 때 마이크 스탠드 같이 보이기', type: 'bool', group: '가사', def: true, hint: '가사가 나오는 동안 모델 입 근처에 그림(마이크 스탠드)을 보여줘요. 위치/크기는 편집 모드에서 끌거나 휠로 조절하고 모델별로 저장돼요. 그림을 바꾸려면 모델 폴더에 lyricsItem.png 를 넣으세요' },
  { path: 'lyrics.background', label: '가사 뒤에 어두운 배경 상자', type: 'bool', group: '가사', def: true },

  { path: 'proactive.enabled', label: '먼저 말 걸기', type: 'bool', group: '먼저 말 걸기', norm: normalizeProactive, key: 'enabled', configKey: 'proactive' },
  { path: 'proactive.minMinutes', label: '최소 간격(분)', type: 'int', min: 1, max: 720, group: '먼저 말 걸기', norm: normalizeProactive, key: 'minMinutes', configKey: 'proactive' },
  { path: 'proactive.maxMinutes', label: '최대 간격(분)', type: 'int', min: 1, max: 1440, group: '먼저 말 걸기', norm: normalizeProactive, key: 'maxMinutes', configKey: 'proactive' },
  { path: 'proactiveScreen', label: '먼저 말 걸 때도 화면 보기 (화면 공유 모드일 때)', type: 'bool', group: '먼저 말 걸기', def: true, hint: '화면 공유 모드를 켜 두면 캐릭터가 먼저 말을 걸 때도 지금 화면을 같이 봐요. 끄면 먼저 거는 말은 화면 없이 해요. 화면 공유 모드가 꺼져 있으면 아무 영향이 없어요' },

  { path: 'gaze.enabled', label: '마우스 따라보기', type: 'bool', group: '시선', norm: normalizeGaze, key: 'enabled', configKey: 'gaze' },
  { path: 'gaze.strength', label: '움직임 세기 (0~1)', type: 'float', min: 0, max: 1, group: '시선', norm: normalizeGaze, key: 'strength', configKey: 'gaze' },
  { path: 'gaze.head', label: '고개/몸도 움직임', type: 'bool', group: '시선', norm: normalizeGaze, key: 'head', configKey: 'gaze' },
  { path: 'gaze.radius', label: '민감 거리(px, 작을수록 예민)', type: 'int', min: 50, max: 4000, group: '시선', norm: normalizeGaze, key: 'radius', configKey: 'gaze' },
  { path: 'gaze.idleSeconds', label: '멈추면 정면으로 돌아오는 시간(초, 0=계속)', type: 'float', min: 0, max: 3600, group: '시선', norm: normalizeGaze, key: 'idleSeconds', configKey: 'gaze' },
];

export function getPath(obj, path) {
  let cur = obj;
  for (const k of path.split('.')) {
    if (cur == null || typeof cur !== 'object' || !(k in cur)) return undefined;
    cur = cur[k];
  }
  return cur;
}

// 지금 앱이 실제로 쓰는 값 (config.json 에 없으면 기본값)
export function currentValue(field, config) {
  if (field.norm) return field.norm(config && config[field.configKey])[field.key];
  const v = getPath(config || {}, field.path);
  return v === undefined ? field.def : v;
}

// 화면에서 온 값(체크박스는 true/false, 나머지는 글자)을 검사해서 올바른 형식으로. 안 되면 Error(사용자에게 보여 줄 메시지)
export function coerceField(field, raw) {
  const t = field.type;
  if (t === 'bool') {
    if (typeof raw !== 'boolean') throw new Error(`'${field.label}' 은 켜기/끄기여야 해요`);
    return raw;
  }
  if (t === 'int' || t === 'float') {
    const s = typeof raw === 'number' ? String(raw) : String(raw ?? '').trim();
    if (s === '' || !Number.isFinite(Number(s))) throw new Error(`'${field.label}' 은 숫자여야 해요`);
    const n = t === 'int' ? Math.round(Number(s)) : Number(s);
    if ((field.min != null && n < field.min) || (field.max != null && n > field.max)) throw new Error(`'${field.label}' 은 ${field.min} ~ ${field.max} 사이여야 해요`);
    return n;
  }
  if (t === 'color') {
    const c = String(raw ?? '').trim();
    if (c === '') return '';
    if (!/^#[0-9a-fA-F]{6}$/.test(c)) throw new Error(`'${field.label}' 은 #RRGGBB 형식이어야 해요`);
    return c.toLowerCase();
  }
  if (t === 'select') {
    if (!(field.options || []).includes(raw)) throw new Error(`'${field.label}' 은 ${(field.options || []).join(' / ')} 중 하나여야 해요`);
    return raw;
  }
  const v = String(raw ?? '').trim();
  if (v.length > 500) throw new Error(`'${field.label}' 이 너무 길어요`);
  return v;
}

// 바뀐 항목 {경로: 값} 을 config:set 에 보낼 패치로. 'gaze.strength' 처럼 안쪽 값은 그 묶음 전체를 합쳐서 (얕은 병합이라서)
export function buildPatch(config, changes) {
  const patch = {};
  for (const [path, value] of Object.entries(changes)) {
    const i = path.indexOf('.');
    if (i < 0) { patch[path] = value; continue; }
    const top = path.slice(0, i);
    const base = patch[top] ?? (config && typeof config[top] === 'object' && config[top] ? config[top] : {});
    patch[top] = { ...base, [path.slice(i + 1)]: value };
  }
  return patch;
}

const CONFIRM_MS = 3000;
const SIZE_KEY = 'miku.settings.size';   // 설정 창 크기 (localStorage)
const COLLAPSE_KEY = 'miku.settings.collapsed';   // 접어 둔 그룹 목록 (localStorage)
function loadCollapsed() {
  try {
    const arr = JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '[]');
    return new Set(Array.isArray(arr) ? arr.filter((x) => typeof x === 'string').slice(0, 100) : []);
  } catch { return new Set(); }   // localStorage 가 없거나 깨졌으면 모두 펼친 상태
}
const TABS = [['model', '모델'], ['client', '화면/앱'], ['hotkeys', '단축키'], ['memory', '기억']];
export const TAG_RE = /^[A-Za-z가-힣]{1,20}$/;
// 모델별 음성 (GPT-SoVITS). 키는 서버 config.yaml 의 tts 설정과 같다. 비워 두면 서버 설정을 쓴다.
export const VOICE_FIELDS = [
  { key: 'gpt_weights_path', label: 'GPT 가중치(.ckpt)', hint: '예: GPT_weights_v3/내모델-e15.ckpt' },
  { key: 'sovits_weights_path', label: 'SoVITS 가중치(.pth)', hint: '예: SoVITS_weights_v3/내모델_e8_s200.pth' },
  { key: 'ref_audio_path', label: '참조 음성 파일', hint: '예: C:/voice/ref.wav' },
  { key: 'prompt_text', label: '참조 음성의 대사', hint: '참조 음성에서 실제로 말하는 내용' },
  { key: 'prompt_lang', label: '참조 음성 언어', hint: 'ko / ja / en / zh' },
  { key: 'speed_factor', label: '말하는 속도 (1.0=보통)', hint: '0.5 ~ 2.0 (예: 1.1). 비우면 서버 설정. Edge 음성에도 적용돼요' },
];
export const LANG_RE = /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$/;   // 서버가 [태그] 로 알아보는 글자만 (native/profile.js 와 같은 규칙)

export class SettingsDialog {
  constructor(root, handlers = {}) {
    this.root = root;
    this.h = handlers;
    this.doc = root.ownerDocument;
    this.tab = 'client';
    this.msg = { model: '', client: '', memory: '', hotkeys: '' };
    this.draft = null;             // 모델 탭에서 고치는 중인 값 { model, persona, rows } (저장하거나 창을 열 때 초기화)
    this.memories = null;
    this.confirmKey = null;        // 두 번 눌러 지우는 확인 상태 ('m:<id>' 또는 'clear')
    this.confirmTimer = null;
    this.editingId = null;
    this.hk = null;                // 단축키 탭 { defaults, saved, draft, status } (열 때마다 메인에서 새로 받는다)
    this.hkLoading = false;
    this.hkError = '';
    this.recording = null;         // 단축키를 눌러 정하는 중 { id, btn }
    this.readers = new Map();      // path -> () => 지금 입력된 값
    this.collapsed = loadCollapsed();   // 접어 둔 그룹 ('탭:제목')
    this.restartNeeded = false;
    this.dragging = false;         // 제목줄을 끌어 창을 옮기는 중
    this._drag = null;
    this._build();
  }

  _el(tag, cls, text) {
    const e = this.doc.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  _build() {
    this.titleRow = this._el('div', 'st-head');
    const close = this._el('button', '', '✕');
    close.title = '닫기 (Esc)';
    close.addEventListener('click', () => this.close());
    this.titleRow.append(this._el('span', 'st-title', '설정'), close);
    this.titleRow.addEventListener('mousedown', (e) => this._dragStart(e));   // 제목줄을 잡고 창을 옮긴다
    this.tabsEl = this._el('div', 'st-tabs');
    this.bodyEl = this._el('div', 'st-body');
    this.footEl = this._el('div', 'st-foot');
    this.root.replaceChildren(this.titleRow, this.tabsEl, this.bodyEl, this.footEl);
    this._initResize();
    this.render();
  }

  // 오른쪽 아래 모서리를 끌어 바꾼 창 크기를 기억한다 (CSS resize: both). 다음에 열 때 그 크기로.
  _initResize() {
    try {
      const sz = JSON.parse(localStorage.getItem(SIZE_KEY) || 'null');
      if (sz && sz.w >= 360 && sz.h >= 260) { this.root.style.width = `${sz.w}px`; this.root.style.height = `${sz.h}px`; }
    } catch { /* 저장된 크기를 못 읽으면 기본 크기 */ }
    if (typeof ResizeObserver === 'undefined') return;
    let t = null;
    new ResizeObserver(() => {
      if (!this.isOpen || (!this.root.style.width && !this.root.style.height)) return;   // 사용자가 끌어서 바꾼 적이 없으면(탭 내용 때문에 높이가 달라진 것뿐이면) 기억하지 않는다
      clearTimeout(t);
      t = setTimeout(() => {
        const r = this.root.getBoundingClientRect();
        if (r.width < 360 || r.height < 260) return;
        try { localStorage.setItem(SIZE_KEY, JSON.stringify({ w: Math.round(r.width), h: Math.round(r.height) })); } catch { /* 저장 못 해도 이번엔 된다 */ }
        this._fit();
      }, 300);
    }).observe(this.root);
  }

  // ---------- 열기/닫기 ----------
  get isOpen() { return this.root.style.display === 'flex'; }
  open(tab) {
    if (tab) this.tab = tab;
    if (this.isOpen) { this.render(); return; }
    this.root.style.display = 'flex';
    if (this.root.style.transform !== 'none') {   // 가운데 정렬(transform)이면 크기를 바꿀 때 창이 양쪽으로 늘어나서 모서리를 끌기 어렵다: 지금 자리 그대로 좌표로 바꿔 둔다
      const r = this.root.getBoundingClientRect();
      this.root.style.left = `${r.left}px`; this.root.style.top = `${r.top}px`; this.root.style.transform = 'none';
    }
    this._fit();   // 옮겨 둔 위치가 지금 화면 밖이면 안으로 끌어온다
    this.msg = { model: '', client: '', memory: '', hotkeys: '' };
    this.draft = null;
    this.hk = null; this.hkError = '';   // 단축키 탭은 열 때마다 새로 읽는다
    this.restartNeeded = false;
    this.render();
    this.h.onOpen && this.h.onOpen();
    this.h.onRefresh && this.h.onRefresh();
  }
  close() {
    if (!this.isOpen) return;
    this._stopRecord(false);
    this._dragEnd();
    this.root.style.display = 'none';
    this._clearConfirm();
    this.editingId = null;
    this.h.onClose && this.h.onClose();
  }
  contains(x, y) {
    if (!this.isOpen) return false;
    if (this.dragging) return true;   // 옮기는 중에는 커서가 창 밖으로 잠깐 벗어나도 마우스를 계속 받는다 (클릭 통과로 넘어가지 않게)
    const r = this.root.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }

  // ---------- 창 옮기기 (제목줄을 마우스로 끈다) ----------
  _viewport() {
    const w = this.doc.defaultView;
    return { w: (w && w.innerWidth) || 1280, h: (w && w.innerHeight) || 720 };
  }

  _dragStart(e) {
    if (!this.isOpen || (e.button !== undefined && e.button !== 0)) return;
    if (e.target && e.target.closest && e.target.closest('button')) return;   // ✕ 버튼은 그대로 눌린다
    const r = this.root.getBoundingClientRect();
    this.root.style.transform = 'none';   // 처음엔 가운데 정렬(transform) 이라, 옮길 때부터 좌표로 바꾼다
    this.root.style.left = `${r.left}px`;
    this.root.style.top = `${r.top}px`;
    this._drag = { dx: e.clientX - r.left, dy: e.clientY - r.top, w: r.right - r.left, h: r.bottom - r.top };
    this.dragging = true;
    const win = this.doc.defaultView;
    if (win && win.addEventListener) {
      this._onMove = (ev) => this._dragMove(ev.clientX, ev.clientY);
      this._onUp = () => this._dragEnd();
      win.addEventListener('mousemove', this._onMove);
      win.addEventListener('mouseup', this._onUp);
    }
    if (e.preventDefault) e.preventDefault();   // 끄는 동안 글자가 선택되지 않게
  }

  _dragMove(x, y) {
    const d = this._drag;
    if (!d) return;
    const v = this._viewport();
    // 창이 화면 밖으로 나가 제목줄을 못 잡게 되는 일이 없도록 화면 안에 가둔다
    this.root.style.left = `${Math.min(Math.max(x - d.dx, 0), Math.max(v.w - d.w, 0))}px`;
    this.root.style.top = `${Math.min(Math.max(y - d.dy, 0), Math.max(v.h - d.h, 0))}px`;
  }

  _dragEnd() {
    const win = this.doc.defaultView;
    if (win && win.removeEventListener && this._onMove) {
      win.removeEventListener('mousemove', this._onMove);
      win.removeEventListener('mouseup', this._onUp);
    }
    this._onMove = this._onUp = null;
    this._drag = null;
    this.dragging = false;
  }

  // 옮겨 둔 창이 지금 화면(창 크기가 바뀌었거나 탭을 바꿔 높이가 달라짐)을 벗어나면 안으로 끌어온다. 안 옮겼으면 가운데 그대로.
  _fit() {
    if (this.root.style.transform !== 'none') return;
    const r = this.root.getBoundingClientRect();
    const v = this._viewport();
    const left = Math.min(Math.max(r.left, 0), Math.max(v.w - (r.right - r.left), 0));
    const top = Math.min(Math.max(r.top, 0), Math.max(v.h - (r.bottom - r.top), 0));
    this.root.style.left = `${left}px`;
    this.root.style.top = `${top}px`;
  }

  // ---------- 서버에서 온 데이터 ----------
  setMemories(list) { this.memories = Array.isArray(list) ? list : []; this.editingId = null; this._clearConfirm(); this.render(); }
  setMemoryError(text) { this.msg.memory = text; this.render(); }

  _clearConfirm() { clearTimeout(this.confirmTimer); this.confirmTimer = null; this.confirmKey = null; }
  _arm(key) {
    this._clearConfirm();
    this.confirmKey = key;
    this.confirmTimer = setTimeout(() => { this.confirmKey = null; this.confirmTimer = null; this.render(); }, CONFIRM_MS);
  }

  // ---------- 그리기 ----------
  render() {
    this.readers = new Map();
    const tabs = TABS.map(([id, label]) => {
      const b = this._el('button', `st-tab${this.tab === id ? ' active' : ''}`, label);
      b.addEventListener('click', () => { if (this.tab !== id) { this.tab = id; this._clearConfirm(); this.render(); } });
      return b;
    });
    this.tabsEl.replaceChildren(...tabs);
    if (this.recording && this.tab !== 'hotkeys') this._stopRecord(false);   // 다른 탭으로 가면 키 입력 대기를 끝낸다
    if (this.tab === 'model') this._renderModel();
    else if (this.tab === 'client') this._renderClient();
    else if (this.tab === 'hotkeys') this._renderHotkeys();
    else this._renderMemory();
    if (this.isOpen) this._fit();   // 탭을 바꿔 창 높이가 달라져도 화면 안에 머문다
  }

  // 그룹 제목: 누르면 그 그룹의 항목들이 접히고 펼쳐진다. 접힌 그룹은 기억해 둔다 (탭을 바꾸거나 창을 닫았다 열어도 유지).
  // 항목 요소는 _wireGroups 가 제목과 이어 준다. 화살표(▾/▸)는 CSS 로 그려서 제목 글자는 그대로다.
  _group(title) {
    const h = this._el('div', 'st-group', title);
    h._groupKey = `${this.tab}:${title}`;
    h._members = [];
    h.tabIndex = 0;
    h.title = '눌러서 접기/펼치기';
    const toggle = () => this._toggleGroup(h);
    h.addEventListener('click', toggle);
    h.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { if (e.preventDefault) e.preventDefault(); toggle(); } });
    return h;
  }

  // rows(제목과 항목이 섞인 목록)에서 제목 뒤에 오는 항목들을 그 제목의 소속으로 묶고, 접힌 상태를 적용한다.
  // 제목이 하나뿐인 탭은 접어도 의미가 없으니 묶지 않는다. 마지막 안내 문구처럼 그룹 밖에 둘 요소는 이 함수를 부른 뒤에 목록에 넣는다.
  _wireGroups(rows) {
    const heads = rows.filter((r) => r._groupKey);
    let cur = null;
    for (const r of rows) {
      if (r._groupKey) { cur = heads.length > 1 ? r : null; continue; }
      if (cur) cur._members.push(r);
    }
    for (const h of heads) {
      if (heads.length <= 1) { h.className = 'st-group'; h.tabIndex = -1; h.title = ''; h._noToggle = true; continue; }
      this._paintGroup(h);
    }
  }

  _paintGroup(h) {
    const closed = this.collapsed.has(h._groupKey);
    h.className = `st-group toggle${closed ? ' collapsed' : ''}`;
    for (const m of h._members) m.style.display = closed ? 'none' : '';
  }

  _toggleGroup(h) {
    if (h._noToggle) return;
    if (this.collapsed.has(h._groupKey)) this.collapsed.delete(h._groupKey); else this.collapsed.add(h._groupKey);
    this._paintGroup(h);   // 다시 그리지 않고 보이기만 바꾼다 (입력 중인 값이 사라지지 않게)
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...this.collapsed])); } catch { /* 저장 못 해도 이번엔 동작한다 */ }
    if (this.isOpen) this._fit();
  }

  _fieldRow(field, value, setFlag) {
    const row = this._el('label', 'st-row');
    const name = this._el('span', 'st-label', field.label + (field.restart ? ' ↻' : ''));
    if (field.hint) name.title = field.hint;
    let input;
    if (field.type === 'bool') {
      input = this._el('input'); input.type = 'checkbox'; input.checked = value === true;
      this.readers.set(field.path, () => input.checked);
    } else if (field.type === 'color') {
      input = this._colorInput(value, field);
    } else if (field.type === 'select') {
      input = this._el('select');
      for (const o of field.options || []) { const op = this._el('option', '', o); op.value = o; input.append(op); }
      input.value = value ?? '';
      this.readers.set(field.path, () => input.value);
    } else {
      input = this._el('input');
      input.type = field.type === 'secret' ? 'password' : 'text';
      if (field.type === 'int' || field.type === 'float') input.inputMode = 'decimal';
      input.value = field.type === 'secret' ? '' : (value ?? '');
      if (field.type === 'secret') input.placeholder = field.set ? '(설정됨 — 바꾸려면 입력)' : '(비어 있음)';
      else if (field.hint) input.placeholder = field.hint;
      input.maxLength = 500;
      input.addEventListener('keydown', (e) => e.stopPropagation());   // 글자 입력 중 Esc/단축키가 창을 닫지 않게
      this.readers.set(field.path, () => input.value);
    }
    input.addEventListener('input', () => { this.msg[this.tab] = ''; });
    row.append(name, input);
    return row;
  }

  // 색 입력: 색 상자 + R G B 숫자(0~255) + 지우기. 손대지 않았으면 비어 있는 채로(= 기본 색) 둔다
  _colorInput(value, field) {
    const wrap = this._el('span', 'st-color');
    const hex = /^#[0-9a-fA-F]{6}$/.test(String(value || '')) ? String(value).toLowerCase() : '';
    let cur = hex;   // '' = 기본 색
    const pick = this._el('input'); pick.type = 'color'; pick.value = hex || '#ffffff';
    const nums = ['R', 'G', 'B'].map((n) => {
      const i = this._el('input'); i.type = 'text'; i.inputMode = 'numeric'; i.maxLength = 3; i.placeholder = n; i.title = n;
      i.addEventListener('keydown', (e) => e.stopPropagation());
      return i;
    });
    const clear = this._el('button', '', '지우기'); clear.type = 'button';
    const hex2 = (n) => n.toString(16).padStart(2, '0');
    const paint = () => {
      if (!cur) { nums.forEach((n) => { n.value = ''; }); return; }
      const v = parseInt(cur.slice(1), 16);
      [(v >> 16) & 255, (v >> 8) & 255, v & 255].forEach((x, k) => { nums[k].value = String(x); });
    };
    pick.addEventListener('input', () => { cur = pick.value.toLowerCase(); paint(); this.msg[this.tab] = ''; });
    const fromNums = () => {
      const vals = nums.map((n) => n.value.trim());
      if (vals.every((v) => v === '')) { cur = ''; return; }
      const ns = vals.map((v) => Math.max(0, Math.min(255, Math.round(Number(v) || 0))));
      cur = `#${ns.map(hex2).join('')}`; pick.value = cur;
    };
    nums.forEach((n) => n.addEventListener('input', () => { fromNums(); this.msg[this.tab] = ''; }));
    clear.addEventListener('click', (e) => { e.preventDefault(); cur = ''; pick.value = '#ffffff'; paint(); this.msg[this.tab] = ''; });
    paint();
    wrap.append(pick, ...nums, clear);
    this.readers.set(field.path, () => cur);
    return wrap;
  }

  // fields 의 입력값을 읽어 바뀐 것만 {경로: 값} 으로. 잘못된 값이 있으면 Error.
  _collect(fields, initialOf) {
    const changes = {};
    for (const f of fields) {
      const read = this.readers.get(f.path);
      if (!read) continue;
      const raw = read();
      if (f.type === 'secret') { if (String(raw).trim() !== '') changes[f.path] = String(raw).trim(); continue; }
      const next = coerceField(f, raw);
      const before = initialOf(f);
      let same = next === before;
      if (!same && (f.type === 'int' || f.type === 'float')) same = Number(before) === next;
      if (!same && (f.type === 'str' || f.type === 'color')) same = String(before ?? '') === next;
      if (!same) changes[f.path] = next;
    }
    return changes;
  }

  _footer(msg, buttons) {
    const m = this._el('div', 'st-msg', msg || '');
    const row = this._el('div', 'st-btns');
    row.append(...buttons);
    this.footEl.replaceChildren(m, row);
  }

  _button(text, onClick, cls = '') {
    const b = this._el('button', cls, text);
    b.addEventListener('click', onClick);
    return b;
  }

  // ---------- 모델 탭: 이 모델의 persona / 감정 태그 <-> 표정 ----------
  _renderModel() {
    const info = (this.h.getProfile && this.h.getProfile()) || null;
    if (!info || !info.model) {
      this.bodyEl.replaceChildren(this._el('div', 'st-empty', '모델을 불러온 뒤에 쓸 수 있어요.'));
      this._footer(this.msg.model, []);
      return;
    }
    if (!this.draft || this.draft.model !== info.model) {   // 모델이 바뀌면 그 모델의 값으로 다시 시작
      this.draft = { model: info.model, persona: info.persona || '', rows: (info.rows || []).map((r) => ({ tag: r.tag, expression: r.expression, desc: r.desc || '' })), voice: { ...Object.fromEntries(VOICE_FIELDS.map((f) => [f.key, ''])), ...(info.voice || {}) }, lyricsPose: { expression: '', motion: '', ...(info.lyricsPose || {}) } };
    }
    const d = this.draft;
    const exprs = info.expressions || [];
    const touch = () => { this.msg.model = ''; };

    const persona = this._el('textarea', 'st-persona');
    persona.value = d.persona;
    persona.rows = 8;
    persona.placeholder = '(비어 있으면 서버의 persona.txt 를 써요)';
    persona.addEventListener('keydown', (e) => e.stopPropagation());
    persona.addEventListener('input', () => { d.persona = persona.value; touch(); });
    this.personaInput = persona;

    const rows = [
      this._group(`모델: ${d.model}`),
      this._el('div', 'st-note', '이 모델 폴더(models/모델이름/)의 persona.txt, emotionAliases.json 에 저장돼요. 모델을 바꾸면 그 모델의 설정이 적용돼요.'),
      this._group('캐릭터 설정 (persona.txt)'),
      persona,
      this._el('div', 'st-note', '사용자 호칭과 캐릭터 이름도 여기에 직접 적어 주세요.'),
      this._group('음성 (GPT-SoVITS · 속도는 Edge 에도 적용)'),
      this._el('div', 'st-note', '서버 PC 기준 경로예요 (상대 경로는 GPT-SoVITS 폴더 기준). 비워 둔 칸은 서버 설정의 값을 써요. 가중치가 바뀌면 처음 말할 때 몇 초 걸릴 수 있어요. 가중치를 안 적은 모델은 이전 모델의 가중치가 남을 수 있으니, 기본 가중치는 서버 설정 탭에 적어 두세요.'),
      ...VOICE_FIELDS.map((f) => this._voiceRow(d, f, touch)),
      this._group('감정 태그 ↔ 표정 (emotionAliases)'),
      this._el('div', 'st-note', 'AI 는 여기 있는 태그 중에서 감정을 골라요(neutral 은 항상 포함). 설명은 AI 에게 주는 힌트예요. 표정이 하나도 연결되지 않으면 서버 기본 감정을 써요.'),
    ];
    d.rows.forEach((r, i) => rows.push(this._emotionRow(d, r, i, exprs, touch)));
    rows.push(this._button('＋ 감정 추가', () => { d.rows.push({ tag: '', expression: '', desc: '' }); touch(); this.render(); }));
    const lists = info.poseLists || { expressions: [], motions: [] };
    rows.push(this._group('가사창 표정/모션 (가사가 나올 때만)'));
    rows.push(this._el('div', 'st-note', '노래가 재생되며 가사창이 떠 있는 동안만 쓰는 표정/모션이에요. 대사의 감정으로는 쓰이지 않고 AI 에게도 알리지 않아요. 모션은 가사창이 뜰 때 한 번 재생되고, 가사가 멈추고 몇 초 뒤에는 중립 표정으로 돌아와요. 비워 두면 쓰지 않아요. 위 감정 태그에 연결한 표정과는 겹치게 쓸 수 없어요.'));
    rows.push(this._poseRow(d, 'expression', '표정', lists.expressions, touch));
    rows.push(this._poseRow(d, 'motion', '모션', lists.motions, touch));
    this._wireGroups(rows);
    this.bodyEl.replaceChildren(...rows);
    this._footer(this.msg.model, [
      this._button('되돌리기', () => { this.draft = null; this.msg.model = ''; this.render(); }),
      this._button('저장', () => this._saveModel(), 'primary'),
    ]);
  }

  // 가사창 전용 표정/모션 한 줄 (이름 고르기 + 미리보기)
  _poseRow(d, key, label, names, touch) {
    const row = this._el('div', 'st-row');
    const name = this._el('span', 'st-label', label);
    const sel = this._el('select', 'st-emoexpr');
    const want = d.lyricsPose[key] ? cleanName(d.lyricsPose[key]) : '';
    const blank = this._el('option', '', '(사용 안 함)'); blank.value = ''; sel.append(blank);
    const all = want && !names.includes(want) ? [want, ...names] : names;   // 파일에는 있는데 모델에 없는 이름도 보여 준다
    for (const n of all) { const o = this._el('option', '', n === want && !names.includes(n) ? `⚠ ${n} (없음)` : n); o.value = n; sel.append(o); }
    sel.value = want;
    sel.addEventListener('change', () => { d.lyricsPose[key] = sel.value; touch(); });
    const play = this._el('button', 'st-ib', '▶');
    play.title = `이 ${label} 미리보기`;
    play.addEventListener('click', () => { if (sel.value && this.h.onPreviewEmotion) this.h.onPreviewEmotion(sel.value); });
    const box = this._el('div', 'st-poseline');
    box.append(sel, play);
    row.append(name, box);
    return row;
  }

  _voiceRow(d, f, touch) {
    const row = this._el('label', 'st-row');
    const name = this._el('span', 'st-label', f.label);
    const input = this._el('input');
    input.type = 'text'; input.value = d.voice[f.key] || ''; input.placeholder = f.hint; input.maxLength = 500;
    input.addEventListener('keydown', (e) => e.stopPropagation());
    input.addEventListener('input', () => { d.voice[f.key] = input.value; touch(); });
    row.append(name, input);
    return row;
  }

  _emotionRow(d, r, i, exprs, touch) {
    const box = this._el('div', 'st-emo');
    const tag = this._el('input', 'st-emotag');
    tag.type = 'text'; tag.value = r.tag; tag.placeholder = '태그'; tag.maxLength = 20;
    tag.addEventListener('keydown', (e) => e.stopPropagation());
    tag.addEventListener('input', () => { r.tag = tag.value.trim().toLowerCase(); touch(); });

    const sel = this._el('select', 'st-emoexpr');
    const want = r.expression ? cleanName(r.expression) : '';
    const blank = this._el('option', '', '(표정 선택)'); blank.value = ''; sel.append(blank);
    const names = want && !exprs.includes(want) ? [want, ...exprs] : exprs;   // 파일에는 있는데 모델에 없는 이름도 보여 준다
    for (const n of names) { const o = this._el('option', '', n === want && !exprs.includes(n) ? `⚠ ${n} (없음)` : n); o.value = n; sel.append(o); }
    sel.value = want;
    sel.addEventListener('change', () => { r.expression = sel.value; touch(); });

    const play = this._el('button', 'st-ib', '▶');
    play.title = '이 표정 미리보기';
    play.addEventListener('click', () => { if (sel.value && this.h.onPreviewEmotion) this.h.onPreviewEmotion(sel.value); });
    const del = this._el('button', 'st-ib', '🗑');
    del.title = '이 감정 지우기 (저장해야 적용돼요)';
    del.addEventListener('click', () => { d.rows.splice(i, 1); touch(); this.render(); });

    const line1 = this._el('div', 'st-emoline');
    line1.append(tag, sel, play, del);
    const desc = this._el('input', 'st-emodesc');
    desc.type = 'text'; desc.value = r.desc || ''; desc.placeholder = 'AI 에게 알려 줄 설명 (생략 가능, 예: 기쁨, 즐거움)'; desc.maxLength = 60;
    desc.addEventListener('keydown', (e) => e.stopPropagation());
    desc.addEventListener('input', () => { r.desc = desc.value; touch(); });
    box.append(line1, desc);
    return box;
  }

  async _saveModel() {
    const d = this.draft;
    if (!d) return;
    const done = (msg) => { this.msg.model = msg; this._footer(msg, [
      this._button('되돌리기', () => { this.draft = null; this.msg.model = ''; this.render(); }),
      this._button('저장', () => this._saveModel(), 'primary')]); };
    const rows = d.rows.filter((r) => r.tag || r.expression || r.desc);   // 빈 줄은 무시
    const seen = new Set();
    for (const r of rows) {
      if (!TAG_RE.test(r.tag)) { done(`감정 태그는 영어/한글 글자만 쓸 수 있어요 (1~20자): "${r.tag}"`); return; }
      if (seen.has(r.tag)) { done(`감정 태그가 겹쳐요: "${r.tag}"`); return; }
      if (!r.expression) { done(`"${r.tag}" 에 연결할 표정을 골라 주세요`); return; }
      seen.add(r.tag);
    }
    for (const [k, label] of [['expression', '표정'], ['motion', '모션']]) {   // 감정 태그에 연결한 것과 같으면 대사 중에도 나올 수 있다
      const n = d.lyricsPose[k] ? cleanName(d.lyricsPose[k]) : '';
      const hit = n && rows.find((r) => r.expression && cleanName(r.expression) === n);
      if (hit) { done(`가사창 ${label} "${n}" 은 감정 "${hit.tag}" 에도 연결돼 있어요. 대사 중에 쓰이지 않게 다른 걸 골라 주세요`); return; }
    }
    for (const f of VOICE_FIELDS) { if (d.voice[f.key] && d.voice[f.key].length > 500) { done(`"${f.label}" 이 너무 길어요 (최대 500자)`); return; } }
    if (d.voice.prompt_lang && !LANG_RE.test(d.voice.prompt_lang.trim())) { done('참조 음성 언어는 ko / ja / en / zh 같은 언어 코드로 적어 주세요'); return; }
    if (d.voice.speed_factor && d.voice.speed_factor.trim()) {
      const sp = Number(d.voice.speed_factor.trim());
      if (!Number.isFinite(sp) || sp < 0.5 || sp > 2) { done('말하는 속도는 0.5 ~ 2.0 사이 숫자로 적어 주세요 (1.0 = 보통)'); return; }
    }
    done('저장하는 중…');
    let res;
    try { res = await (this.h.onSaveProfile && this.h.onSaveProfile(d.model, { persona: d.persona, rows, voice: d.voice, lyricsPose: d.lyricsPose })); }
    catch (e) { res = { ok: false, error: String((e && e.message) || e) }; }
    if (!res || !res.ok) { done((res && res.error) || '저장하지 못했어요.'); return; }
    this.draft = null;
    this.msg.model = '저장했어요. 바로 적용됐어요.';
    this.render();
  }

  // ---------- 단축키 탭: 동작별로 키를 눌러 정한다 (전역 단축키, config.json 의 hotkeys) ----------
  _loadHotkeys() {
    if (this.hkLoading || !this.h.loadHotkeys) { if (!this.h.loadHotkeys) this.hkError = '단축키를 쓰려면 앱을 다시 시작해 주세요.'; return; }
    this.hkLoading = true;
    Promise.resolve(this.h.loadHotkeys()).then((r) => {
      const defaults = (r && r.defaults) || {};
      const saved = effectiveMap(defaults, r && r.overrides);
      this.hk = { defaults, saved, draft: { ...saved }, status: (r && r.status) || {} };
    }).catch((e) => { this.hkError = `단축키를 불러오지 못했어요: ${(e && e.message) || e}`; })
      .finally(() => { this.hkLoading = false; if (this.isOpen && this.tab === 'hotkeys') this.render(); });
  }

  _hkButtons() {
    return [
      this._button('모두 기본값', () => { this._stopRecord(false); this.hk.draft = effectiveMap(this.hk.defaults, {}); this.msg.hotkeys = ''; this.render(); }),
      this._button('되돌리기', () => { this._stopRecord(false); this.hk.draft = { ...this.hk.saved }; this.msg.hotkeys = ''; this.render(); }),
      this._button('저장', () => this._saveHotkeys(), 'primary'),
    ];
  }

  _renderHotkeys() {
    if (!this.hk) {
      this.bodyEl.replaceChildren(this._el('div', 'st-empty', this.hkError || '불러오는 중…'));
      this._footer(this.msg.hotkeys, []);
      if (!this.hkLoading && !this.hkError) this._loadHotkeys();
      return;
    }
    const rows = [this._el('div', 'st-note', '키 칸을 누른 뒤 원하는 단축키를 누르면 바뀌어요 (Esc 취소 · Backspace/Delete 는 비우기). 저장해야 적용돼요. Ctrl/Alt 없이도 F1~F24, Insert, Home, End, PageUp/PageDown, PrintScreen, ScrollLock, 숫자패드 키는 혼자 쓸 수 있어요 (글자/숫자/방향키/Space/Enter 는 다른 프로그램 입력을 막아서 안 돼요). 앱이나 시스템이 쓰는 조합(Ctrl+C 등)과 다른 동작에 쓴 키는 쓸 수 없어요.')];
    let last = null;
    for (const a of HOTKEY_ACTIONS) {
      if (a.group !== last) { rows.push(this._group(a.group)); last = a.group; }
      rows.push(this._hotkeyRow(a));
    }
    this._wireGroups(rows);
    this.bodyEl.replaceChildren(...rows);
    this._footer(this.msg.hotkeys, this._hkButtons());
  }

  _hotkeyRow(a) {
    const { defaults, saved, draft, status } = this.hk;
    const cur = draft[a.id];
    const row = this._el('div', 'st-hk');
    const name = this._el('span', 'st-label', a.label);
    const changed = normAccel(cur) !== normAccel(saved[a.id]);
    const btn = this._el('button', `st-key${cur ? '' : ' empty'}${changed ? ' changed' : ''}`, displayAccel(cur));
    btn.title = '눌러서 새 단축키를 정해요';
    btn.addEventListener('click', () => this._startRecord(a.id, btn));
    const def = this._el('button', 'st-ib', '↺');
    def.title = `기본값으로 (${displayAccel(firstOf(defaults[a.id]))})`;
    def.addEventListener('click', () => { this._stopRecord(false); draft[a.id] = firstOf(defaults[a.id]); this.msg.hotkeys = ''; this.render(); });
    const clr = this._el('button', 'st-ib', '✕');
    clr.title = '이 동작은 단축키를 쓰지 않아요';
    clr.addEventListener('click', () => { this._stopRecord(false); draft[a.id] = ''; this.msg.hotkeys = ''; this.render(); });
    row.append(name, btn, def, clr);
    const st = status[a.id];
    if (st && st.ok === false && !changed) {
      const w = this._el('span', 'st-hkwarn', '⚠');
      w.title = '등록하지 못했어요 (다른 프로그램이 쓰는 키일 수 있어요)';
      row.append(w);
    }
    return row;
  }

  _startRecord(id, btn) {
    this._stopRecord(false);
    this.recording = { id, btn };
    btn.classList.add('recording');
    btn.textContent = '키를 누르세요…';
    if (this.h.onHotkeyPause) this.h.onHotkeyPause(true);   // 이미 등록된 단축키가 같이 동작하지 않게 잠시 풀어 둔다
    this._recKey = (ev) => this._onRecordKey(ev);
    this._recBlur = () => this._stopRecord(true);
    this.doc.addEventListener('keydown', this._recKey, true);
    const w = this.doc.defaultView;
    if (w && w.addEventListener) w.addEventListener('blur', this._recBlur);
  }

  _stopRecord(rerender) {
    if (!this.recording) return;
    this.recording = null;
    this.doc.removeEventListener('keydown', this._recKey, true);
    const w = this.doc.defaultView;
    if (w && w.removeEventListener) w.removeEventListener('blur', this._recBlur);
    if (this.h.onHotkeyPause) this.h.onHotkeyPause(false);
    if (rerender && this.isOpen && this.tab === 'hotkeys') this.render();
  }

  _onRecordKey(ev) {
    if (!this.recording) return;
    if (ev.preventDefault) ev.preventDefault();
    if (ev.stopPropagation) ev.stopPropagation();
    if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
    if (ev.repeat) return;
    const { id, btn } = this.recording;
    const bare = !ev.ctrlKey && !ev.altKey && !ev.shiftKey && !ev.metaKey;
    if (ev.code === 'Escape' && bare) { this._stopRecord(true); return; }
    if ((ev.code === 'Backspace' || ev.code === 'Delete') && bare) { this.hk.draft[id] = ''; this.msg.hotkeys = ''; this._stopRecord(true); return; }
    const { mods, key } = readKeyEvent(ev);
    if (!key) { btn.textContent = mods.length ? `${displayAccel(joinAccel(mods, ''))} + …` : '키를 누르세요…'; return; }   // 보조키만 눌린 중
    const accel = joinAccel(mods, key);
    const why = checkAccel(accel);
    if (why) { this.msg.hotkeys = why; this._footer(why, this._hkButtons()); return; }   // 계속 눌러 볼 수 있게 대기 상태를 유지
    this.hk.draft[id] = accel;
    this.msg.hotkeys = '';
    this._stopRecord(true);
  }

  async _saveHotkeys() {
    const hk = this.hk;
    if (!hk) return;
    this._stopRecord(false);
    const draft = hk.draft;
    const label = (id) => (HOTKEY_ACTIONS.find((a) => a.id === id) || {}).label || id;
    const fail = (msg) => { this.msg.hotkeys = msg; this._footer(msg, this._hkButtons()); };
    for (const a of HOTKEY_ACTIONS) {
      const why = checkAccel(draft[a.id]);
      if (why) { fail(`'${a.label}': ${why}`); return; }
    }
    const conflicts = findConflicts(draft);
    if (conflicts.length) { fail(`'${label(conflicts[0][0])}' 와 '${label(conflicts[0][1])}' 에 같은 키가 지정돼 있어요`); return; }
    fail('저장하는 중…');
    let res;
    try { res = await (this.h.onSaveHotkeys && this.h.onSaveHotkeys(buildOverrides(hk.defaults, draft))); }
    catch (e) { res = { ok: false, error: String((e && e.message) || e) }; }
    if (!res || !res.ok) { fail((res && res.error) || '저장하지 못했어요.'); return; }
    hk.status = res.status || {};
    hk.saved = { ...draft };
    const failed = HOTKEY_ACTIONS.filter((a) => hk.status[a.id] && hk.status[a.id].ok === false).map((a) => a.label);
    this.msg.hotkeys = failed.length ? `저장했어요. 다만 등록하지 못한 키가 있어요 (다른 프로그램이 쓰는 키일 수 있어요): ${failed.join(', ')}` : '저장했어요. 바로 적용됐어요.';
    this.render();
  }

  _renderClient() {
    const config = (this.h.getClientConfig && this.h.getClientConfig()) || {};
    const rows = [];
    let group = null;
    for (const f of CLIENT_FIELDS) {
      if (f.group !== group) { group = f.group; rows.push(this._group(group)); }
      rows.push(this._fieldRow(f, currentValue(f, config), null));
    }
    this._wireGroups(rows);
    rows.push(this._el('div', 'st-note', '↻ 표시 항목은 저장 후 앱을 다시 시작해야 적용돼요. 나머지는 바로 적용돼요.'));
    this.bodyEl.replaceChildren(...rows);
    this._clientFooter();
  }

  _clientFooter() {
    const btns = [this._button('저장', () => this._saveClient(false), 'primary')];
    if (this.restartNeeded) btns.unshift(this._button('지금 다시 시작', () => this.h.onRelaunch && this.h.onRelaunch()));
    this._footer(this.msg.client, btns);
  }

  _saveClient() {
    const config = (this.h.getClientConfig && this.h.getClientConfig()) || {};
    try {
      const changes = this._collect(CLIENT_FIELDS, (f) => currentValue(f, config));
      const minV = changes['proactive.minMinutes'] ?? currentValue(CLIENT_FIELDS.find((f) => f.path === 'proactive.minMinutes'), config);
      const maxV = changes['proactive.maxMinutes'] ?? currentValue(CLIENT_FIELDS.find((f) => f.path === 'proactive.maxMinutes'), config);
      if (maxV < minV) throw new Error('먼저 말 걸기의 최대 간격은 최소 간격보다 작을 수 없어요');
      const paths = Object.keys(changes);
      if (!paths.length) { this.msg.client = '바뀐 게 없어요.'; this._clientFooter(); return; }
      const restart = paths.filter((p) => CLIENT_FIELDS.find((f) => f.path === p).restart);
      this.restartNeeded = restart.length > 0;
      this.msg.client = restart.length
        ? `저장했어요. 다시 시작해야 적용되는 항목: ${restart.map((p) => CLIENT_FIELDS.find((f) => f.path === p).label).join(', ')}`
        : '저장했어요. 바로 적용됐어요.';
      this.h.onSaveClient && this.h.onSaveClient(buildPatch(config, changes), this.restartNeeded);
      this._clientFooter();
    } catch (e) {
      this.msg.client = e.message;
      this._clientFooter();
    }
  }

  _renderMemory() {
    const rows = [this._el('div', 'st-note', '캐릭터가 대화에서 알아낸 사실이에요. 몇 번 대화할 때마다 자동으로 정리되고, 여기서 직접 고치거나 지울 수 있어요.')];
    if (this.memories === null) rows.push(this._el('div', 'st-empty', '불러오는 중…'));
    else if (!this.memories.length) rows.push(this._el('div', 'st-empty', '아직 기억하는 게 없어요.'));
    else for (const m of this.memories) rows.push(this._memoryItem(m));
    this.bodyEl.replaceChildren(...rows);

    const add = this._el('input', 'st-add');
    add.placeholder = '직접 기억시키기 (예: 나는 고양이를 키워)';
    add.maxLength = 200;
    const submit = () => { const t = add.value.trim(); if (t && this.h.onMemoryAdd) { this.msg.memory = ''; this.h.onMemoryAdd(t); add.value = ''; } };
    add.addEventListener('keydown', (e) => { e.stopPropagation(); if (e.key === 'Enter' && !e.isComposing) submit(); });
    this.addInput = add;
    const clear = this._button(this.confirmKey === 'clear' ? '정말 전부?' : '전체 지우기', () => {
      if (!this.memories || !this.memories.length) return;
      if (this.confirmKey === 'clear') { this._clearConfirm(); this.h.onMemoryClear && this.h.onMemoryClear(); return; }
      this._arm('clear'); this.render();
    }, this.confirmKey === 'clear' ? 'danger' : '');
    clear.disabled = !this.memories || !this.memories.length;
    this.clearBtn = clear;
    const line = this._el('div', 'st-addrow');
    line.append(add, this._button('추가', submit, 'primary'));
    this.footEl.replaceChildren(this._el('div', 'st-msg', this.msg.memory || ''), line, (() => { const r = this._el('div', 'st-btns'); r.append(clear); return r; })());
  }

  _memoryItem(m) {
    const row = this._el('div', 'st-mem');
    if (this.editingId === m.id) {
      const inp = this._el('input', 'st-memedit');
      inp.value = m.text;
      inp.maxLength = 200;
      let done = false;
      const finish = (commit) => {
        if (done) return;
        done = true;
        this.editingId = null;
        const v = inp.value.trim();
        if (commit && v && v !== m.text) this.h.onMemoryUpdate && this.h.onMemoryUpdate(m.id, v);
        this.render();
      };
      inp.addEventListener('keydown', (e) => {
        e.stopPropagation();
        if (e.key === 'Enter' && !e.isComposing) finish(true);
        else if (e.key === 'Escape') finish(false);
      });
      inp.addEventListener('blur', () => finish(true));
      row.append(inp);
      setTimeout(() => { inp.focus(); inp.select(); }, 0);
      return row;
    }
    const confirming = this.confirmKey === `m:${m.id}`;
    const edit = this._el('button', 'st-ib', '✎');
    edit.title = '고치기';
    edit.addEventListener('click', () => { this._clearConfirm(); this.editingId = m.id; this.render(); });
    const del = this._el('button', `st-ib${confirming ? ' danger' : ''}`, confirming ? '삭제?' : '🗑');
    del.title = confirming ? '한 번 더 누르면 지워요' : '이 기억 지우기';
    del.addEventListener('click', () => {
      if (this.confirmKey === `m:${m.id}`) { this._clearConfirm(); this.h.onMemoryDelete && this.h.onMemoryDelete(m.id); return; }
      this._arm(`m:${m.id}`); this.render();
    });
    row.append(this._el('span', 'st-memtext', m.text), edit, del);
    return row;
  }
}
