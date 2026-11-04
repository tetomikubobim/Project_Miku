// 단축키 탭에서 쓰는 목록과 순수 함수. 실제 등록은 메인 프로세스(main.js 의 registerHotkeys)가 하고, 여기서는 이름/표시/키 입력 해석/검사만 한다.
// 설정 값은 config.json 의 hotkeys: { 동작이름: "Ctrl+Alt+H" } — 적은 것만 기본값을 덮어쓰고, ""(빈 글자)는 그 동작의 단축키를 쓰지 않음.

// 동작 목록 (id 는 main.js DEFAULT_HOTKEYS 의 키와 같다)
export const HOTKEY_ACTIONS = [
  { id: 'toggleModel', label: '모델 보이기/숨기기', group: '화면' },
  { id: 'toggleBubble', label: '대사창 켜기/끄기', group: '화면' },
  { id: 'toggleClickThrough', label: '모델 뒤 클릭 가능 켜기/끄기', group: '화면' },
  { id: 'toggleSessions', label: '대화 목록 열기/닫기', group: '화면' },
  { id: 'capturePartial', label: '부분 캡처 (입력창에 첨부)', group: '화면 보기' },
  { id: 'captureFull', label: '전체 화면 캡처 (입력창에 첨부)', group: '화면 보기' },
  { id: 'toggleScreenShare', label: '화면 공유 모드 켜기/끄기', group: '화면 보기' },
  { id: 'toggleMic', label: '마이크 듣기 시작/중지', group: '기능' },
  { id: 'toggleProactive', label: '먼저 말 걸기 켜기/끄기', group: '기능' },
  { id: 'toggleGaze', label: '마우스 따라보기 켜기/끄기', group: '기능' },
  { id: 'toggleLyrics', label: '실시간 가사 켜기/끄기', group: '가사' },
  { id: 'lyricsEarlier', label: '가사 싱크 +0.5초 (더 일찍)', group: '가사' },
  { id: 'lyricsLater', label: '가사 싱크 -0.5초 (더 늦게)', group: '가사' },
  { id: 'lyricsReset', label: '가사 싱크 보정 초기화', group: '가사' },
  { id: 'lyricsPick', label: '가사 직접 고르기 열기', group: '가사' },
];

const MODS = { commandorcontrol: 'CommandOrControl', cmdorctrl: 'CommandOrControl', control: 'CommandOrControl', ctrl: 'CommandOrControl', command: 'CommandOrControl', cmd: 'CommandOrControl', alt: 'Alt', option: 'Alt', altgr: 'AltGr', shift: 'Shift', super: 'Super', meta: 'Super' };
const MOD_ORDER = ['CommandOrControl', 'Alt', 'AltGr', 'Shift', 'Super'];

// 키보드 이벤트의 code -> Electron 단축키 글자. 알 수 없으면 ''.
const PUNCT = { Minus: '-', Equal: '=', BracketLeft: '[', BracketRight: ']', Backslash: '\\', Semicolon: ';', Quote: "'", Backquote: '`', Comma: ',', Period: '.', Slash: '/' };
const NAMED = {
  Space: 'Space', Enter: 'Return', NumpadEnter: 'Return', Tab: 'Tab', Backspace: 'Backspace', Delete: 'Delete', Insert: 'Insert',
  Home: 'Home', End: 'End', PageUp: 'PageUp', PageDown: 'PageDown', ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
  PrintScreen: 'PrintScreen', ScrollLock: 'Scrolllock',
  NumpadAdd: 'numadd', NumpadSubtract: 'numsub', NumpadMultiply: 'nummult', NumpadDivide: 'numdiv', NumpadDecimal: 'numdec',
};
export function keyFromCode(code) {
  const c = String(code || '');
  let m;
  if ((m = /^Key([A-Z])$/.exec(c))) return m[1];
  if ((m = /^Digit([0-9])$/.exec(c))) return m[1];
  if ((m = /^F([1-9]|1[0-9]|2[0-4])$/.exec(c))) return `F${m[1]}`;
  if ((m = /^Numpad([0-9])$/.exec(c))) return `num${m[1]}`;
  return NAMED[c] || PUNCT[c] || '';
}
const isModifierCode = (code) => /^(Control|Shift|Alt|Meta)(Left|Right)$/.test(String(code || '')) || code === 'AltGraph';
// 보조키 없이 단독으로 등록할 수 있는 키. 전역 단축키는 등록한 키를 다른 프로그램에서 못 쓰게 가로채니까,
// 타자/커서 이동/확인·취소에 쓰는 키(글자, 숫자, 방향키, Space, Enter, Esc, Tab, Backspace, Delete, 기호)는 단독으로는 안 된다.
const STANDALONE = /^(F([1-9]|1[0-9]|2[0-4])|Insert|Home|End|PageUp|PageDown|PrintScreen|Scrolllock|num[0-9]|numadd|numsub|nummult|numdiv|numdec)$/i;
const STANDALONE_HELP = 'F1~F24, Insert, Home, End, PageUp, PageDown, PrintScreen, ScrollLock, 숫자패드 키';

// 이 조합은 앱 안(입력창 편집)이나 시스템이 이미 쓰고 있어서 전역 단축키로 가로채면 안 된다. 비교용 글자(normAccel) -> 쓰이는 곳
const RESERVED = new Map([
  ['commandorcontrol+c', '복사 (앱 입력창)'], ['commandorcontrol+v', '붙여넣기 (앱 입력창)'], ['commandorcontrol+x', '잘라내기 (앱 입력창)'],
  ['commandorcontrol+a', '전체 선택 (앱 입력창)'], ['commandorcontrol+z', '실행 취소 (앱 입력창)'], ['commandorcontrol+y', '다시 실행 (앱 입력창)'],
  ['alt+f4', '창 닫기 (시스템)'], ['alt+tab', '창 전환 (시스템)'], ['commandorcontrol+w', '탭/창 닫기'], ['commandorcontrol+tab', '탭 전환'],
  ['commandorcontrol+shift+escape', '작업 관리자 (시스템)'], ['commandorcontrol+alt+delete', '보안 화면 (시스템)'], ['super+l', '화면 잠금 (시스템)'], ['super+d', '바탕화면 보기 (시스템)'], ['super+tab', '작업 보기 (시스템)'],
]);
const KEY_RE = /^([A-Za-z0-9]|F([1-9]|1[0-9]|2[0-4])|Space|Tab|Backspace|Delete|Insert|Return|Enter|Home|End|PageUp|PageDown|Up|Down|Left|Right|PrintScreen|Scrolllock|Plus|num[0-9]|numadd|numsub|nummult|numdiv|numdec|[-=\[\]\\;',./`])$/i;

// 키를 눌렀을 때: { mods: ['CommandOrControl', ...], key: 'H' | '' (보조키만 눌린 중) }
export function readKeyEvent(ev) {
  const mods = [];
  if (ev.ctrlKey) mods.push('CommandOrControl');
  if (ev.altKey) mods.push('Alt');
  if (ev.shiftKey) mods.push('Shift');
  if (ev.metaKey) mods.push('Super');
  const key = isModifierCode(ev.code) ? '' : keyFromCode(ev.code);
  return { mods, key };
}

// 보조키들 + 키 -> "CommandOrControl+Alt+H"
export function joinAccel(mods, key) {
  const ordered = MOD_ORDER.filter((m) => mods.includes(m));
  return [...ordered, key].filter(Boolean).join('+');
}

// 단축키 글자 -> 검사. 올바르면 '' 아니면 사용자에게 보여 줄 이유.
export function checkAccel(accel) {
  const s = String(accel ?? '').trim();
  if (!s) return '';   // 비움 = 쓰지 않음
  const parts = s.split('+').map((p) => p.trim());
  if (parts.some((p) => !p)) return '단축키 모양이 올바르지 않아요';
  const key = parts[parts.length - 1];
  const mods = parts.slice(0, -1);
  for (const m of mods) if (!MODS[m.toLowerCase()]) return `알 수 없는 보조키예요: ${m}`;
  if (!KEY_RE.test(key)) return `이 키는 단축키로 쓸 수 없어요: ${key}`;
  const real = mods.filter((m) => !/^shift$/i.test(m));   // Shift 만으로는 타자와 구분이 안 된다
  if (!real.length && !STANDALONE.test(key)) return `이 키를 혼자 쓰면 다른 프로그램에서 입력이 안 돼요. Ctrl / Alt 를 같이 누르거나, 혼자 쓸 수 있는 키(${STANDALONE_HELP})를 쓰세요`;
  const used = RESERVED.get(normAccel(s));
  if (used) return `'${displayAccel(s)}' 는 ${used} 에 쓰는 키라 단축키로 쓸 수 없어요`;
  return '';
}

// 비교용: 보조키 순서/별칭(Ctrl, Control, CmdOrCtrl ...)/대소문자를 통일
export function normAccel(accel) {
  const s = String(accel ?? '').trim();
  if (!s) return '';
  const parts = s.split('+').map((p) => p.trim());
  const key = parts.pop().toLowerCase();
  const mods = parts.map((m) => MODS[m.toLowerCase()] || m);
  return [...MOD_ORDER.filter((m) => mods.includes(m)), key].join('+').toLowerCase();
}

// 화면에 보일 글자 ("CommandOrControl" -> "Ctrl")
export function displayAccel(accel) {
  const s = String(accel ?? '').trim();
  if (!s) return '(사용 안 함)';
  return s.split('+').map((p) => (MODS[p.trim().toLowerCase()] === 'CommandOrControl' ? 'Ctrl' : p.trim())).join(' + ');
}

export const firstOf = (v) => (Array.isArray(v) ? (v[0] || '') : String(v ?? ''));

// 기본값 + 사용자가 바꾼 것 -> 동작별 지금 단축키 { id: "글자" }. 사용자가 비운("") 것은 ''
export function effectiveMap(defaults, overrides) {
  const out = {};
  for (const a of HOTKEY_ACTIONS) {
    out[a.id] = overrides && Object.prototype.hasOwnProperty.call(overrides, a.id) ? String(overrides[a.id] ?? '') : firstOf(defaults && defaults[a.id]);
  }
  return out;
}

// 화면에서 고친 값 -> config.hotkeys 에 저장할 것(기본값과 같은 건 뺀다)
export function buildOverrides(defaults, draft) {
  const out = {};
  for (const a of HOTKEY_ACTIONS) {
    const v = String(draft[a.id] ?? '').trim();
    if (normAccel(v) !== normAccel(firstOf(defaults && defaults[a.id]))) out[a.id] = v;
  }
  return out;
}

// 같은 키를 두 동작에 쓰면 하나만 등록되니 저장 전에 알려 준다. [[idA, idB], ...]
export function findConflicts(map) {
  const seen = new Map(); const out = [];
  for (const a of HOTKEY_ACTIONS) {
    const n = normAccel(map[a.id]);
    if (!n) continue;
    if (seen.has(n)) out.push([seen.get(n), a.id]); else seen.set(n, a.id);
  }
  return out;
}
