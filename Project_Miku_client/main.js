const { app, BrowserWindow, Tray, Menu, ipcMain, screen, nativeImage, globalShortcut, desktopCapturer, session, Notification, powerMonitor, dialog, clipboard } = require('electron');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');
const media = require('./native/media');
const { Scheduler } = require('./native/scheduler');
const { sanitizePatch, effective, intervalLabel } = require('./native/proactive-config');
const profileStore = require('./native/profile');
const { Voice } = require('./native/voice');
const { transcribeRequest } = require('./native/stt-request');
const { LyricsService } = require('./native/lyrics-service');

const ROOT = __dirname;
const CONFIG_PATH = path.join(ROOT, 'config.json');
const MODELS_DIR = path.join(ROOT, 'models');
const SCHEDULE_PATH = path.join(ROOT, 'schedules.json');

let win = null;
let tray = null;

// ---------- config ----------
function readJsonFile(p) {
  const text = fs.readFileSync(p, 'utf8').replace(/^\uFEFF/, '');
  return JSON.parse(text);
}
const DEFAULT_CONFIG_PATH = path.join(ROOT, 'config.default.json');   // 배포 때의 기본 설정 (config.json 이 없거나 깨졌을 때, 그리고 런처의 '기본값으로 초기화' 가 쓴다)
function loadConfig() {
  try { return readJsonFile(CONFIG_PATH); } catch { /* 없거나 깨짐: 기본값 파일로 */ }
  try { return readJsonFile(DEFAULT_CONFIG_PATH); } catch { return {}; }
}
function saveConfig(cfg) {
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2), 'utf8');
}

// ---------- models ----------
// models/<폴더>/ 안에 *.vtube.json 또는 *.model3.json 이 있으면 모델로 인식
function listModels() {
  if (!fs.existsSync(MODELS_DIR)) return [];
  // 폴더 바로 안에 없으면 하위 폴더(최대 2단계)까지 찾는다. 이름은 models/ 바로 아래 폴더 이름.
  const find = (dir, depth) => {
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return null; }
    const vtube = entries.find((f) => f.isFile() && f.name.toLowerCase().endsWith('.vtube.json'));
    const model3 = entries.find((f) => f.isFile() && f.name.toLowerCase().endsWith('.model3.json'));
    if (vtube || model3) {
      return { dir, vtube: vtube ? path.join(dir, vtube.name) : null, model3: model3 ? path.join(dir, model3.name) : null };
    }
    if (depth <= 0) return null;
    for (const d of entries.filter((e) => e.isDirectory())) {
      const r = find(path.join(dir, d.name), depth - 1);
      if (r) return r;
    }
    return null;
  };
  return fs.readdirSync(MODELS_DIR, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => { const r = find(path.join(MODELS_DIR, d.name), 2); return r && { name: d.name, ...r }; })
    .filter(Boolean);
}

// 모델 폴더 안의 표정/모션 파일을 전부 찾아서 model3.json 폴더 기준 상대경로로 돌려준다.
// (파일 이름으로 감정을 자동 연결하는 데 쓴다)
function scanAssetFiles(rootDir, maxDepth = 4) {
  const out = [];
  const walk = (dir, depth) => {
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) { if (depth < maxDepth) walk(full, depth + 1); }
      else if (/\.(exp3|motion3)\.json$/i.test(e.name)) {
        out.push(path.relative(rootDir, full).split(path.sep).join('/'));
      }
    }
  };
  walk(rootDir, 0);
  return out;
}

// model3.json 이 가리키는데 디스크에 없는 표정/모션 항목을 제거한다.
// 파일 이름을 바꿔도 모델 로딩 전체가 실패하지 않게 하기 위함.
function pruneMissing(model3, baseDir) {
  const fr = model3.FileReferences || {};
  const exists = (f) => typeof f === 'string' && fs.existsSync(path.join(baseDir, f));
  let pruned = 0;
  if (Array.isArray(fr.Expressions)) {
    const before = fr.Expressions.length;
    fr.Expressions = fr.Expressions.filter((e) => e && exists(e.File));
    pruned += before - fr.Expressions.length;
  }
  if (fr.Motions && typeof fr.Motions === 'object') {
    for (const g of Object.keys(fr.Motions)) {
      const list = Array.isArray(fr.Motions[g]) ? fr.Motions[g] : [];
      const kept = list.filter((m) => m && exists(m.File));
      pruned += list.length - kept.length;
      if (kept.length) fr.Motions[g] = kept; else delete fr.Motions[g];
    }
  }
  return pruned;
}

function resolveModel(name) {
  const all = listModels();
  const entry = all.find((m) => m.name === name) || all.find((m) => m.name.toLowerCase() === String(name).toLowerCase());
  if (!entry) throw new Error(`모델을 찾을 수 없음: ${name} (models/ 안의 인식된 모델: ${all.map((m) => m.name).join(', ') || '없음'})`);

  let vts = null;
  if (entry.vtube) {
    try { vts = readJsonFile(entry.vtube); } catch (e) { console.warn('vtube.json 파싱 실패', e); }
  }

  // vtube.json 이 가리키는 model3.json 을 우선, 없으면 폴더 안의 model3.json
  let model3Path = entry.model3;
  const ref = vts && vts.FileReferences && vts.FileReferences.Model;
  if (ref) {
    const p = path.join(path.dirname(entry.vtube), ref);
    if (fs.existsSync(p)) model3Path = p;
  }
  if (!model3Path) throw new Error('model3.json 을 찾을 수 없음');

  const model3 = readJsonFile(model3Path);
  const model3Dir = path.dirname(model3Path);
  const pruned = pruneMissing(model3, model3Dir);
  if (pruned) console.warn(`[model] ${name}: 디스크에 없는 표정/모션 참조 ${pruned}개를 무시했어요`);
  const files = scanAssetFiles(model3Dir);
  // vtube.json 의 파일 참조(표정/모션)는 vtube.json 폴더 기준 → model3.json 폴더 기준 상대경로로 변환
  const baseDir = entry.vtube ? path.dirname(entry.vtube) : path.dirname(model3Path);
  let hotkeyBase = path.relative(path.dirname(model3Path), baseDir).split(path.sep).join('/');
  if (hotkeyBase) hotkeyBase += '/';

  return {
    name: entry.name,
    vts,
    model3,
    model3Url: pathToFileURL(model3Path).href,
    hotkeyBase,
    files,
    pruned,
  };
}

// ---------- 모델별 프로필 (persona.txt / emotionAliases.json) ----------
// 모델 폴더(models/<이름>/) 안에 둔다: 모델을 옮기거나 지우면 프로필도 같이 간다.
function profileDir(name) {
  const all = listModels();
  const entry = all.find((m) => m.name === name) || all.find((m) => m.name.toLowerCase() === String(name).toLowerCase());
  if (!entry) throw new Error(`모델을 찾을 수 없음: ${name}`);
  return path.join(MODELS_DIR, entry.name);   // 인식된 모델 폴더만 허용 (경로 조작 방지)
}
// 옛 방식(config.json 의 emotionAliases + modelAliases[모델])은 파일이 없을 때 한 번 옮겨 담는 용도로만 쓴다
function legacyAliases(name) {
  const cfg = loadConfig();
  return { ...(cfg.emotionAliases || {}), ...((cfg.modelAliases || {})[name] || {}) };
}

// ---------- log ----------
// 콘솔 한글이 깨지는 건 Windows 콘솔 인코딩 문제라서, 렌더러 콘솔을 UTF-8 파일로도 남긴다.
const LOG_PATH = path.join(ROOT, 'miku.log');
function logLine(line) {
  try { fs.appendFileSync(LOG_PATH, line + '\n', 'utf8'); } catch { /* ignore */ }
}
function onConsoleMessage(...args) {
  const first = args[0];
  // Electron 버전에 따라 (event, level, message, line, source) 또는 event.message 형태
  const message = first && typeof first.message === 'string' ? first.message : args[2];
  const level = first && typeof first.level !== 'undefined' && typeof first.message === 'string' ? first.level : args[1];
  if (typeof message !== 'string' || message.includes('Electron Security Warning')) return;
  const tag = ['debug', 'info', 'warn', 'error'][typeof level === 'number' ? level : 1] || String(level);
  logLine(`${new Date().toTimeString().slice(0, 8)} [${tag}] ${message}`);
}

// ---------- window ----------
// 창은 모니터의 작업 영역(작업표시줄 제외) 전체를 덮는 투명 창이다.
// 모델은 이 안에서 자유롭게 움직이고, 모델/입력창이 없는 부분은 클릭이 뒤로 통과한다.
let editMode = false;

function targetDisplay() {
  const id = loadConfig().displayId;
  return screen.getAllDisplays().find((d) => d.id === id) || screen.getPrimaryDisplay();
}

function placeWindow() {
  if (!win || win.isDestroyed()) return;
  const wa = targetDisplay().workArea;
  win.setResizable(true);
  win.setBounds({ x: wa.x, y: wa.y, width: wa.width, height: wa.height });
  win.setResizable(false);
}

function createWindow() {
  const wa = targetDisplay().workArea;
  win = new BrowserWindow({
    x: wa.x,
    y: wa.y,
    width: wa.width,
    height: wa.height,
    transparent: true,
    frame: false,
    hasShadow: false,
    resizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    icon: path.join(ROOT, 'assets', 'icon.ico'),
    webPreferences: {
      preload: path.join(ROOT, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false, // 숨김 상태에서도 음성/통신이 멈추지 않게
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  appliedProtection = null; // 새 창이면 다시 적용
  applyCaptureProtection();
  win.webContents.on('console-message', onConsoleMessage);
  win.loadFile(path.join(ROOT, 'renderer', 'index.html'));
  startCursorPolling();
}

// 클릭 통과 중에는 창이 마우스 이벤트를 못 받는다 (forward 옵션은 환경에 따라 불안정).
// 그래서 메인 프로세스가 커서 위치를 직접 읽어서 렌더러에 창 기준 좌표로 알려준다.
function startCursorPolling() {
  let last = '';
  setInterval(() => {
    if (!win || win.isDestroyed()) return;
    const p = screen.getCursorScreenPoint();
    const b = win.getBounds();
    const x = p.x - b.x;
    const y = p.y - b.y;
    const key = `${x},${y}`;
    if (key === last) return;
    last = key;
    win.webContents.send('cursor:pos', { x, y });
  }, 33);
}

// ---------- state / actions ----------
// 값이 배열이면 앞에서부터 등록을 시도해 처음 성공한 것을 쓴다 (Pause 가 환경에 따라 안 먹을 때 대비)
const DEFAULT_HOTKEYS = {
  toggleModel: 'CommandOrControl+Alt+H',        // 모델 표시/숨기기
  toggleClickThrough: 'CommandOrControl+Alt+P', // 모델 뒤 클릭 가능(모델도 클릭 통과) 켜기/끄기
  toggleBubble: 'CommandOrControl+Alt+B',       // 대사창 켜기/끄기
  capturePartial: ['F9'],              // 부분 캡처 -> 입력창에 첨부 (메시지 입력 후 Enter 로 전송)
  captureFull: ['Shift+F9'],     // 전체 화면 캡처 -> 입력창에 첨부 (메시지 입력 후 Enter 로 전송)
  toggleScreenShare: ['F10'],     // 화면 공유 모드 켜기/끄기 (켜면 메시지마다 화면을 같이 보냄)
  toggleSessions: 'CommandOrControl+Alt+S',     // 대화 목록 패널 열기/닫기
  toggleMic: 'CommandOrControl+Alt+M',          // 마이크 듣기 시작/중지
  toggleProactive: 'CommandOrControl+Alt+T',    // 먼저 말 걸기 켜기/끄기
  toggleGaze: 'CommandOrControl+Alt+G',         // 시선 추적(마우스 따라보기) 켜기/끄기
  toggleLyrics: 'CommandOrControl+Alt+L',       // 실시간 가사 켜기/끄기
  lyricsEarlier: 'CommandOrControl+Alt+PageUp', // 가사 싱크 +0.5초 (가사가 더 일찍 나오게)
  lyricsLater: 'CommandOrControl+Alt+PageDown',  // 가사 싱크 -0.5초 (가사가 더 늦게 나오게)
  lyricsReset: 'CommandOrControl+Alt+End',       // 가사 싱크 보정 초기화
  lyricsPick: '',                                // 가사 직접 고르기 패널 열기 (기본 단축키 없음. 설정 > 단축키에서 정하기)
};
const hotkeys = () => ({ ...DEFAULT_HOTKEYS, ...(loadConfig().hotkeys || {}) });
const hkLabel = (v) => (Array.isArray(v) ? (activeHotkeys[v.join('|')] || v[0]) : v) || '단축키 없음';
const activeHotkeys = {}; // 배열 설정 -> 실제로 등록된 키
const hotkeyStatus = {};  // 동작 이름 -> { wanted, accel, ok } (설정 창 단축키 탭에서 등록 실패를 보여 주려고)

function refreshTray() {
  if (tray) tray.setContextMenu(buildTrayMenu());
}
// 캡처 방지: 켜면 모델/말풍선이 스크린샷·화면 캡처(앱의 화면 보기 포함)에 찍히지 않고, 끄면 찍힌다.
// config.captureExcludeSelf (기본 켜짐). Windows 10 2004+, macOS. 설정이 바뀌면 바로 다시 적용한다.
let appliedProtection = null;
function applyCaptureProtection() {
  if (!win || win.isDestroyed()) return;
  const on = loadConfig().captureExcludeSelf !== false;
  if (on === appliedProtection) return;
  try { win.setContentProtection(on); appliedProtection = on; } catch (e) { console.warn('[capture] 캡처 방지 설정 실패', e.message); }
}
function notifyConfig() {
  applyCaptureProtection();
  lyricsSvc.configure();
  win && !win.isDestroyed() && win.webContents.send('config:changed', loadConfig());
}
function setConfigFlag(patch) {
  saveConfig({ ...loadConfig(), ...patch });
  notifyConfig();
  refreshTray();
}
function toggleVisible() {
  if (!win) return;
  if (win.isVisible()) win.hide(); else win.showInactive();
  refreshTray();
}
function setEditMode(v) {
  editMode = !!v;
  if (win && editMode && !win.isVisible()) win.showInactive();
  win && win.webContents.send('edit:mode', editMode);
  refreshTray();
}
function toggleClickThroughModel() {
  setConfigFlag({ clickThroughModel: loadConfig().clickThroughModel !== true });
}

// 시선 추적(마우스 포인터를 따라 눈/고개를 돌림): 기본 켜짐
function toggleGaze() {
  setConfigNested('gaze', { enabled: (loadConfig().gaze || {}).enabled === false });
}
function toggleBubble() {
  setConfigFlag({ showBubble: loadConfig().showBubble === false });
}
function toggleScreenShare() {
  setConfigFlag({ screenShare: loadConfig().screenShare !== true });
}
// 대화 목록 패널: 숨겨진 상태에서도 단축키/메뉴로 열 수 있게 창을 먼저 보여 준다
function showSessionsUI(channel) {
  if (!win || win.isDestroyed()) return;
  if (!win.isVisible()) win.showInactive();
  win.webContents.send(channel);
}
const toggleSessions = () => showSessionsUI('sessions:toggle');
const newSession = () => showSessionsUI('sessions:new');
const openSettings = () => showSessionsUI('settings:open');

function requestCapture(mode) {
  if (!win || win.isDestroyed()) return;
  if (!win.isVisible()) win.showInactive();
  win.webContents.send('capture:request', mode);
}

function setConfigNested(key, patch) {
  const cfg = loadConfig();
  setConfigFlag({ [key]: { ...(cfg[key] || {}), ...patch } });
}
function sendToRenderer(channel, payload) {
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
}
// 먼저 말 거는 간격 입력 상자: 모델을 숨겨 둔 상태에서도 열 수 있게 창을 먼저 보여 준다
function showIntervalPrompt() {
  if (!win || win.isDestroyed()) return;
  if (!win.isVisible()) win.showInactive();
  win.webContents.send('proactive:prompt');
}
let micActive = false; // 렌더러가 알려 주는 마이크 상태 (트레이 메뉴의 체크 표시용)
function toggleMic() { sendToRenderer('mic:toggle'); }

// 먼저 말 걸기(사용자가 말이 없으면 캐릭터가 먼저 거는 말): 설정을 저장하면 트레이와 렌더러가 함께 갱신되고, 렌더러가 안내 말풍선을 띄운다
function toggleProactive() {
  const on = (loadConfig().proactive || {}).enabled === true;
  setConfigNested('proactive', { enabled: !on });
}

// ---------- 실시간 가사 ----------
// 재생 중인 곡(윈도우 미디어 정보)을 감시해서 알송/LRCLIB 에서 가사를 찾고, 렌더러(말풍선 자리)로 보낸다. 설정: config.json 의 lyrics
const lyricsSvc = new LyricsService({ root: ROOT, getConfig: loadConfig, send: sendToRenderer, log: logLine });
function toggleLyrics() { setConfigNested('lyrics', { enabled: (loadConfig().lyrics || {}).enabled === false }); }
function lyricsCommand(action, arg) {
  return lyricsSvc.command(action, arg).then((r) => { if (!r.ok && action !== 'status') sendToRenderer('lyrics:flash', { text: r.message.slice(0, 40) }); return r; });
}

// ---------- 일정 ----------
// 일정이 바뀌면 열려 있는 일정 관리 창이 목록을 다시 읽게 알린다
function notifyScheduleChanged() {
  for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed() && w !== win) w.webContents.send('schedule:changed');
}
let scheduleWin = null;
function openScheduleWindow() {
  if (scheduleWin && !scheduleWin.isDestroyed()) { scheduleWin.show(); scheduleWin.focus(); return; }
  scheduleWin = new BrowserWindow({
    width: 460, height: 640, minWidth: 380, minHeight: 420,
    title: '일정 관리', autoHideMenuBar: true, alwaysOnTop: true,   // 모델 창이 항상 위라서 이 창도 위에 둔다
    icon: path.join(ROOT, 'assets', 'icon.ico'),
    webPreferences: { preload: path.join(ROOT, 'schedule-preload.js'), contextIsolation: true, nodeIntegration: false },
  });
  scheduleWin.setAlwaysOnTop(true, 'screen-saver');   // 모델 창과 같은 층 (그보다 아래면 모델에 가려진다)
  scheduleWin.setMenuBarVisibility(false);
  scheduleWin.loadFile(path.join(ROOT, 'renderer', 'schedule.html'));
  scheduleWin.on('closed', () => { scheduleWin = null; });
}
const scheduler = new Scheduler(SCHEDULE_PATH, (item) => {
  notifyScheduleChanged();
  sendToRenderer('schedule:fire', item);
  // 창을 숨겨 둔 상태면 말풍선이 안 보이니 시스템 알림도 함께 띄운다
  if (win && !win.isVisible() && Notification.isSupported()) new Notification({ title: '⏰ 일정 알림', body: item.text }).show();
});

function registerHotkeys() {
  globalShortcut.unregisterAll();
  const hk = hotkeys();
  const actions = {
    toggleModel: toggleVisible,
    toggleClickThrough: toggleClickThroughModel,
    toggleBubble,
    capturePartial: () => requestCapture('partial'),
    captureFull: () => requestCapture('full'),
    toggleScreenShare,
    toggleSessions,
    toggleMic,
    toggleProactive,
    toggleGaze,
    toggleLyrics,
    lyricsEarlier: () => lyricsCommand('offset', 0.5),
    lyricsLater: () => lyricsCommand('offset', -0.5),
    lyricsReset: () => lyricsCommand('offset-reset'),
    lyricsPick: () => sendToRenderer('lyrics:pick', {}),   // 가사 직접 고르기 패널 열기
  };
  for (const k of Object.keys(hotkeyStatus)) delete hotkeyStatus[k];
  for (const [name, fn] of Object.entries(actions)) {
    const raw = hk[name];
    if (!raw) continue;
    const list = Array.isArray(raw) ? raw : [raw];
    let used = null;
    for (const accel of list) {
      try {
        if (globalShortcut.register(accel, fn)) { used = accel; break; }
        logLine(`[hotkey] 등록 실패(다른 프로그램이 사용 중?): ${accel}`);
      } catch (e) {
        logLine(`[hotkey] 잘못된 단축키 "${accel}": ${e.message}`);
      }
    }
    if (Array.isArray(raw)) activeHotkeys[raw.join('|')] = used || raw[0];
    hotkeyStatus[name] = { wanted: list[0], accel: used, ok: !!used };
    if (used) logLine(`[hotkey] ${name} = ${used}`);
  }
  refreshTray();
}

// ---------- tray ----------
// 모니터가 여러 개일 때만 "모니터" 메뉴를 보여준다
function displayMenu(cfg) {
  const all = screen.getAllDisplays();
  if (all.length < 2) return [];
  const current = targetDisplay().id;
  return [{
    label: '모니터',
    submenu: all.map((d, i) => ({
      label: `모니터 ${i + 1} (${d.size.width}x${d.size.height})${d.id === screen.getPrimaryDisplay().id ? ' · 주 모니터' : ''}`,
      type: 'radio',
      checked: d.id === current,
      click: () => { saveConfig({ ...loadConfig(), displayId: d.id }); placeWindow(); refreshTray(); },
    })),
  }];
}

function buildTrayMenu() {
  const cfg = loadConfig();
  const models = listModels();
  const hk = hotkeys();
  const pro = effective(cfg.proactive); // 먼저 말 걸기: 지금 적용 중인 값 (기본 20~45분)
  return Menu.buildFromTemplate([
    {
      label: '모델',
      submenu: models.length
        ? models.map((m) => ({
            label: m.name,
            type: 'radio',
            checked: m.name === cfg.model,
            click: () => {
              saveConfig({ ...loadConfig(), model: m.name });
              win && win.webContents.send('model:select', m.name);
            },
          }))
        : [{ label: 'models/ 폴더에 모델을 넣어 주세요', enabled: false }],
    },
    {
      label: '모델 목록 새로고침',
      click: () => refreshTray(),
    },
    { type: 'separator' },
    { label: '📅 일정 관리…', click: () => openScheduleWindow() },
    { type: 'separator' },
    ...displayMenu(cfg),
    {
      label: `모델 표시 (${hkLabel(hk.toggleModel)})`,
      type: 'checkbox',
      checked: win ? win.isVisible() : true,
      click: () => toggleVisible(),
    },
    {
      label: '편집 모드 (크기/위치 조절)',
      type: 'checkbox',
      checked: editMode,
      click: () => setEditMode(!editMode),
    },
    { type: 'separator' },
    {
      // 모델 주변의 투명한 부분만 클릭이 뒤로 통과. 모델 몸통은 마우스를 받는다(드래그/입력창).
      label: '투명 영역 클릭 통과',
      type: 'checkbox',
      checked: cfg.passthrough !== false,
      click: (item) => setConfigFlag({ passthrough: item.checked }),
    },
    {
      // 모델 몸통까지 포함해서 창 전체가 클릭을 통과. 모델 뒤에 가려진 창을 클릭할 수 있다.
      label: `모델 뒤 클릭 가능 (${hkLabel(hk.toggleClickThrough)})`,
      type: 'checkbox',
      checked: cfg.clickThroughModel === true,
      click: (item) => setConfigFlag({ clickThroughModel: item.checked }),
    },
    { type: 'separator' },
    {
      label: `대사창 표시 (${hkLabel(hk.toggleBubble)})`,
      type: 'checkbox',
      checked: cfg.showBubble !== false,
      click: (item) => setConfigFlag({ showBubble: item.checked }),
    },
    { label: `대화 목록 (${hkLabel(hk.toggleSessions)})`, click: () => toggleSessions() },
    { label: '새 대화 시작', click: () => newSession() },
    { type: 'separator' },
    {
      label: `화면 공유 모드 - 메시지마다 화면 전송 (${hkLabel(hk.toggleScreenShare)})`,
      type: 'checkbox',
      checked: cfg.screenShare === true,
      click: (item) => setConfigFlag({ screenShare: item.checked }),
    },
    { label: `부분 캡처 (${hkLabel(hk.capturePartial)})`, click: () => requestCapture('partial') },
    { label: `전체 화면 캡처 (${hkLabel(hk.captureFull)})`, click: () => requestCapture('full') },
    {
      // 켜면 모델/말풍선이 스크린샷·화면 보기에 찍히지 않고, 끄면 같이 찍힌다 (바로 적용). config.json 의 captureExcludeSelf 와 같은 값
      label: '캡처 방지 - 모델/말풍선이 캡처에 안 찍힘',
      type: 'checkbox',
      checked: cfg.captureExcludeSelf !== false,
      click: (item) => setConfigFlag({ captureExcludeSelf: item.checked }),
    },
    { type: 'separator' },
    // 마이크: 켜 두면 끌 때까지 계속 듣는다. 체크 표시는 실제 마이크 상태를 따른다
    { label: `마이크 듣기 (${hkLabel(hk.toggleMic)})`, type: 'checkbox', checked: micActive, click: () => toggleMic() },
    // 화자 인증(선택): config.json 의 speaker.enabled 를 켜고 목소리를 등록하면, 등록한 사람의 말만 인식한다
    { label: '내 목소리 등록…', click: () => sendToRenderer('speaker:enroll') },
    { label: '등록한 목소리 지우기', click: () => voice.speakerClear().then(() => sendToRenderer('speaker:cleared')) },
    {
      label: `먼저 말 걸기 - 말이 없으면 캐릭터가 먼저 (${hkLabel(hk.toggleProactive)})`,
      type: 'checkbox',
      checked: (cfg.proactive || {}).enabled === true,
      click: (item) => setConfigNested('proactive', { enabled: item.checked }),
    },
    {
      // 숫자를 직접 입력해서 정한다. 적용하면 먼저 말 걸기도 같이 켜진다 (더 세밀한 조절은 config.json 의 proactive)
      label: '먼저 말 거는 간격',
      submenu: [
        { label: `현재: ${intervalLabel(pro.min, pro.max)} (${pro.enabled ? '켜짐' : '꺼짐'})`, enabled: false },
        { type: 'separator' },
        { label: '간격 직접 입력…', click: () => showIntervalPrompt() },
        { label: '지금 한 번 말 걸어보기', click: () => sendToRenderer('proactive:now') },
      ],
    },
    {
      label: `시선 추적 - 마우스 포인터 따라보기 (${hkLabel(hk.toggleGaze)})`,
      type: 'checkbox',
      checked: (cfg.gaze || {}).enabled !== false,
      click: (item) => setConfigNested('gaze', { enabled: item.checked }),
    },
    {
      label: `실시간 가사 - 재생 중인 곡 가사를 말풍선 자리에 (${hkLabel(hk.toggleLyrics)})`,
      type: 'checkbox',
      checked: (cfg.lyrics || {}).enabled !== false,
      click: (item) => setConfigNested('lyrics', { enabled: item.checked }),
    },
    {
      label: '    ↳ 가사 싱크 / 다시 찾기',
      submenu: [
        { label: `가사 더 일찍 +0.5초 (${hkLabel(hk.lyricsEarlier)})`, click: () => lyricsCommand('offset', 0.5) },
        { label: `가사 더 늦게 -0.5초 (${hkLabel(hk.lyricsLater)})`, click: () => lyricsCommand('offset', -0.5) },
        { label: `싱크 보정 초기화 (${hkLabel(hk.lyricsReset)})`, click: () => lyricsCommand('offset-reset') },
        { type: 'separator' },
        { label: '가사 다시 찾기', click: () => lyricsCommand('refresh') },
        { label: `가사 직접 고르기… (${hkLabel(hk.lyricsPick)})`, click: () => sendToRenderer('lyrics:pick', {}) },
        { label: '직접 검색은 입력창에 "/가사 가수 - 제목"', enabled: false },
      ],
    },
    { type: 'separator' },
    { label: '설정… (서버/화면/기억)', click: () => openSettings() },
    { label: '종료', click: () => app.quit() },
  ]);
}

function createTray() {
  const iconPath = path.join(ROOT, 'assets', 'tray.png');
  const icon = fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
  tray = new Tray(icon);
  tray.setToolTip('Project Miku');
  tray.setContextMenu(buildTrayMenu());
  tray.on('click', () => tray.popUpContextMenu()); // 왼쪽 클릭으로도 메뉴가 뜨게
}

// ---------- IPC ----------
ipcMain.handle('config:get', () => loadConfig());
ipcMain.handle('config:set', (_e, patch) => {
  const next = { ...loadConfig(), ...patch };
  saveConfig(next);
  return next;
});
// 설정 창에서 바꾼 값: 저장하고 렌더러에 바로 알린다 (config:set 은 편집 모드의 연속 저장용이라 알리지 않는다)
ipcMain.handle('config:apply', (_e, patch) => {
  const next = { ...loadConfig(), ...patch };
  saveConfig(next);
  notifyConfig();
  refreshTray();
  return next;
});
// 대화 내보내기: 저장 위치를 물어보고 글 파일로 저장한다
ipcMain.handle('file:saveText', async (_e, filename, text) => {
  try {
    const name = path.basename(String(filename || 'miku.md')).replace(/[\\/:*?"<>|]/g, '_') || 'miku.md';
    const parent = win && !win.isDestroyed() ? win : undefined;
    const r = await dialog.showSaveDialog(parent, {
      title: '대화 내보내기',
      defaultPath: path.join(app.getPath('documents'), name),
      filters: [{ name: '텍스트', extensions: [path.extname(name).slice(1) || 'md'] }],
    });
    if (r.canceled || !r.filePath) return { ok: false, canceled: true };
    fs.writeFileSync(r.filePath, String(text ?? ''), 'utf8');
    return { ok: true, path: r.filePath };
  } catch (e) {
    return { ok: false, error: String(e && e.message || e) };
  }
});
ipcMain.on('app:relaunch', () => { app.relaunch(); app.exit(0); });
ipcMain.handle('models:list', () => listModels().map((m) => m.name));
ipcMain.handle('models:resolve', (_e, name) => resolveModel(name));
ipcMain.handle('profile:get', (_e, name) => {
  const dir = profileDir(name);
  const r = profileStore.readProfile(dir, legacyAliases(name));
  if (r.migrated) logLine(`[profile] ${name}: config.json 의 감정 별칭 ${r.rows.length}개를 ${profileStore.ALIAS_FILE} 로 옮겼어요`);
  if (r.error) logLine(`[profile] ${name}: ${r.error}`);
  return r;
});
ipcMain.handle('profile:save', (_e, name, data) => {
  try { return { ok: true, ...profileStore.writeProfile(profileDir(name), data || {}) }; }
  catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
});
// 단축키 탭: 기본값/사용자가 바꾼 값/등록 상태를 알려 주고, 저장하면 바로 다시 등록한다
ipcMain.handle('hotkeys:get', () => ({ defaults: DEFAULT_HOTKEYS, overrides: loadConfig().hotkeys || {}, status: { ...hotkeyStatus } }));
ipcMain.handle('hotkeys:save', (_e, overrides) => {
  if (!overrides || typeof overrides !== 'object' || Array.isArray(overrides)) return { ok: false, error: '단축키 설정이 올바르지 않아요' };
  const clean = {};
  for (const [k, v] of Object.entries(overrides)) {
    if (!(k in DEFAULT_HOTKEYS)) continue;   // 모르는 동작 이름은 버린다
    if (typeof v !== 'string' || v.length > 60) return { ok: false, error: `'${k}' 단축키가 올바르지 않아요` };
    clean[k] = v.trim();
  }
  saveConfig({ ...loadConfig(), hotkeys: clean });
  registerHotkeys();
  notifyConfig();
  return { ok: true, status: { ...hotkeyStatus } };
});
// 단축키를 새로 눌러 정하는 동안에는 등록된 단축키가 같이 동작하지 않게 잠시 풀어 둔다 (false 로 다시 등록)
ipcMain.on('hotkeys:pause', (_e, paused) => { if (paused) globalShortcut.unregisterAll(); else registerHotkeys(); });
// 가사창 아이템(마이크 스탠드): 모델별 위치/크기 + 그림 주소 + 클릭 판정용 알파 마스크. 그림은 모델 폴더의 lyricsItem.png 가 있으면 그걸, 없으면 assets/lyrics-mic-stand.png
// (알파는 여기서 읽어 보낸다: file:// 그림을 캔버스로 읽으면 브라우저가 막는다)
const ITEM_DEFAULT_IMAGE = path.join(__dirname, 'assets', 'lyrics-mic-stand.png');
ipcMain.handle('item:get', (_e, name) => {
  const dir = profileDir(name);
  const file = profileStore.findItemImage(dir) || ITEM_DEFAULT_IMAGE;
  let mask = null; let ver = '';
  try {
    ver = String(Math.round(fs.statSync(file).mtimeMs));
    const img = nativeImage.createFromPath(file);
    if (!img.isEmpty()) {
      const { width, height } = img.getSize();
      const w = 160; const h = Math.max(1, Math.round((w * height) / width));
      const bmp = img.resize({ width: w, height: h, quality: 'good' }).toBitmap();   // 4바이트(BGRA) 씩
      const alpha = new Uint8Array(w * h);
      for (let i = 0; i < alpha.length; i++) alpha[i] = bmp[i * 4 + 3];
      mask = { w, h, alpha };
    }
  } catch (e) { logLine(`[item] ${name}: 그림 판정 정보를 못 만들었어요: ${e.message}`); }
  return { item: profileStore.readLyricsItem(dir), image: pathToFileURL(file).href + (ver ? `?v=${ver}` : ''), mask };
});
ipcMain.handle('item:save', (_e, name, item) => {
  try { return { ok: true, item: profileStore.writeLyricsItem(profileDir(name), item) }; }
  catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
});
ipcMain.handle('edit:get', () => editMode);
ipcMain.on('win:focus', () => { if (win && !win.isDestroyed()) win.focus(); });

// 모델이 올라간 모니터 전체를 캡처해서 JPEG 로 돌려준다. (부분 캡처는 렌더러에서 자른다)
ipcMain.handle('capture:screen', async () => {
  const d = targetDisplay();
  const sf = d.scaleFactor || 1;
  const pxW = Math.round(d.bounds.width * sf);
  const pxH = Math.round(d.bounds.height * sf);
  const sources = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: pxW, height: pxH } });
  const src = sources.find((x) => String(x.display_id) === String(d.id)) || sources[0];
  if (!src) throw new Error('캡처할 화면을 찾지 못했어요');
  const img = src.thumbnail;
  const size = img.getSize();
  const wa = d.workArea;
  return {
    jpg: img.toJPEG(92),
    pxW: size.width, pxH: size.height,
    dipW: d.bounds.width, dipH: d.bounds.height,
    offX: wa.x - d.bounds.x, offY: wa.y - d.bounds.y, // 창(작업 영역) 좌표 -> 모니터 좌표
  };
});
ipcMain.on('edit:set', (_e, v) => setEditMode(v));
ipcMain.handle('media:run', (_e, action, value) => media.run(action, value));
ipcMain.handle('lyrics:snapshot', () => lyricsSvc.snapshot());
// 지금 재생 중인 곡: 가사 감시가 알고 있으면 그걸(가사 DB 의 같은 곡 정보 포함), 감시가 꺼져 있으면 미디어 세션을 한 번 읽어서
ipcMain.handle('nowplaying:get', async () => {
  const t = lyricsSvc.nowPlaying();
  if (t) return t;
  try {
    const r = await media.run('nowplaying');
    if (r.ok && r.data && r.data.found && r.data.title) return { title: r.data.title, artist: r.data.artist || '', album: '', app: '', duration: 0, status: r.data.status === 'Paused' ? 'Paused' : 'Playing' };
  } catch { /* 곡 정보 없이 보낸다 */ }
  return null;
});
ipcMain.handle('lyrics:cmd', (_e, action, arg) => lyricsSvc.command(action, arg));
ipcMain.handle('lyrics:candidates', (_e, query) => lyricsSvc.candidates(query));   // 가사 직접 고르기: 후보 목록 / 미리보기 / 선택
ipcMain.handle('lyrics:preview', (_e, key) => lyricsSvc.preview(String(key || '')));
ipcMain.handle('lyrics:choose', (_e, key) => lyricsSvc.choose(String(key || '')));
ipcMain.on('mic:state', (_e, active) => { micActive = active === true; refreshTray(); });
// 음성 전처리 (Silero VAD / 화자 인증): 모델이 없거나 실패해도 마이크는 예전 방식으로 계속 동작한다
const voice = new Voice({ baseDir: ROOT, getConfig: loadConfig });
// 음성 -> 글자 요청은 메인 프로세스에서 보낸다 (렌더러의 fetch 는 서버의 CORS 설정에 막힌다)
ipcMain.handle('stt:transcribe', async (_e, cfg, audio) => {
  try { return { ok: true, text: await transcribeRequest(cfg, Buffer.from(audio)) }; }
  catch (e) { return { ok: false, error: (e && e.message) || String(e) }; }
});
ipcMain.handle('vad:init', () => voice.initVad());
ipcMain.on('vad:reset', () => voice.vadReset());
ipcMain.handle('vad:frame', (_e, frame) => voice.vadFrame(frame));
ipcMain.handle('speaker:status', () => voice.speakerStatus());
ipcMain.handle('speaker:verify', (_e, pcm) => voice.speakerVerify(pcm));
ipcMain.handle('speaker:enroll', (_e, pcms) => voice.speakerEnroll(pcms));
ipcMain.handle('speaker:clear', () => voice.speakerClear());
// 먼저 말 걸기 설정 저장 (렌더러가 보낸 값은 검증해서 필요한 항목만 받는다)
ipcMain.handle('proactive:set', (_e, patch) => {
  const clean = sanitizePatch(patch);
  if (Object.keys(clean).length) setConfigNested('proactive', clean);
});
// 먼저 말 걸기 조건: 지금 자리에 있는지(키보드/마우스 입력이 마지막으로 있은 뒤 몇 초), 모델이 보이는 상태인지
ipcMain.handle('system:state', () => ({
  idleSec: powerMonitor.getSystemIdleTime(),
  visible: !!(win && !win.isDestroyed() && win.isVisible()),
}));
ipcMain.handle('schedule:add', (_e, item) => { const r = scheduler.add(item); notifyScheduleChanged(); return r; });
ipcMain.handle('schedule:list', () => scheduler.list());
ipcMain.handle('schedule:remove', (_e, idOrAll) => { const r = scheduler.remove(idOrAll); notifyScheduleChanged(); return r; });
ipcMain.handle('schedule:update', (_e, id, patch) => { const r = scheduler.update(String(id), patch || {}); notifyScheduleChanged(); return r; });
// 클립보드(글자만): 사용자가 "방금 복사한 거" 같은 말로 물었을 때만 렌더러가 부른다
ipcMain.handle('clipboard:read', () => { try { return clipboard.readText() || ''; } catch { return ''; } });
// 클릭 통과: 커서 위치는 메인이 직접 읽어 알려주므로(startCursorPolling) forward 는 필요 없다.
ipcMain.on('win:setIgnore', (_e, ignore) => {
  if (win) win.setIgnoreMouseEvents(!!ignore);
});
// 모델 우클릭 -> 트레이와 같은 메뉴를 그 자리에 띄운다
ipcMain.on('menu:show', () => {
  if (win && !win.isDestroyed()) buildTrayMenu().popup({ window: win });
});

// ---------- lifecycle ----------
app.whenReady().then(() => {
  try { fs.writeFileSync(LOG_PATH, '', 'utf8'); } catch { /* ignore */ }
  // 마이크(오디오)만 허용하고 카메라 등은 막는다
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb, details) => {
    const types = (details && details.mediaTypes) || [];
    cb(permission === 'media' && !types.includes('video'));
  });
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => permission === 'media');
  // PC 에서 나는 소리(유튜브/음악 등)를 마이크 에코 제거의 기준 신호로 받는다: Windows 의 loopback 오디오. 화면 영상은 쓰지 않는다.
  session.defaultSession.setDisplayMediaRequestHandler(async (_req, cb) => {
    try {
      if (process.platform !== 'win32') return cb({});   // loopback 오디오는 Windows 만 지원
      const sources = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: 1, height: 1 } });
      return sources.length ? cb({ video: sources[0], audio: 'loopback' }) : cb({});
    } catch (e) {
      console.warn('[aec] PC 소리 캡처 준비 실패:', e && e.message);
      return cb({});
    }
  });
  createWindow();
  createTray();
  registerHotkeys();
  lyricsSvc.configure();
  // 렌더러가 이벤트 수신을 등록한 뒤부터 일정을 확인한다 (그 전에 울리면 놓친 알림이 유실됨)
  win.webContents.once('did-finish-load', () => setTimeout(() => scheduler.start(), 3000));
  for (const ev of ['display-added', 'display-removed', 'display-metrics-changed']) screen.on(ev, () => { placeWindow(); refreshTray(); });
});
app.on('will-quit', () => { globalShortcut.unregisterAll(); scheduler.stop(); lyricsSvc.stop(); });
app.on('window-all-closed', () => app.quit());
