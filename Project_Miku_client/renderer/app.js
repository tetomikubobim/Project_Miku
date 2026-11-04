import { buildModelSettings } from './model-loader.js';
import { EmotionController } from './emotion.js';
import { MikuSocket } from './ws-client.js';
import { Bubble } from './bubble.js';
import { rmsOf, nextMouth, smoothMouth, createMouthWriter, normalizeAudioLatency } from './lipsync.js';
import { LyricsView } from './lyrics.js';
import { splitThink } from './think.js';
import { cropToBlob, selectRegion } from './capture.js';
import { SessionPanel } from './sessions.js';
import { parseCommand, fmtWhen, fmtRepeat } from './commands.js';
import { MicListener } from './mic.js';
import { Proactive, normalizeProactive, describe as describeProactive, canInterrupt } from './proactive.js';
import { transcribe, isGhostText, effectiveStt, textSimilarity } from './stt.js';
import { IntervalDialog } from './interval.js';
import { GazeController, createGazeWriter, normalizeGaze } from './gaze.js';
import { StreamState } from './stream.js';
import { SettingsDialog } from './settings.js';
import { setupArtMeshOverrides } from './artmesh.js';
import { aliasMap, serverProfile } from './profile.js';
import { LyricsPicker } from './lyrics-picker.js';
import { asksAboutMusic, toPayload as nowPlayingPayload } from './now-playing.js';
import { asksAboutClipboard, clipText, CLIP_DEFAULT_MAX, schedulePayload } from './context.js';
import { ITEM_DEFAULT, normalizeItem, itemRect, moveItem, scaleItem, buildMask, hitMask, itemShown } from './lyrics-item.js';

// 에러가 나면 위치를 알 수 있게 스택까지 콘솔에 남긴다
window.addEventListener('error', (e) => console.error('ERR>', e.message, e.error && e.error.stack));

const { Application } = PIXI;
const { Live2DModel } = PIXI.live2d;

// 창은 모니터 작업 영역 전체를 덮는다. 모델은 그 안에서 자유롭게 움직인다.
const BASE_MODEL_HEIGHT = 593;   // 배율 1.0 일 때 모델 높이(px)
const BASE_RIGHT_MARGIN = 260;   // 기본 위치: 화면 오른쪽 아래, 오른쪽 끝에서 이만큼 안쪽이 모델 중심

// 사용자가 편집 모드에서 바꾸는 값들의 기본값 (config.json 에 저장됨)
const DEFAULTS = {
  scale: 1,
  modelOffset: { x: 0, y: 0 },
  ui: { width: 300, fontSize: 14, offsetX: 0, offsetY: 0 },
};

let config = {};
let modelName = '';
let app = null;
let model = null;
let emotion = null;
let profile = null; // 지금 모델의 프로필 { persona, rows:[{tag,expression,desc}], voice:{gpt_weights_path,…} } (모델 폴더의 persona.txt / emotionAliases.json). 못 읽었으면 null
let socket = null;
let bubble = null;
let editMode = false;
let pendingEmotion = ''; // 서버 감정 라벨 (think 에서 단서를 못 찾았을 때 폴백으로 사용)

const $ = (id) => document.getElementById(id);
const canvas = $('stage');
const ui = $('ui');
const bubbleEl = $('bubble');
const lyricsEl = $('lyrics');
let lyrics = null;   // 말풍선 자리에 뜨는 실시간 가사 (lyrics.js)
let lyricsPoseOn = false;   // 가사창 전용 표정/모션을 적용해 둔 상태 (모델 폴더의 lyricsPose.json)
let lyricsPoseOffAt = 0;    // 가사가 멈춘 시각 (잠깐 멈춤/곡 사이에 표정이 깜빡이지 않게 기다린다)
const inputBar = $('inputBar');
const input = $('input');
const btnSessions = $('btnSessions');
const btnStop = $('btnStop');
const panel = $('editPanel');

// 대화 목록 패널: 동작은 서버에 요청만 하고, 화면은 서버가 돌려주는 sessions/session_active 이벤트로 갱신한다.
const sessionPanel = new SessionPanel($('sessPanel'), {
  onNew: () => { proactive.noteUserActivity(); stopAudio(); socket && socket.newSession(); },
  onSwitch: (id) => { proactive.noteUserActivity(); stopAudio(); socket && socket.switchSession(id); },
  onDelete: (id) => socket && socket.deleteSession(id),
  onDeleteAll: () => { stopAudio(); socket && socket.deleteAllSessions(); },
  onRename: (id, title) => socket && socket.renameSession(id, title),
  onSearch: (q) => socket && socket.searchSessions(q),
  onExport: (id) => { if (!(socket && socket.exportSession(id))) say('서버에 연결되어 있지 않아요.'); },
  onSettings: () => settingsDlg.open(),
  onOpen: () => {
    positionSessions();
    applyIgnoreMode();
    if (socket) { socket.listSessions(); if (sessionPanel.activeId != null) socket.loadMessages(sessionPanel.activeId); }
  },
  onClose: () => applyIgnoreMode(),
});

// 설정 창 (트레이 > 설정…, 대화 목록의 ⚙): 화면 설정은 config.json 에 저장하고, 기억은 서버에 요청한다. (서버 설정은 통합 런처가 맡는다)
const stream = new StreamState();   // 답이 만들어지는 동안 도착하는 조각을 말풍선에 이어 붙이는 상태
// 가사 직접 고르기 (/가사 선택, 트레이 메뉴, 단축키): 후보 중에서 사용자가 고른다
const lyricsPicker = new LyricsPicker($('lyricsPicker'), {
  candidates: (q) => window.api.lyricsCandidates(q),
  current: () => window.api.lyricsSnapshot().then((s) => (s && s.track ? { title: s.track.title, artist: s.track.artist, duration: Math.round(s.track.duration || 0) } : null)),
  preview: (k) => window.api.lyricsPreview(k),
  choose: (k) => window.api.lyricsChoose(k),
  auto: () => window.api.lyricsCommand('auto'),
  onDone: (r) => { say(r.message); bubble.expireIn(r.ok ? 2.5 : 5, true); },
});

const settingsDlg = new SettingsDialog($('settingsDlg'), {
  onOpen: () => { proactive.noteUserActivity(); window.api.focusWindow(); applyIgnoreMode(); },
  onClose: () => applyIgnoreMode(),
  onRefresh: () => { if (socket && socket.isOpen) socket.listMemories(); },
  isConnected: () => !!socket && socket.isOpen,
  getClientConfig: () => config,
  onSaveClient: async (patch) => { config = await window.api.applyConfig(patch); },
  onRelaunch: () => window.api.relaunch(),
  // 모델 탭: 지금 모델의 persona / 감정 태그 <-> 표정. 저장하면 모델 폴더의 파일에 쓰고 서버에도 바로 알린다.
  getProfile: () => ({ model: modelName, persona: profile ? profile.persona : '', rows: profile ? profile.rows : [], voice: profile ? profile.voice : null, lyricsPose: profile ? profile.lyricsPose : null, poseLists: emotion ? emotion.listTyped() : { expressions: [], motions: [] }, expressions: emotion ? emotion.list() : [] }),
  onSaveProfile: async (name, data) => {
    const r = await window.api.saveProfile(name, data);
    if (r && r.ok && name === modelName) {   // 편집하는 동안 다른 모델로 바뀌었으면 파일만 저장하고 지금 상태는 건드리지 않는다
      profile = { persona: r.persona, rows: r.rows, voice: r.voice, lyricsPose: r.lyricsPose };
      emotion && emotion.setAliases(currentAliases());
      applyPoseReserved();
      lyricsPoseOn = false;   // 바뀐 표정/모션을 다음 가사에서 새로 적용
      pushProfile();
    }
    return r;
  },
  onPreviewEmotion: (name) => emotion && emotion.preview(name),
  // 단축키 탭: 기본값/바꾼 값/등록 상태는 메인 프로세스가 알고 있다
  loadHotkeys: () => window.api.getHotkeys(),
  onSaveHotkeys: (overrides) => window.api.saveHotkeys(overrides),
  onHotkeyPause: (paused) => window.api.pauseHotkeys(paused),
  onMemoryAdd: (t) => socket && socket.addMemory(t),
  onMemoryUpdate: (id, t) => socket && socket.updateMemory(id, t),
  onMemoryDelete: (id) => socket && socket.deleteMemory(id),
  onMemoryClear: () => socket && socket.clearMemories(),
});

// 먼저 말 거는 간격 직접 입력 상자 (트레이 > 먼저 말 거는 간격 > 간격 직접 입력…). 적용하면 먼저 말 걸기도 같이 켜진다.
const intervalDlg = new IntervalDialog($('intervalDlg'), {
  onOpen: () => { proactive.noteUserActivity(); window.api.focusWindow(); applyIgnoreMode(); },   // 입력하는 동안은 끼어들지 않는다
  onClose: () => applyIgnoreMode(),
  onApply: async (iv) => {
    const cur = normalizeProactive(config.proactive);
    if (cur.enabled && cur.minMinutes === iv.minMinutes && cur.maxMinutes === iv.maxMinutes) {
      say(`💬 지금과 같은 간격이에요. (${describeProactive(cur)})`);
      return;
    }
    await window.api.proactiveSet({ enabled: true, ...iv });   // 저장되면 config:changed 로 되돌아와서 안내 말풍선이 뜬다
  },
});

// 먼저 말 걸기: 사용자가 한동안 말이 없으면 서버에 'proactive' 를 보내 캐릭터가 먼저 말하게 한다 (켜고 끄기는 설정/트레이/단축키/말로)
const proactive = new Proactive({
  getConfig: () => config.proactive,
  // 이미 말하는 중이거나, 사용자가 말/입력/편집/캡처/대화 목록을 쓰는 중에는 끼어들지 않는다.
  // (마이크가 켜져 있기만 한 건 상관없다. 실제로 말하는 중/인식 중일 때만 막는다)
  canSpeak: () => canInterrupt({
    connected: !!socket && socket.isOpen,
    audioPlaying: playing || !!activeSource,
    micSpeaking: !!(mic && mic.speaking),
    micBusy,
    editMode,
    selecting,
    typing: input.value.trim() !== '',
    panelOpen: sessionPanel.isOpen || intervalDlg.isOpen || settingsDlg.isOpen || lyricsPicker.isOpen,
    lyricsPlaying: !!(lyrics && lyrics.isPlaying()),
  }),
  getSystemState: () => window.api.systemState().catch(() => null),
  // 화면 공유 모드이고 '먼저 말 걸 때도 화면 보기'(기본 켜짐)면 지금 화면을 같이 보낸다. 캡처가 안 되면 화면 없이 말한다.
  speak: async () => {
    if (!socket) return false;
    let ids;
    if (config.screenShare === true && config.proactiveScreen !== false) {
      try { ids = [await grabImage('full')]; }
      catch (e) { console.warn('[capture] 먼저 말 걸기 화면 캡처 실패 (화면 없이 말해요)', e); }
    }
    return socket.sendProactive(ids);
  },
});

function positionSessions() {
  if (!sessionPanel.isOpen) return;
  const b = model ? model.getBounds() : { x: baseX() - 100, y: window.innerHeight - 400, width: 200, height: 400 };
  sessionPanel.position(b, window.innerWidth, window.innerHeight);
}

// 입력창 안내문에 지금 대화 이름을 보여 준다
function refreshPlaceholder() {
  if (pendingShot) return;
  const name = sessionPanel.activeId == null ? '새 대화' : sessionPanel.activeTitle || '대화';
  input.placeholder = `${name} · 메시지 입력 후 Enter (Esc로 닫기)`;
}

const cfgUI = () => ({ ...DEFAULTS.ui, offsetY: config.uiOffsetY || 0, ...(config.ui || {}) });
const cfgOffset = () => ({ ...DEFAULTS.modelOffset, ...(config.modelOffset || {}) });
const cfgScale = () => config.scale || DEFAULTS.scale;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// ---------- 설정 저장 (편집/드래그 중에는 자주 바뀌므로 잠깐 모았다가 한 번에) ----------
let pending = {};
let saveTimer = null;
function persist(patch) {
  config = { ...config, ...patch };
  pending = { ...pending, ...patch };
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, 300);
}
function flushSave() {
  clearTimeout(saveTimer);
  if (!Object.keys(pending).length) return;
  window.api.setConfig(pending);
  pending = {};
}

// ---------- 모델 로드 ----------
async function loadModel(name, retry = false) {
  flushItemSave();   // 이전 모델에서 옮겨 둔 스탠드 위치가 있으면 먼저 저장
  if (!name) {
    const names = await window.api.listModels();
    if (!names.length) { bubble.show('models/ 폴더에 모델을 넣어 주세요.'); return; }
    name = names[0];
    config = await window.api.setConfig({ model: name });
  }

  let resolved;
  try {
    resolved = await window.api.resolveModel(name);
  } catch (e) {
    console.warn('[model] 로드 실패:', e.message || e);
    // 저장된 모델 이름이 사라졌으면(폴더 이름 변경 등) 목록의 첫 모델로 대신 연다
    const names = await window.api.listModels().catch(() => []);
    if (names.length && !names.includes(name) && !retry) {
      config = await window.api.setConfig({ model: names[0] });
      bubble.show(`"${name}" 모델을 못 찾아서 "${names[0]}" 로 열게요.`, '', true);
      return loadModel(names[0], true);
    }
    bubble.show(`모델 로드 실패: ${e.message || e}`, '', true);
    return;
  }

  // 모델 폴더의 persona.txt / emotionAliases.json (없으면 비어 있음). 읽기에 실패해도 모델은 열고, 옛 config.json 별칭을 쓴다.
  let nextProfile = null;
  try { nextProfile = await window.api.getProfile(name); } catch (e) { console.warn('[profile] 읽기 실패:', e.message || e); }

  const { settings, emotionMap, report } = buildModelSettings(resolved);
  console.info('[model]', name, JSON.stringify(report), 'emotions:', [...emotionMap.keys()].join(','));

  let next;
  try {
    next = await Live2DModel.from(settings, { autoInteract: false });
  } catch (e) {
    console.error(e);
    bubble.show('모델을 불러오지 못했어요. 로그를 확인하세요.');
    return;
  }

  if (model) {
    app.stage.removeChild(model);
    model.destroy();
  }
  model = next;
  app.stage.addChild(model);
  measuredHeadTop = null;
  layoutModel();
  setupLipSync(model, settings);
  setupGaze(model);
  // VTube Studio 에서 바꾼 아트메시 투명도/색(vtube.json)을 그대로 적용 (설정 창에서 끌 수 있다)
  try {
    setupArtMeshOverrides(model.internalModel, resolved.vts, {
      isEnabled: () => config.artMeshColors !== false,
      log: (level, msg) => (level === 'warn' ? console.warn(msg) : console.info(msg)),
    });
  } catch (e) { console.warn('[artmesh] 설정 실패', e); }

  modelName = name;
  profile = nextProfile;
  emotion = new EmotionController(model, emotionMap, currentAliases());
  applyPoseReserved();
  lyricsPoseOn = false; lyricsPoseOffAt = 0;
  loadLyricsItem(name);
  console.info('[profile]', name, profile ? `persona ${profile.persona.length}자, 감정 태그 ${profile.rows.map((r) => r.tag).join(',') || '없음'}` : '없음');
  pushProfile();
  if (settingsDlg.isOpen) settingsDlg.render();
  setupMotionDiagnostics(model);
  setTimeout(scheduleMeasure, 800); // 첫 모션이 자리 잡은 뒤 머리 위치를 다시 잰다
}

// 움찔거림 진단/대응: 모션이 시작될 때마다 로그를 남기고, config.idleMotion=false 면 대기 모션(Idle)을 끈다.
// 대기 모션이 끝나고 다시 시작되는 순간 자세가 튀는 모델이 있다.
const idleGapMs = () => Math.max(0, Number(config.idleGap ?? 8)) * 1000;
function setupMotionDiagnostics(m) {
  try {
    const mm = m.internalModel.motionManager;
    mm.on('motionStart', (group, index) => console.info(`[motion] 시작 ${group}#${index}`));
    // 대기 모션 사이에 쉬는 시간: 모션이 끝나고 idleGap 초(기본 8) 뒤에 다음 대기 모션을 시작
    const st = mm.state;
    if (st && typeof st.shouldRequestIdleMotion === 'function' && !st._gapPatched) {
      st._gapPatched = true;
      let lastEnd = performance.now();
      mm.on('motionFinish', () => { lastEnd = performance.now(); });
      const orig = st.shouldRequestIdleMotion.bind(st);
      st.shouldRequestIdleMotion = () => orig() && performance.now() - lastEnd >= idleGapMs();
    }
    if (config.idleMotion === false && mm.groups) {
      mm.groups.idle = undefined; // 대기 모션 자동 재생 끔
      mm.stopAllMotions();
      console.info('[motion] idleMotion=false: 대기 모션 끔');
    }
  } catch (e) { console.warn('[motion] 진단 설정 실패', e); }
}

// 감정 태그 -> 표정: 모델 폴더의 emotionAliases.json 이 기준이다.
// (옛 방식인 config.json 의 emotionAliases/modelAliases 는 파일이 없을 때 처음 한 번 파일로 옮겨지고, 프로필을 못 읽었을 때만 폴백으로 쓴다)
function currentAliases() {
  if (profile) return aliasMap(profile.rows);
  return { ...(config.emotionAliases || {}), ...((config.modelAliases || {})[modelName] || {}) };
}

// 가사창 전용 표정/모션 (모델 폴더의 lyricsPose.json). 대사의 감정으로는 절대 고르지 않게 감정 매칭에서 뺀다.
const poseOf = () => (profile && profile.lyricsPose) || null;
function applyPoseReserved() {
  const p = poseOf();
  emotion && emotion.setReserved(p ? [p.expression, p.motion].filter(Boolean) : []);
}
// ---------- 가사창 아이템 (마이크 스탠드): 가사창이 뜰 때 모델에 핀 고정으로 같이 뜬다 ----------
// 위치/크기는 모델 높이에 대한 비율이라 모델을 옮기거나 키워도 같이 따라간다 (layoutModel 이 부른다). 모델별로 models/<모델>/lyricsItem.json 에 저장.
// 평소엔 마우스를 받지 않고, 편집 모드에서만 끌어서 옮기고 휠로 크기를 바꾼다.
const itemImg = $('lyricsItem');
let lyricsItem = { ...ITEM_DEFAULT };
let itemMask = null;        // 편집 모드에서 아이템을 잡을 때 쓰는 알파 마스크 (없으면 그림 전체 칸)
let itemDirty = false;
let itemSaveTimer = null;
const modelHeightPx = () => BASE_MODEL_HEIGHT * cfgScale();   // 모델이 화면에 그려진 높이 (모션으로 흔들려도 일정)
function itemRectNow() {
  if (!model || !itemImg.naturalWidth) return null;
  return itemRect({ x: model.x, y: model.y, height: modelHeightPx() }, lyricsItem, itemImg.naturalWidth / itemImg.naturalHeight);
}
function layoutItem() {
  const r = itemRectNow();
  if (!r) return;
  Object.assign(itemImg.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
}
async function loadLyricsItem(name) {
  let info = null;
  try { info = await window.api.getLyricsItem(name); } catch (e) { console.warn('[item] 읽기 실패', e.message || e); }
  if (name !== modelName) return;   // 읽는 사이 다른 모델로 바뀌었으면 버린다
  lyricsItem = normalizeItem(info && info.item);
  itemMask = info && info.mask ? buildMask(info.mask.alpha, info.mask.w, info.mask.h) : null;
  itemDirty = false;
  if (info && info.image && itemImg.getAttribute('src') !== info.image) itemImg.src = info.image;
  layoutItem();
}
itemImg.addEventListener('load', layoutItem);
itemImg.addEventListener('error', () => console.warn('[item] 그림을 불러오지 못했어요:', itemImg.getAttribute('src')));
// 설정(lyrics.item, 기본 켜짐)이 켜져 있고 가사창이 보이는 동안(편집 모드에서는 항상) 보인다
let itemOffAt = 0;   // 노래가 멈춘(일시정지) 시각
function updateLyricsItem(now = Date.now()) {
  const r = itemShown({
    enabled: (config.lyrics || {}).item !== false && !!model && itemImg.naturalWidth > 0,
    editMode,
    lyricsVisible: !!(lyrics && lyrics.visible),
    playing: !!(lyrics && lyrics.isPlaying()),
    wasOn: itemImg.classList.contains('on'),
    offAt: itemOffAt,
  }, now, LYRICS_POSE_RELEASE_MS);   // 가사창 전용 표정/모션이 돌아오는 시간과 같다
  itemOffAt = r.offAt;
  itemImg.classList.toggle('on', r.on);
}
function setItem(next) {
  lyricsItem = normalizeItem(next);
  itemDirty = true;
  layoutItem();
  syncPanel();
  clearTimeout(itemSaveTimer);
  itemSaveTimer = setTimeout(flushItemSave, 400);
}
function flushItemSave() {
  clearTimeout(itemSaveTimer); itemSaveTimer = null;
  if (!itemDirty || !modelName) return;
  itemDirty = false;
  window.api.saveLyricsItem(modelName, lyricsItem).then((r) => { if (r && !r.ok) console.warn('[item] 저장 실패', r.error); }).catch((e) => console.warn('[item] 저장 실패', e));
}
// 편집 모드에서 이 화면 좌표가 아이템 위인가 (그림이 있는 곳만)
function overItem(x, y) {
  if (!editMode || (config.lyrics || {}).item === false) return false;
  return hitMask(itemMask, itemRectNow(), x, y);
}

const LYRICS_POSE_RELEASE_MS = 3000;
// 가사가 나오는 동안(노래 재생 중 + 가사창이 보이는 중)만 그 표정/모션을 쓴다.
// 대사(음성/말풍선) 중에는 대사의 감정이 우선이고, 끝난 뒤에도 가사가 나오고 있으면 다시 적용한다. 가사가 멈추면 몇 초 뒤 중립으로 돌아온다.
function updateLyricsPose(now) {
  const pose = poseOf();
  if (!emotion || !pose || !(pose.expression || pose.motion)) { lyricsPoseOn = false; return; }
  const talking = playing || !!activeSource || bubble.visible;
  if (talking) { lyricsPoseOn = false; lyricsPoseOffAt = 0; return; }
  if (lyrics && lyrics.isPlaying()) {
    lyricsPoseOffAt = 0;
    if (!lyricsPoseOn) lyricsPoseOn = emotion.playPose(pose);
    return;
  }
  if (!lyricsPoseOn) return;
  if (!lyricsPoseOffAt) { lyricsPoseOffAt = now; return; }
  if (now - lyricsPoseOffAt >= LYRICS_POSE_RELEASE_MS) { emotion.releasePose(); lyricsPoseOn = false; lyricsPoseOffAt = 0; }
}

// 지금 모델의 persona / 감정 태그를 서버에 알린다 (서버는 이걸로 프롬프트를 만든다). 접속 직후, 모델이 바뀔 때, 프로필을 저장할 때 부른다.
function pushProfile() {
  if (!(socket && socket.isOpen && modelName && emotion)) return;
  socket.setProfile(serverProfile(modelName, profile, emotion));
}

// 모델 중심(x)과 발밑(y)이 화면 밖으로 완전히 사라지지 않게 오프셋을 제한한다
const baseX = () => window.innerWidth - BASE_RIGHT_MARGIN;
function clampOffset(o) {
  const x = clamp(baseX() + o.x, 20, window.innerWidth - 20) - baseX();
  // 아래로는 머리 끝이 화면 아래쪽 80px 안쪽까지 내려갈 수 있어야 한다 (모델이 클수록 더 많이 내려감)
  const h = model ? model.height : BASE_MODEL_HEIGHT;
  const y = clamp(o.y, 80 - window.innerHeight, Math.max(150, h - 80));
  return { x, y };
}

let sliderBusy = false; // 슬라이더를 만지는 동안 패널이 따라 움직이면 손잡이가 달아난다
function layoutModel() {
  if (!model) return;
  invalidateHitCache();
  model.anchor.set(0.5, 1);
  model.scale.set(1);
  model.scale.set((BASE_MODEL_HEIGHT / model.height) * cfgScale());
  const off = clampOffset(cfgOffset());
  model.x = baseX() + off.x;
  model.y = window.innerHeight + off.y;
  layoutItem();
  positionUI();
  if (editMode && !sliderBusy) positionPanel();
  scheduleMeasure();
}

// ---------- 말풍선/입력창 위치 ----------
// 모델이 실제로 그려진 맨 윗줄(머리카락 끝)을 픽셀로 찾아서, 그 바로 위에 붙인다.
let measureTimer = null;
let measuredHeadTop = null;
let measureBuf = null;

function scheduleMeasure() {
  clearTimeout(measureTimer);
  measureTimer = setTimeout(() => requestAnimationFrame(() => requestAnimationFrame(measureHeadTop)), 200);
}

// 모델 영역만 읽어서(전체 화면을 읽으면 무겁다) 가장 위쪽의 그려진 줄을 찾는다
function measureHeadTop() {
  if (!model || !app) return;
  try {
    const gl = app.renderer.gl;
    const W = gl.drawingBufferWidth;
    const H = gl.drawingBufferHeight;
    const res = app.renderer.resolution;
    const b = model.getBounds();
    const x0 = clamp(Math.floor(b.x * res), 0, W);
    const x1 = clamp(Math.ceil((b.x + b.width) * res), 0, W);
    const yTop = clamp(Math.floor(b.y * res), 0, H); // 화면 좌표(위가 0), 장치 픽셀
    const yBot = clamp(Math.ceil((b.y + b.height) * res), 0, H);
    const w = x1 - x0;
    const h = yBot - yTop;
    if (w > 0 && h > 0) {
      if (!measureBuf || measureBuf.length !== w * h * 4) measureBuf = new Uint8Array(w * h * 4);
      gl.readPixels(x0, H - yBot, w, h, gl.RGBA, gl.UNSIGNED_BYTE, measureBuf); // GL 은 아래가 0
      for (let r = h - 1; r >= 0; r--) { // 버퍼의 마지막 줄이 화면에서 가장 위
        const row = r * w * 4;
        for (let x = 0; x < w; x += 2) {
          if (measureBuf[row + x * 4 + 3] > 16) {
            measuredHeadTop = (yTop + (h - 1 - r)) / res;
            positionUI();
            return;
          }
        }
      }
    }
  } catch (e) {
    console.warn('[ui] 머리 위치 측정 실패', e);
  }
  measuredHeadTop = null;
  positionUI();
}

// 가사창 줄별 글자 크기: 편집 모드에서 따로 정한다. 정하기 전에는 이전 설정(fontSize)이나 대사창 크기와 같게
function lyricsSizes() {
  const l = config.lyrics || {};
  const base = Number(l.fontSize) || cfgUI().fontSize;
  return [1, 2, 3].map((n) => Number(l[`size${n}`]) || base);
}

function positionUI() {
  const u = cfgUI();
  const w = clamp(u.width, 120, Math.max(window.innerWidth - 8, 120));
  const cx = (model ? model.x : baseX()) + u.offsetX;
  const left = clamp(cx - w / 2, 4, Math.max(window.innerWidth - w - 4, 4));
  const headTop = measuredHeadTop ?? (model ? model.getBounds().y : window.innerHeight * 0.3);
  const top = Math.max(headTop + u.offsetY, 110); // 머리가 화면 위쪽이어도 말풍선이 눌려 사라지지 않게 최소 높이 확보

  ui.style.left = `${left}px`;
  ui.style.width = `${w}px`;
  ui.style.bottom = `${window.innerHeight - top + 4}px`;
  ui.style.maxHeight = `${Math.max(top - 8, 100)}px`;
  bubbleEl.style.fontSize = input.style.fontSize = `${u.fontSize}px`;
  lyricsSizes().forEach((px, i) => lyricsEl.style.setProperty(`--fs${i + 1}`, `${px}px`));
  positionSessions();
}

// ---------- 오디오 재생 + 립싱크 ----------
// pixi-live2d-display 0.4.0 에는 speak() 가 없다. WebAudio 로 재생하면서
// AnalyserNode 로 음량을 읽어 입 파라미터(ParamMouthOpenY)에 직접 넣는다.
const audioQueue = [];
let audioCtx = null;
let analyser = null;
let mouthBuf = null;
let activeSource = null;
let activeGain = null; // 재생 중인 음성의 페이드용 GainNode (조각 시작/끝의 '틱' 팝노이즈 방지)
let playing = false; // 디코딩 중에도 true (그 사이에 들어온 음성이 겹쳐 재생되지 않게)
let lastAudioEnd = 0; // 마지막으로 캐릭터 음성이 끝난 시각 (마이크가 잔향을 듣지 않게)
let audioGen = 0;    // stopAudio() 마다 증가. 디코딩이 끝난 뒤 세대가 바뀌었으면 그 음성은 버린다

let audioHint = null; // 지금 AudioContext 를 만들 때 쓴 지연 설정 (설정 창에서 바꾸면 말하는 중이 아닐 때 새로 만든다)

function getAudio() {
  const hint = normalizeAudioLatency(config.audioLatency);
  if (audioCtx && String(hint) !== audioHint && !playing && !activeSource) {
    try { audioCtx.close(); } catch { /* 이미 닫힘 */ }
    audioCtx = null;
    analyser = null;
  }
  if (!audioCtx) {
    // 기본값(interactive)은 버퍼가 아주 작아서 Live2D 그리기/GPU(RVC 등) 부하가 순간적으로 몰리면 소리가 '톡' 끊긴다.
    // playback 은 버퍼를 넉넉히 잡아 그런 팝을 막는다 (입 모양이 수십 ms 늦을 수 있음). 런처 설정 > 음성 > 오디오 버퍼 (config.json 의 audioLatency)
    try { audioCtx = new AudioContext({ latencyHint: hint }); } catch { audioCtx = new AudioContext(); }
    audioHint = String(hint);
    audioCtx.onstatechange = () => console.info('[audio] AudioContext 상태:', audioCtx.state);
    console.info(`[audio] 지연 설정 ${hint} (baseLatency ${audioCtx.baseLatency?.toFixed?.(3)}s, outputLatency ${audioCtx.outputLatency?.toFixed?.(3)}s, ${audioCtx.sampleRate}Hz)`);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    mouthBuf = new Uint8Array(analyser.fftSize);
    analyser.connect(audioCtx.destination);
  }
  return audioCtx;
}

// 지금 말하는 중이거나 대기 중인 음성을 전부 끊는다 (interrupt)
function stopAudio() {
  audioGen++;
  audioQueue.length = 0;
  if (activeSource) {
    const s = activeSource;
    const g = activeGain;
    activeSource = null;
    activeGain = null;
    s.onended = null; // 끊은 것이 다음 음성 재생을 일으키지 않게
    try {
      // 소리가 중간에서 뚝 끊기면 팝노이즈가 나므로 아주 짧게 줄이고 멈춘다
      const t = audioCtx.currentTime;
      if (g) { g.gain.cancelScheduledValues(t); g.gain.setValueAtTime(g.gain.value, t); g.gain.linearRampToValueAtTime(0, t + 0.02); }
      s.stop(g ? t + 0.025 : 0);
    } catch { /* 이미 끝남 */ }
  }
  playing = false;
  lastAudioEnd = performance.now();
  bubble.release('audio');
}

function enqueueAudio(buffer, mime, info = {}) {
  // 음성이 끊겨 들릴 때 원인(서버가 만든 파일 vs 클라이언트 재생)을 가르기 위한 로그
  console.info(`[audio] 수신 ${buffer.byteLength}B` + (info.declared ? ` (content-length ${info.declared})` : '') + (info.note ? ` · ${info.note}` : '') + (info.url ? ` · ${info.url}` : ''));
  audioQueue.push({ buffer, mime });
  bubble.hold('audio'); // 말하는 동안 말풍선이 사라지지 않게
  if (!playing) playNext();
}

async function playNext() {
  const gen = audioGen;
  const item = audioQueue.shift();
  if (!item) {
    playing = false;
    lastAudioEnd = performance.now();
    // 다 말하고 나서 bubbleSeconds 뒤에 사라짐. 가사가 기다리고 있으면 1초 뒤에 사라져서 가사로 돌아간다
    bubble.release('audio', lyrics && lyrics.isActive() ? 1 : undefined, true);
    return;
  }
  playing = true;
  try {
    const ctx = getAudio();
    if (ctx.state === 'suspended') await ctx.resume();
    const decoded = await ctx.decodeAudioData(item.buffer.slice(0));
    if (gen !== audioGen) return; // 디코딩 중에 interrupt 됨: 이 음성은 버린다
    const src = ctx.createBufferSource();
    src.buffer = decoded;
    // 음성 조각(문장)의 처음/끝이 0 이 아닌 값에서 시작/끝나면 이어 붙는 지점마다 '틱' 소리가 난다 (RVC 출력에서 특히). 짧게 키우고 줄인다
    const gain = ctx.createGain();
    const t0 = ctx.currentTime;
    const dur = decoded.duration;
    const fin = Math.min(0.01, dur / 4), fout = Math.min(0.012, dur / 4); // 끝 페이드는 짧게 (길면 끝소리가 깎여 들린다)
    gain.gain.setValueAtTime(0, t0);
    gain.gain.linearRampToValueAtTime(1, t0 + fin);
    gain.gain.setValueAtTime(1, t0 + dur - fout);
    gain.gain.linearRampToValueAtTime(0, t0 + dur);
    src.connect(gain);
    gain.connect(analyser);
    activeGain = gain;
    const startedAt = performance.now();
    src.onended = () => {
      const played = (performance.now() - startedAt) / 1000;
      // 정상이면 재생 시간 ≈ 음성 길이. 훨씬 짧으면 재생 도중 끊긴 것
      console.info(`[audio] 재생 끝 ${played.toFixed(1)}s / 음성 ${decoded.duration.toFixed(1)}s`);
      if (played < decoded.duration - 0.5) console.warn('[audio] 음성 길이보다 일찍 끝남: 재생 도중 끊김', ctx.state);
      activeSource = null;
      activeGain = null;
      playNext();
    };
    activeSource = src;
    console.info(`[audio] 재생 시작 ${decoded.duration.toFixed(1)}s ${decoded.sampleRate}Hz ctx=${ctx.state}`);
    src.start();
  } catch (e) {
    if (gen !== audioGen) return;
    console.warn('[audio] 재생 실패', e);
    activeSource = null;
    playNext();
  }
}

// 모델이 로드될 때마다 입 파라미터 쓰기 함수를 만들고, 모델 업데이트 직전에 값을 넣는다.
function setupLipSync(m, settings) {
  const groups = Array.isArray(settings.Groups) ? settings.Groups : [];
  const lip = groups.find((g) => g && g.Name === 'LipSync');
  const ids = lip && Array.isArray(lip.Ids) && lip.Ids.length ? lip.Ids : ['ParamMouthOpenY'];
  const writer = createMouthWriter(m.internalModel.coreModel, ids);
  if (!writer.ok) { console.warn('[lipsync] 입 파라미터를 찾지 못했어요:', ids.join(',')); return; }

  let mouth = 0;
  let logged = false;
  m.internalModel.on('beforeModelUpdate', () => {
    if (activeSource && analyser) {
      analyser.getByteTimeDomainData(mouthBuf);
      mouth = nextMouth(mouth, rmsOf(mouthBuf), config.lipSyncGain || 5);
    } else if (!playing && !audioQueue.length && lyrics && (config.lyrics || {}).lipSync !== false) {
      // 음성이 나오지 않을 때만 가사 박자로 노래하는 입모양 (음성 재생/디코딩/대기 중에는 항상 음성이 우선)
      mouth = smoothMouth(mouth, lyrics.singLevel());
    } else {
      mouth = nextMouth(mouth, 0, 1);
    }
    // 말하는 중이 아닐 때는 건드리지 않는다 (표정이 입을 벌리는 경우를 덮어쓰지 않도록)
    if (activeSource || mouth > 0) writer.write(mouth);
    if (!logged && mouth > 0.05) { logged = true; console.info('[lipsync] 입 움직임 적용 중', ids.join(',')); }
  });
}

// ---------- 시선 추적 (마우스 포인터를 따라 눈/고개/몸을 돌린다) ----------
// 포인터 위치는 메인 프로세스가 알려준다(onCursor). 계산/적용은 gaze.js. 모션이 정한 값에 더해서 쓴다.
const gaze = new GazeController();
let gazeHead = null;   // 모델의 머리 위치(화면 좌표). 0.1초마다 갱신
let gazeHeadAt = 0;

function setupGaze(m) {
  const writer = createGazeWriter(m.internalModel.coreModel);
  if (!writer.ok) { console.warn('[gaze] 시선 파라미터를 찾지 못했어요'); return; }
  console.info('[gaze] 사용 파라미터:', writer.names.join(','));
  let last = performance.now();
  m.internalModel.on('beforeModelUpdate', () => {
    const now = performance.now();
    const dt = Math.min(now - last, 100); // 프레임이 오래 끊겨도 한 번에 튀지 않게
    last = now;
    if (!gaze.o.enabled && gaze.settled) return;
    if (!gazeHead || now - gazeHeadAt > 100) {
      const b = m.getBounds();
      gazeHead = { x: b.x + b.width / 2, y: b.y + b.height * 0.16 };
      gazeHeadAt = now;
    }
    const { eye, head } = gaze.step(dt, gazeHead, now);
    if (!gaze.settled) writer.apply(eye, head);
  });
}

// ---------- 서버 응답 처리 ----------
const trunc = (s, n) => (s.length > n ? `${s.slice(0, n)}…` : s);

// 응답 텍스트에서 <think> 속마음을 분리하고, 그 내용을 단서로 표정을 고른다.
// think 에서 단서를 못 찾으면 서버의 감정 라벨(KoELECTRA)로 폴백한다.
function applyResponse(raw) {
  const { spoken, think } = splitThink(raw);
  // 행동 묘사(💭)와 감정 태그는 각각 자기 설정(showThink / showEmotionTag)으로만 켜고 끈다. 둘은 서로 영향을 주지 않는다.
  const thinkLines = [];
  const emoLines = [];
  if (think && config.showThink !== false) thinkLines.push(`💭 ${trunc(think, 140)}`);

  if (emotion) {
    lyricsPoseOn = false;   // 대사의 감정을 적용했으니 가사창 표정은 가사가 이어질 때 다시 적용
    let applied = '';
    const label = think ? emotion.matchText(think) : null;
    if (label && emotion.play(label)) {
      applied = `${label} → ${emotion.lastMatch} (think)`;
    } else if (pendingEmotion && emotion.play(pendingEmotion)) {
      applied = `${pendingEmotion} → ${emotion.lastMatch} (서버 라벨${think ? ', think 단서 없음' : ''})`;
    } else {
      applied = `매칭 없음 (think: ${think ? '단서 없음' : '없음'}, 서버: ${pendingEmotion || '없음'})`;
      if (pendingEmotion) console.info(`[emotion] 매핑 없음: "${pendingEmotion}" / 사용 가능: ${emotion.list().join(',')}`);
    }
    if (config.showEmotionTag !== false) emoLines.push(`감정: ${applied}`);
  }
  pendingEmotion = '';
  return { spoken, tag: [...thinkLines, ...emoLines].join('\n') };
}

// 서버가 보내준 대화 글을 파일로 저장한다 (저장 위치는 사용자에게 물어본다)
async function exportToFile(ev) {
  const r = await window.api.saveText(ev.filename, ev.text);
  if (r && r.ok) say(`💾 대화 ${ev.count}개 메시지를 저장했어요.\n${r.path}`);
  else if (r && r.canceled) say('저장을 취소했어요.');
  else say(`저장하지 못했어요: ${(r && r.error) || '알 수 없는 오류'}`);
}

function handleEvent(ev) {
  // 중단 버튼을 누른 뒤 서버가 알아채기 전에 도착한 조각은 멈춘 대답의 것이므로 버린다
  if (awaitInterruptAck && (DROP_TYPES.has(ev.type) || ((ev.type === 'audio' || ev.type === 'text') && DROP_OPS.has(ev.op)))) return;
  switch (ev.type) {
    case 'interrupted':   // 서버가 멈췄다고 알려 옴 (이 뒤에 오는 이벤트는 새 대화의 것)
      awaitInterruptAck = false; clearTimeout(ackTimer);
      stopAudio(); stream.end(); pendingEmotion = '';
      setAwaiting(false);
      break;
    case 'emotion': pendingEmotion = ev.emotion; break; // 다음 text 이벤트에서 함께 처리
    case 'stream_start':   // 답이 만들어지기 시작: 이전 음성은 끊고, 도착하는 조각을 말풍선에 이어 붙인다
      stopAudio();
      pendingEmotion = '';
      stream.start(ev.id);
      bubble.show('…');
      break;
    case 'stream_search':   // 서버가 웹 검색 중 (결과가 오면 곧 답이 이어진다)
      if (!stream.accepts(ev.id)) break;
      bubble.show(`🔍 "${ev.query}" 검색 중…`);
      positionUI();
      break;
    case 'stream_meta': {  // 감정/속마음이 정해짐: 대사보다 먼저 표정을 바꾼다
      if (!stream.accepts(ev.id)) break;
      pendingEmotion = ev.emotion || '';
      stream.tag = applyResponse(ev.think ? `<think>${ev.think}</think>` : '').tag;
      bubble.show(stream.spoken || '…', stream.tag, false, true);
      positionUI();
      break;
    }
    case 'stream_text':
      if (!stream.accepts(ev.id)) break;
      bubble.show(stream.text(ev.delta), stream.tag, false, true);
      positionUI();
      break;
    case 'text': {
      if (ev.streamed) {   // 이미 조각으로 보여주고 말한 답: 최종 글로 바꾸기만 한다 (음성/표정은 다시 건드리지 않는다)
        pendingEmotion = '';
        bubble.show(splitThink(ev.text).spoken, stream.tag, false, true);
        stream.end();
        setAwaiting(false);   // 답이 끝남: 이제 남은 건 음성 재생(playing)이 막아 준다
        positionUI();
        break;
      }
      // 내 메시지에 대한 새 응답이 오면 이전 음성은 끊는다. 서버가 먼저 거는 말(observe/notification)은 끊지 않고 이어서 말한다.
      if (ev.op === 'chat_result') stopAudio();
      const { spoken, tag } = applyResponse(ev.text);
      bubble.show(spoken, tag, false, true);
      if (ev.op === 'chat_result') setAwaiting(false);
      positionUI();
      break;
    }
    case 'audio': enqueueAudio(ev.data, ev.mime, ev); break;
    case 'status': pendingEmotion = ''; bubble.show('…'); break;
    case 'sessions': {
      const prevActive = sessionPanel.activeId;
      const prevCount = sessionPanel.countOf(ev.active);
      sessionPanel.setSessions(ev.sessions, ev.active);
      refreshPlaceholder();
      // 열려 있는 기록이 낡았으면(새 메시지/알림이 쌓였거나 새 대화가 저장됨) 다시 받는다
      if (sessionPanel.isOpen && ev.active != null && (prevActive !== ev.active || prevCount !== sessionPanel.countOf(ev.active))) socket.loadMessages(ev.active);
      break;
    }
    case 'session_active':
      stopAudio();
      sessionPanel.setActive(ev.id, ev.title, ev.messages);
      refreshPlaceholder();
      bubble.show(ev.id == null ? '새 대화예요. 아무 말이나 걸어 보세요.' : `「${ev.title}」 대화로 바꿨어요.`);
      positionUI();
      break;
    case 'session_messages': sessionPanel.setMessages(ev.id, ev.messages); break;
    case 'session_search_result': sessionPanel.setSearchResults(ev.query, ev.results); break;
    case 'session_export': exportToFile(ev); break;
    case 'memories': settingsDlg.setMemories(ev.memories); break;
    case 'anniversaries': lastAnniv = ev.anniversaries || []; say(annivText(lastAnniv)); break;
    case 'error':
      stream.end();
      setAwaiting(false);
      // 기억 창에서 일어난 오류는 그 창 안에 보여 준다 (말풍선에 띄우지 않는다)
      if (ev.code === 'memory_error') settingsDlg.setMemoryError(ev.message);
      else bubble.show(`서버 오류: ${ev.message}`);
      break;
  }
}

// ---------- 입력창 + 대사창: 모델 위에 마우스를 올리면 나타남 (사라진 마지막 대사도 다시 보인다) ----------
let hideTimer = null;
let pendingShot = null; // 캡처 후 아직 안 보낸 이미지 { blob, url }

function showInput() {
  clearTimeout(hideTimer);
  hideTimer = null; // 비우지 않으면 scheduleHideInput 이 영원히 예약을 건너뛴다
  if (inputBar.style.display !== 'block') inputBar.style.display = 'block';
  bubble && bubble.hoverOn();   // 사라진 대사창도 다시 보여 준다 (올려 둔 동안 유지)
  syncPendingUI();
  // 주의: 여기서 input.focus() 를 부르지 않는다. 호버만으로 다른 앱의 포커스를 뺏으면 안 되므로,
  // 사용자가 입력창을 직접 클릭했을 때만 포커스가 잡힌다.
}

function hideInput() {
  clearTimeout(hideTimer);
  hideTimer = null;
  inputBar.style.display = 'none';
  bubble && bubble.hoverOff();
  syncPendingUI();
}

function scheduleHideInput() {
  if (hideTimer || editMode) return; // 이미 예약됨 / 편집 중에는 항상 표시
  hideTimer = setTimeout(() => {
    hideTimer = null;
    if (editMode) return; // 편집 모드로 들어갔으면 숨기지 않는다
    if (document.activeElement === input || input.value || pendingShot) return; // 입력 중/캡처 대기 중이면 유지
    hideInput();
  }, 700);
}

// 표정 매핑을 맞출 때 쓰는 로컬 명령 (서버로 보내지 않음)
//   /emo          사용 가능한 표정/핫키 이름 목록
//   /emo <이름>   그 표정을 바로 적용 (서버 라벨이나 별칭도 가능: /emo happy)
//   /emo think <문장>  그 문장을 <think> 내용으로 가정하고 어떤 표정이 고른지 확인
function emoCommand(arg) {
  if (!emotion) { bubble.show('모델이 아직 로드되지 않았어요.'); return; }
  if (!arg || arg === 'list') {
    bubble.show(`[${modelName}] 사용 가능한 이름:\n${emotion.list().join(', ')}\n\n/emo 이름  → 표정 미리보기\n/emo think *smiles*  → think 매칭 테스트`);
    return;
  }
  if (arg.startsWith('think ')) {
    const text = arg.slice(6);
    const label = emotion.matchText(text);
    const ok = label && emotion.play(label);
    bubble.show(text, ok ? `💭 매칭: ${label} → ${emotion.lastMatch}` : '💭 매칭: 단서 없음 (서버 라벨로 폴백될 것)');
    return;
  }
  const ok = emotion.play(arg);
  bubble.show(ok ? `적용: ${arg}` : `"${arg}" 는 매핑/표정에 없어요. /emo 로 목록을 확인하세요.`, ok ? `감정: ${emotion.lastMatch}` : '');
}

btnSessions.addEventListener('click', () => sessionPanel.toggle());
btnStop.addEventListener('click', () => interruptReply());
setInterval(() => { try { btnStop.classList.toggle('active', isReplying()); } catch { /* 아직 초기화 전 */ } }, 200);   // 생각/말하는 중에만 켜 둔다
input.addEventListener('input', () => proactive.noteUserActivity()); // 입력 중에는 끼어들지 않는다
window.addEventListener('keydown', (e) => {
  // 패널이 열려 있고 글자 입력 중이 아닐 때 Esc 로 닫는다 (이름 바꾸기/메시지 입력 중의 Esc 는 각자 처리)
  if (e.key === 'Escape' && !(e.target instanceof HTMLInputElement)) {
    if (lyricsPicker.isOpen) lyricsPicker.close();
    else if (settingsDlg.isOpen) settingsDlg.close();
    else if (sessionPanel.isOpen) sessionPanel.close();
  }
});

// 대사창 글자를 드래그해서 복사할 수 있다. 글자를 선택해 두는 동안에는 대사창이 사라지지 않고, 선택이 풀리면 평소처럼 사라진다.
document.addEventListener('selectionchange', () => {
  if (!bubble) return;
  const sel = window.getSelection();
  const inBubble = !!sel && !sel.isCollapsed && !!sel.anchorNode && bubbleEl.contains(sel.anchorNode);
  if (inBubble) bubble.hold('select');
  else if (bubble.holds.has('select')) bubble.release('select');
});

input.addEventListener('blur', () => scheduleHideInput());
input.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (pendingShot) clearPendingShot(); // 캡처 첨부도 함께 취소
    input.value = ''; input.blur(); hideInput(); return;
  }
  if (e.key === 'Enter' && !e.isComposing) {
    const text = input.value.trim();
    if (!text && !pendingShot) return;
    input.value = '';
    if (pendingShot) sendPendingShot(text); // 캡처 + 방금 친 메시지를 함께 전송 (글이 없으면 기본 문구)
    else submitText(text);
  }
});
$('pendingX').addEventListener('click', () => { clearPendingShot(); input.focus(); });

// ---------- 전송 (+ 화면 캡처) ----------
let capturing = false;
let selecting = false; // 부분 캡처 영역 고르는 중: 창이 마우스를 전부 받는다
const selectLayer = $('selectLayer');

const DEFAULT_CAPTURE_PROMPT = '지금 내 화면이야. 보고 한마디 해줘.';

// 캡처 -> 잘라낸 JPEG blob. mode: 'full' | 'partial'. 취소하면 null
async function grabBlob(mode) {
  const shot = await window.api.captureScreen();
  let rect = null;
  if (mode === 'partial') {
    selecting = true;
    hideInput();
    setIgnore(false);
    window.api.focusWindow();
    try { rect = await selectRegion(selectLayer, $('selBox'), $('selHint')); }
    finally { selecting = false; applyIgnoreMode(); }
    if (!rect) return null;
  }
  const { blob, width, height } = await cropToBlob(shot, rect);
  console.info('[capture]', mode, `${width}x${height}`, `${Math.round(blob.size / 1024)}KB`);
  return blob;
}

// 캡처 -> 업로드 -> image_id (화면 공유 모드용). 취소하면 null
async function grabImage(mode) {
  const blob = await grabBlob(mode);
  return blob ? socket.uploadImage(blob) : null;
}

// ---- 캡처 대기: 찍은 이미지를 들고 있다가, 사용자가 메시지를 입력하고 Enter 를 누를 때 같이 보낸다 ----
function syncPendingUI() {
  const box = $('pendingShot');
  box.style.display = pendingShot && inputBar.style.display === 'block' ? 'flex' : 'none';
}

function setPendingShot(blob) {
  clearPendingShot();
  pendingShot = { blob, url: URL.createObjectURL(blob) };
  $('pendingImg').src = pendingShot.url;
  input.placeholder = '📸 캡처 첨부됨 · 메시지 입력 후 Enter (Esc로 취소)';
  showInput();
  positionUI();
}

function clearPendingShot() {
  if (!pendingShot) return;
  URL.revokeObjectURL(pendingShot.url);
  pendingShot = null;
  $('pendingImg').removeAttribute('src');
  refreshPlaceholder();
  syncPendingUI();
}

async function sendPendingShot(typed) {
  const shot = pendingShot;
  if (!shot || !socket) return;
  proactive.noteUserActivity();
  clearPendingShot();
  stopAudio();
  try {
    bubble.show('📸 화면을 보내는 중…', '', true);
    const id = await socket.uploadImage(shot.blob);
    const text = typed || config.capturePrompt || DEFAULT_CAPTURE_PROMPT;
    if (!socket.sendText(text, [id], await nowPlayingFor(text), await contextFor(text))) bubble.show('서버에 연결되어 있지 않아요.', '', true);
    else if (bubble.enabled) bubble.show('📸 화면을 보냈어요…');
    else bubble.hide();
  } catch (e) {
    console.warn('[capture] 전송 실패', e);
    bubble.show(`캡처 전송 실패: ${e.message || e}`, '', true);
  }
}

// 곡에 대해 묻는 말이면 지금 재생 중인 곡 정보를 같이 보낸다 (설정에서 끌 수 있음). 아니면 undefined
// 마지막으로 서버에 알려 준 곡. 곡 얘기를 한 뒤 곡이 바뀌었으면, 질문이 패턴에 안 걸려도(이번 곡은? 등) 새 곡 정보를 한 번 더 보낸다.
let lastNowPlayingKey = null;
async function nowPlayingFor(text) {
  if (config.nowPlayingContext === false) return undefined;
  const asks = asksAboutMusic(text);
  if (!asks && lastNowPlayingKey == null) return undefined;
  try {
    const np = nowPlayingPayload(await window.api.getNowPlaying());
    if (!np) return undefined;
    const key = `${np.app}|${np.title}|${np.artist}`;
    if (!asks && key === lastNowPlayingKey) return undefined;
    lastNowPlayingKey = key;
    return np;
  }
  catch (e) { console.warn('[now-playing] 곡 정보를 못 읽었어요', e); return undefined; }
}

// 이번 턴에만 AI 에게 같이 보낼 부가 정보: 예정된 일정(있으면 항상, 설정에서 끌 수 있음), 클립보드(가리키는 말을 했을 때만). 없으면 undefined
async function contextFor(text) {
  const ctx = {};
  if (config.scheduleContext !== false) {
    try {
      const now = Date.now();
      const sch = schedulePayload(await window.api.scheduleList(), now, { when: fmtWhen, left: fmtLeft, repeat: fmtRepeat });
      if (sch.length) ctx.schedules = sch;
    } catch (e) { console.warn('[context] 일정을 못 읽었어요', e); }
  }
  if (config.clipboardContext !== false && asksAboutClipboard(text)) {
    try {
      const clip = clipText(await window.api.readClipboard(), config.clipboardMaxChars || CLIP_DEFAULT_MAX);
      ctx.clipboard = clip || '(클립보드가 비어 있어요. 글자가 복사돼 있지 않아요.)';
    } catch (e) { console.warn('[context] 클립보드를 못 읽었어요', e); }
  }
  return Object.keys(ctx).length ? ctx : undefined;
}

// 일반 전송. 화면 공유 모드면 전체 화면을 같이 보낸다.
async function sendChat(text) {
  stopAudio(); // 말하는 도중에 새 메시지를 보내면 바로 끊는다
  setAwaiting(true);   // 답(생각 + 음성)이 끝날 때까지 마이크 입력을 막는다
  let ids;
  if (config.screenShare === true) {
    bubble.show('👀 화면 읽는 중…');
    try { ids = [await grabImage('full')]; }
    catch (e) { console.warn('[capture] 화면 공유 캡처 실패', e); bubble.show(`화면을 못 읽었어요: ${e.message || e}\n글자만 보낼게요.`); }
  }
  const np = await nowPlayingFor(text);
  const ctx = await contextFor(text);
  if (!socket.sendText(text, ids, np, ctx)) { setAwaiting(false); bubble.show('서버에 연결되어 있지 않아요.'); }
}

// 캡처 단축키/메뉴: 찍기만 하고 바로 보내지 않는다. 입력창에 캡처가 첨부되면 메시지를 입력하고 Enter 로 보낸다.
async function captureAndSend(mode) {
  if (capturing || selecting) return;
  if (!socket) return;
  capturing = true;
  try {
    bubble.show(mode === 'partial' ? '📸 보낼 영역을 고르세요' : '📸 화면 캡처 중…', '', true);
    const blob = await grabBlob(mode);
    if (!blob) { bubble.hide(); return; }
    bubble.hide();
    setPendingShot(blob);
    window.api.focusWindow();
    input.focus(); // 바로 타이핑할 수 있게 (사용자가 캡처를 요청한 시점이라 포커스를 가져와도 된다)
  } catch (e) {
    console.warn('[capture] 실패', e);
    bubble.show(`캡처 실패: ${e.message || e}`, '', true);
  } finally {
    capturing = false;
  }
}

// ---------- 실시간 가사 ----------
// 곡 정보/가사 찾기는 메인 프로세스(native/lyrics-service.js)가 하고, 여기서는 받아서 그리기만 한다.
function applyLyricsOptions() {
  if (!lyrics) return;
  const c = config.lyrics || {};
  lyrics.setOptions({ enabled: c.enabled !== false, layout: c.layout === 'next' ? 'next' : 'auto', lipSpeed: c.lipSpeed ?? 1, line2Opacity: c.line2Opacity ?? 0.45, background: c.background !== false, hidePron: c.hidePron === true, color1: c.color1 || '', color2: c.color2 || '', color3: c.color3 || '' });
  positionUI();
}

function setupLyrics() {
  lyrics = new LyricsView(lyricsEl, () => bubble.visible);
  applyLyricsOptions();
  if (!window.api.onLyricsTrack) return;   // 이전 preload 로 떠 있으면 건너뜀
  window.api.onLyricsTrack((t) => { lyrics.onTrack(t); lyricsPicker.onTrackChanged(t); });
  window.api.onLyricsLines((m) => lyrics.onLines(m));
  window.api.onLyricsPlayback((p) => lyrics.onPlayback(p));
  window.api.onLyricsOffset((m) => lyrics.onOffset(m));
  window.api.onLyricsIdle(() => lyrics.onIdle());
  window.api.onLyricsFlash((m) => lyrics.flash(m.text));
  // 앱이 켜진 뒤에 렌더러가 늦게 뜨면 이미 시작된 곡을 놓치므로 현재 상태를 한 번 받아 온다
  window.api.lyricsSnapshot().then((snap) => {
    if (!snap || !snap.track) return;
    lyrics.onTrack(snap.track);
    if (snap.lyrics) lyrics.onLines(snap.lyrics);
    if (snap.playback) lyrics.onPlayback(snap.playback);
  }).catch(() => {});
  setInterval(() => { lyrics.update(Date.now()); updateLyricsPose(Date.now()); updateLyricsItem(Date.now()); }, 80);
}

// /가사                 지금 곡/출처/싱크 보정 보기
// /가사 다시            다시 찾기
// /가사 선택            후보 목록에서 직접 고르기 (같은 곡이 여러 개일 때)   /가사 자동  직접 고른 걸 버리고 자동으로
// /가사 가수 - 제목     그 검색어로 직접 찾기 (자동 보정 없이)  예: /가사 아이유 - 라일락
// /가사 +0.5 | -0.5     싱크 보정 (양수 = 가사가 더 일찍)       /가사 0  으로 초기화
// /가사 번역          번역을 다시 받기 (원문만 있는 가사에 서버 LLM 번역을 덧붙임)
// /가사 끄기 | 켜기
async function lyricsCommand(arg) {
  if (!window.api.lyricsCommand) { say('가사 기능을 쓰려면 앱을 다시 시작해 주세요.'); return; }
  let r;
  if (!arg) r = await window.api.lyricsCommand('status');
  else if (/^(선택|고르기|목록|후보|pick)$/i.test(arg)) { lyricsPicker.open(); return; }
  else if (/^(자동|auto)$/i.test(arg)) r = await window.api.lyricsCommand('auto');
  else if (/^(다시|재검색|refresh)$/i.test(arg)) r = await window.api.lyricsCommand('refresh');
  else if (/^(번역|translate)$/i.test(arg)) r = await window.api.lyricsCommand('translate');
  else if (/^(끄기|off)$/i.test(arg) || /^(켜기|on)$/i.test(arg)) {
    const on = /^(켜기|on)$/i.test(arg);
    await window.api.applyConfig({ lyrics: { ...(config.lyrics || {}), enabled: on } });
    say(on ? '🎵 가사를 켰어요.' : '🎵 가사를 껐어요.');
    return;
  } else if (/^[+-]?\d+(\.\d+)?$/.test(arg)) {
    const n = Number(arg);
    r = /^[+-]/.test(arg) ? await window.api.lyricsCommand('offset', n) : n === 0 ? await window.api.lyricsCommand('offset-reset') : await window.api.lyricsCommand('offset', n);
  } else r = await window.api.lyricsCommand('search', arg.replace(/^\?\?\s*/, ''));
  say(r.message);
  // 결과 안내는 대사가 나오지 않아도 저절로 사라져야 한다 (마우스가 모델 위에 있어도). 사라지면 가사창이 다시 보인다
  bubble.expireIn(!arg ? 8 : r.ok ? 2.5 : 5, true);
}

// ---------- 사용자 입력 처리 (키보드/마이크 공통) ----------
// 미디어 제어/일정 같은 로컬 명령이면 여기서 처리하고, 아니면 서버로 보낸다.
async function submitText(text) {
  text = String(text || '').trim();
  if (!text) return;
  proactive.noteUserActivity(); // 사용자가 말했으니 '먼저 말 걸기' 시간을 처음부터 다시 센다
  if (text.startsWith('/emo')) { emoCommand(text.slice(4).trim()); return; }
  if (/^\/(가사|lyrics)(\s|$)/i.test(text)) { await lyricsCommand(text.replace(/^\/(가사|lyrics)\s*/i, '').trim()); return; }
  let cmd = null;
  try { cmd = parseCommand(text, new Date()); } catch (e) { console.warn('[cmd] 해석 실패', e); }
  if (cmd) { await runLocalCommand(cmd); return; }
  await sendChat(text); // 말 끊기 + (화면 공유 모드면) 화면 첨부 + 전송
}

// 로컬 명령(미디어/일정/마이크)의 응답은 음성이 없으니, 대사창을 꺼 둔 상태에서도 보이게 force 로 띄운다
function say(text, tag = '') { bubble.show(text, tag, true); positionUI(); }

let lastAnniv = [];   // 마지막으로 받은 기념일 목록 (번호로 지우려고)
const annivText = (list) => (list && list.length
  ? `🎂 기념일\n${list.map((a, i) => `${i + 1}. ${a.month}월 ${a.day}일 ${a.label}${a.year ? ` (${a.year}년~)` : ''}`).join('\n')}\n\n삭제: /기념일 삭제 번호`
  : '등록된 기념일이 없어요.\n등록: /기념일 3월 5일 생일  (대화 중에 말해도 알아서 기억해요)');
const schedLine = (s, i) => `${i + 1}. ${fmtWhen(s.at)}${s.repeat ? ` (${fmtRepeat(s.repeat, s.weekday)})` : ''} — ${s.text}`;

async function runLocalCommand(cmd) {
  console.info('[cmd]', JSON.stringify(cmd));
  try {
    switch (cmd.kind) {
      case 'media': {
        const r = await window.api.media(cmd.action, cmd.value);
        say(r.message);
        if (lyrics && lyrics.isActive()) bubble.expireIn(1.5);   // 가사가 떠 있으면 결과 안내는 금방 치우고 가사로 돌아간다
        break;
      }
      case 'media-timer': {
        await window.api.scheduleAdd({
          at: cmd.at, text: `🎵 ${cmd.label}`, repeat: cmd.repeat, weekday: cmd.weekday,
          media: { action: cmd.action, value: cmd.value },
        });
        const rep = fmtRepeat(cmd.repeat, cmd.weekday);
        const left = cmd.repeat ? '' : fmtLeft(cmd.at - Date.now());
        say(`🎵 ${left ? `${left} ` : ''}(${fmtWhen(cmd.at)}${rep ? `, ${rep}` : ''})에 ${cmd.label} 할게요.\n취소: "음악 예약 취소"`);
        break;
      }
      case 'media-timer-cancel': {
        const list = (await window.api.scheduleList()).filter((s) => s.media);
        if (!list.length) { say('예약된 미디어 명령이 없어요.'); break; }
        for (const s of list) await window.api.scheduleRemove(s.id);
        say(`🎵 미디어 예약 ${list.length}개를 취소했어요.`);
        break;
      }
      case 'schedule-add': {
        if (cmd.error === 'past') { say('이미 지난 시각이에요. 시각을 다시 말해 주세요.'); break; }
        await window.api.scheduleAdd({ at: cmd.at, text: cmd.text, repeat: cmd.repeat, weekday: cmd.weekday });
        const rep = fmtRepeat(cmd.repeat, cmd.weekday);
        say(`⏰ ${fmtWhen(cmd.at)}${rep ? ` (${rep})` : ''}에 알려드릴게요.\n내용: ${cmd.text}`);
        break;
      }
      case 'proactive': {
        const cur = normalizeProactive(config.proactive);
        const patch = {};
        if (cmd.enabled != null) patch.enabled = cmd.enabled;
        if (cmd.minutes) { patch.minMinutes = cmd.minutes; patch.maxMinutes = Math.ceil(cmd.minutes * 1.5); }
        if (cmd.factor) { // 더 자주(x0.5) / 덜(x2): 지금 간격을 비율로 조절
          if (!cur.enabled) { say('💬 먼저 말 걸기가 꺼져 있어요. "먼저 말 걸어줘"로 먼저 켜 주세요.'); break; }
          const clamp = (v) => Math.min(Math.max(Math.round(v), 1), 1440);
          patch.minMinutes = clamp(cur.minMinutes * cmd.factor);
          patch.maxMinutes = Math.max(patch.minMinutes, clamp(cur.maxMinutes * cmd.factor));
        }
        if (!Object.keys(patch).some((k) => patch[k] !== cur[k])) {
          say(cmd.factor ? `💬 더는 못 바꿔요. (지금 ${describeProactive(cur)})`
            : cur.enabled ? `💬 먼저 말 걸기는 이미 켜져 있어요. (${describeProactive(cur)})` : '💬 먼저 말 걸기는 이미 꺼져 있어요.');
          break;
        }
        await window.api.proactiveSet(patch); // 저장되면 config:changed 로 되돌아와서 안내 말풍선이 뜬다
        break;
      }
      case 'anniv-list': socket.listAnniversaries(); break;
      case 'anniv-add': socket.addAnniversary(cmd.month, cmd.day, cmd.label, cmd.year); break;
      case 'anniv-delete': {
        const a = lastAnniv[cmd.index - 1];
        if (!a) { say('먼저 /기념일 로 목록을 보고, 지울 번호를 골라 주세요.'); break; }
        socket.deleteAnniversary(a.id);
        break;
      }
      case 'schedule-list': {
        const list = await window.api.scheduleList();
        say(list.length ? `${list.map(schedLine).join('\n')}\n\n취소: /알림 취소 번호 (전체: /알림 전체 취소)` : '등록된 일정이 없어요.');
        break;
      }
      case 'schedule-cancel': {
        const list = await window.api.scheduleList();
        if (!list.length) { say('등록된 일정이 없어요.'); break; }
        if (cmd.all) { await window.api.scheduleRemove('all'); say('일정을 모두 취소했어요.'); break; }
        const item = cmd.index ? list[cmd.index - 1] : null;
        if (!item) { say(`몇 번 일정을 취소할까요?\n${list.map(schedLine).join('\n')}\n\n예: /알림 취소 1`); break; }
        await window.api.scheduleRemove(item.id);
        say(`취소했어요: ${item.text}`);
        break;
      }
    }
  } catch (e) {
    console.warn('[cmd] 처리 실패', e);
    say(`명령을 처리하지 못했어요. (${e.message || e})`);
  }
}

// ---------- 일정 알림 ----------
function chime() {
  try {
    const ctx = getAudio();
    if (ctx.state === 'suspended') ctx.resume();
    const t0 = ctx.currentTime;
    [880, 1175].forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      const t = t0 + i * 0.18;
      o.type = 'sine'; o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.4);
      o.connect(g); g.connect(ctx.destination); // 입 모양이 움직이지 않게 analyser 를 거치지 않는다
      o.start(t); o.stop(t + 0.45);
    });
  } catch (e) { console.warn('[chime] 실패', e); }
}

// 예약된 미디어 명령 실행 ("30분 뒤에 음악 꺼줘"). 알림음/음성 없이 조용히 실행하고 결과만 말풍선으로 알린다.
async function handleMediaTimer(s) {
  if (s.missed) { say(`🎵 앱이 꺼져 있는 동안 예약이 지나가서 실행하지 못했어요: ${s.text}`); return; }
  try {
    const r = await window.api.media(s.media.action, s.media.value);
    say(`⏰ ${s.text}\n${r.message}`);
    if (lyrics && lyrics.isActive()) bubble.expireIn(1.5);
  } catch (e) {
    console.warn('[media-timer] 실행 실패', e);
    say(`예약한 미디어 명령을 실행하지 못했어요. (${e.message || e})`);
  }
}

// 남은 시간 표시: "30분 뒤", "1시간 30분 뒤"
function fmtLeft(ms) {
  const min = Math.max(1, Math.round(ms / 60e3));
  if (min < 60) return `${min}분 뒤`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h >= 24 ? '' : `${h}시간${m ? ` ${m}분` : ''} 뒤`;
}

function handleScheduleFire(s) {
  if (s.media) { handleMediaTimer(s); return; }
  console.info('[schedule] 시각 도달:', s.text);
  const sc = config.schedule || {};
  if (sc.chime !== false) chime();
  bubble.show(`⏰ ${s.text}${s.missed ? '\n(앱이 꺼져 있는 동안 지나간 알림이에요)' : ''}`, '', true);
  bubble.hold('schedule');
  setTimeout(() => bubble.release('schedule'), 15000);
  positionUI();
  // 서버에 넘기면 캐릭터 말투로 음성까지 알려준다. 서버가 말하지 않기로 하거나 연결이 없어도 위 말풍선은 남는다.
  if (sc.speakViaServer !== false && !s.missed) {
    socket.sendNotification('일정', `사용자가 미리 부탁해 둔 일정 알림 시각이 됐어. 내용: "${s.text}". 자연스럽게 말해서 알려줘.`);
  }
}

// ---------- 마이크 ----------
// 켜 두면 끌 때까지 계속 듣는다 (Ctrl+Alt+M 으로 켜고 끔). 말이 끝날 때마다 글자로 바꿔 키보드로 친 것과 똑같이 처리한다.
const micBadge = $('micBadge');
let mic = null;
let micBusy = false;       // 방금 한 말을 글자로 바꾸고 보내는 중 ('먼저 말 걸기'가 이때는 끼어들지 않는다)
// 마이크는 한 번에 한 말씩만 처리한다: 듣는 중 -> (말이 끝남) 인식 -> 생각 -> 말하기(TTS) -> 끝난 뒤 0.6초 까지는 새 마이크 입력을 받지 않는다.
let awaitingReply = false; // 메시지를 보내고 답이 끝나기를 기다리는 중 (생각 중). 음성이 도착하면 재생 중 조건(playing)이 이어서 막는다
let awaitTimer = null;
const AWAIT_MAX_MS = 120000;   // 서버가 끝내 답을 못 보내도 마이크가 영영 잠기지 않게 하는 안전장치
function setAwaiting(v) {
  awaitingReply = !!v;
  clearTimeout(awaitTimer); awaitTimer = null;
  if (awaitingReply) awaitTimer = setTimeout(() => setAwaiting(false), AWAIT_MAX_MS);
  refreshBadge('listening');
}

// ---- 중단(interfere): 생각 중이거나 말하는 중인 대답을 멈춘다 (서버에도 알려서 답 만들기/음성 만들기를 멈추게 한다) ----
let awaitInterruptAck = false;   // 서버의 interrupted 응답을 기다리는 동안은 멈춘 대답의 늦게 온 조각을 버린다
let ackTimer = null;
const DROP_TYPES = new Set(['stream_start', 'stream_search', 'stream_meta', 'stream_text', 'status']);
const DROP_OPS = new Set(['stream_audio', 'chat_result']);
const isReplying = () => awaitingReply || playing || !!activeSource || stream.active;
function interruptReply() {
  if (!isReplying()) return false;
  if (socket && socket.interrupt) socket.interrupt();
  awaitInterruptAck = true;
  clearTimeout(ackTimer); ackTimer = setTimeout(() => { awaitInterruptAck = false; }, 3000);   // 오래된 서버라 응답이 없어도 영원히 버리지 않는다
  stopAudio(); stream.end(); pendingEmotion = '';
  setAwaiting(false);
  say('⏹ 멈췄어요.');
  return true;
}
let lastSttErrorAt = 0;
let vadWarned = false;
let aecWarned = false;

// PC 에서 나는 모든 소리를 받아 오는 장치 (Electron 의 loopback 오디오). 화면 영상은 쓰지 않는다.
const systemAudioLoopback = (() => {
  let stream = null;
  const stop = () => { if (stream) { try { stream.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ } stream = null; } };
  // 기준 소리는 있는 그대로여야 한다 (자동 이득/잡음 억제/에코 제거가 걸리면 AEC3 가 마이크에 섞인 소리와 맞추지 못한다). 제약을 못 받는 환경이면 기본값으로 다시 요청.
  const RAW = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };
  const request = async () => {
    try { return await navigator.mediaDevices.getDisplayMedia({ video: true, audio: RAW }); }
    catch { return navigator.mediaDevices.getDisplayMedia({ video: true, audio: true }); }
  };
  return {
    async open() {
      stop();
      try {
        let s = await request();
        if (!s.getAudioTracks().length) { s.getTracks().forEach((t) => t.stop()); return { ok: false, reason: 'PC 소리를 받지 못했어요 (지원하지 않는 환경일 수 있어요)' }; }
        // 영상은 필요 없으니 끈다. 영상을 끄면 소리까지 끊기는 환경이면, 영상을 켠 채로(화면은 쓰지 않음) 다시 받는다.
        s.getVideoTracks().forEach((t) => t.stop());
        await new Promise((r) => setTimeout(r, 250));
        if (!s.getAudioTracks().some((t) => t.readyState === 'live')) {
          s.getTracks().forEach((t) => t.stop());
          s = await request();
          s.getVideoTracks().forEach((t) => { t.enabled = false; });
        }
        stream = s;
        return { ok: true, stream: new MediaStream(s.getAudioTracks()) };
      } catch (e) {
        stop();
        return { ok: false, reason: (e && (e.message || e.name)) || String(e) };
      }
    },
    close: stop,
  };
})();

// 화자 인증(선택): config.speaker.enabled 가 true 이고 목소리를 등록해 뒀을 때만 동작한다.
// 모델이 없거나 등록 전이거나 확인에 실패하면 막지 않고 그냥 통과시킨다.
let enrolling = null;   // { need, got: [pcm...] } 등록 중이면 다음 말들은 인식 대신 등록에 쓴다
// 등록용 문장: 길이/말투/억양이 서로 다르게 섞어 둔다 (한 가지 말투만 등록하면 평소 말투가 기준에서 벗어나 막힌다)
const ENROLL_SENTENCES = [
  '오늘 날씨가 어떤지 알려줄래?',
  '내일 아침 일곱 시에 알람 좀 맞춰 줘.',
  '방금 들은 노래 제목이 뭐였더라, 한번 찾아봐 줄 수 있어?',
  '저녁에 뭐 먹을지 아직 못 정했는데 추천해 줄래?',
  '아 진짜? 그건 몰랐네, 좀 더 자세히 얘기해 줘.',
  '이번 주말에는 친구들이랑 영화를 보러 갈 생각이야.',
  '잠깐만, 지금 하던 일 끝내고 바로 이야기할게.',
  '컴퓨터가 갑자기 느려졌는데 왜 그런지 같이 봐 줄래?',
  '오늘 하루도 정말 길었다, 이제 좀 쉬고 싶어.',
  '음악 소리 좀 줄여 주고, 다음 곡으로 넘겨 줘.',
  '어제 본 영상이 너무 웃겨서 아직도 생각나.',
  '자기 전에 내일 일정만 한 번 더 확인해 줘.',
];
const ENROLL_COUNT = ENROLL_SENTENCES.length;
const ENROLL_MIN_SECONDS = 2;
const ENROLL_MATCH_MIN = 0.6;   // 인식된 글자가 안내 문장과 이만큼(0~1) 비슷해야 저장한다 (음악/다른 소리가 섞여 들어가는 걸 막는다)
const ENROLL_MAX_TRIES = 3;     // 한 문장에서 이만큼 연속으로 안 맞으면 그 문장은 건너뛴다
const ENROLL_MIN_SAMPLES = 6;   // 맞게 읽은 문장이 이보다 적으면 등록하지 않는다

async function passesSpeaker(info) {
  const sc = config.speaker || {};
  if (sc.enabled !== true || !info || !info.pcm || !window.api.speakerVerify) return true;
  try {
    const r = await window.api.speakerVerify(info.pcm);
    const score = typeof r.score === 'number' ? ` score=${r.score.toFixed(2)}` : '';
    if (r.ok) { if (score) console.info(`[speaker] 통과${score}`); return true; }
    console.info(r.skipped === 'too-short' ? `[speaker] 너무 짧아서 무시 (${(r.seconds || 0).toFixed(1)}초)` : `[speaker] 등록한 목소리가 아니라서 무시${score}`);
    return false;
  } catch (e) {
    console.warn('[speaker] 확인 실패 -> 그냥 통과', e);
    return true;
  }
}

async function collectEnrollment(blob, info) {
  const e = enrolling;
  if (!e || e.busy) return;   // 확인(STT)하는 동안 들어온 소리는 버린다
  e.busy = true;
  try {
    const total = ENROLL_COUNT;
    const prompt = () => `${e.idx + 1}/${total} \"${ENROLL_SENTENCES[e.idx]}\"`;
    if (!info || !info.pcm || info.seconds < ENROLL_MIN_SECONDS) { say(`🎙 조금만 더 길게 말해 주세요. (${ENROLL_MIN_SECONDS}초 이상)\n${prompt()}`); return; }

    // 안내 문장대로 읽었는지 STT 로 확인한다. STT 를 못 쓰면(주소 없음/서버 꺼짐) 확인 없이 저장한다.
    let accepted = true;
    const stt = effectiveStt(config);
    if (stt.url && !e.sttOff) {
      let heard = null;
      try { heard = await transcribe(blob, stt); } catch (err) { e.sttOff = true; console.warn('[enroll] 인식 확인 실패 -> 이후 문장은 확인 없이 저장', err); }
      if (!enrolling) return;   // 확인하는 사이 마이크가 꺼져서 등록이 취소됨
      if (heard !== null) {
        const sim = textSimilarity(heard, ENROLL_SENTENCES[e.idx]);
        console.info(`[enroll] ${e.idx + 1}/${total} 인식="${heard}" 유사도=${sim.toFixed(2)}`);
        if (sim < ENROLL_MATCH_MIN) {
          e.tries++;
          if (e.tries < ENROLL_MAX_TRIES) {
            say(`🎙 안내 문장과 다르게 들렸어요${heard ? ` (\"${trunc(heard, 30)}\")` : ''}. 문장을 그대로 다시 읽어 주세요.\n${prompt()}`);
            return;
          }
          accepted = false;   // 계속 안 맞으면 이 문장은 건너뛴다
        }
      }
    }
    if (accepted) e.got.push(info.pcm);
    e.idx++; e.tries = 0;
    if (e.idx < total) {
      say(accepted ? `🎙 ${e.idx}/${total} 좋아요. 다음 문장을 읽어 주세요.\n${prompt()}` : `🎙 이 문장은 건너뛸게요. 다음 문장을 읽어 주세요.\n${prompt()}`);
      return;
    }

    enrolling = null;
    if (e.got.length < ENROLL_MIN_SAMPLES) {
      say(`🎙 안내 문장대로 읽은 게 ${e.got.length}문장뿐이라 등록하지 않았어요. (최소 ${ENROLL_MIN_SAMPLES}문장)\n조용한 곳에서, 음악을 끄고 다시 해 주세요.`);
      return;
    }
    const r = await window.api.speakerEnroll(e.got).catch((err) => ({ ok: false, reason: (err && err.message) || String(err) }));
    if (!r.ok) { say(`🎙 목소리 등록에 실패했어요.\n${r.reason || ''}`); return; }
    const sc = config.speaker || {};
    let msg = sc.enabled === true
      ? `🎙 목소리를 ${r.samples}문장 등록했어요. 이제 등록한 목소리만 인식해요.`
      : `🎙 목소리를 ${r.samples}문장 등록했어요. config.json 의 speaker.enabled 를 true 로 바꾸면 등록한 목소리만 인식해요.`;
    if (typeof r.selfMin === 'number') {
      const th = Number.isFinite(Number(sc.threshold)) ? Number(sc.threshold) : 0.5;
      msg += `\n내 목소리끼리의 점수: 평균 ${r.selfMean.toFixed(2)}, 최저 ${r.selfMin.toFixed(2)} (현재 임계값 ${th})`;
      if (th > r.selfMin) msg += `\n임계값이 최저 점수보다 높아서 내 말도 막힐 수 있어요. config.json 의 speaker.threshold 를 ${Math.max(0.2, Math.floor(r.selfMin * 100 - 5) / 100)} 정도로 낮춰 보세요.`;
    }
    say(msg);
  } finally {
    e.busy = false;
  }
}

async function startEnroll() {
  const st = window.api.speakerStatus ? await window.api.speakerStatus().catch(() => null) : null;
  if (!st || !st.available) {
    say(`🎙 화자 모델을 쓸 수 없어요.\n${(st && st.reason) || 'voice-models/speaker.onnx 를 확인하세요. (npm run get-models)'}`);
    return;
  }
  enrolling = { idx: 0, tries: 0, got: [], sttOff: false, busy: false };
  say(`🎙 목소리 등록: 아래 문장을 평소 말투로 하나씩 읽어 주세요. (${ENROLL_COUNT}문장, 조용한 곳에서, 음악은 꺼 주세요. 인식된 글자가 안내 문장과 많이 다르면 다시 읽게 해요)\n1/${ENROLL_COUNT} "${ENROLL_SENTENCES[0]}"`);
  const m = ensureMic();
  if (!m.active) await startMic(m);
}

function refreshBadge(state) {
  if (!mic || !mic.active) { micBadge.style.display = 'none'; return; }
  if (state === 'listening') state = micBusy ? 'busy' : awaitingReply ? 'thinking' : state;   // 듣지 않는 단계는 배지에 그대로 보여 준다
  micBadge.style.display = 'block';
  micBadge.classList.toggle('speaking', state === 'speaking');
  micBadge.textContent = state === 'speaking' ? '🎤 듣는 중…' : state === 'busy' ? '📝 인식 중…' : state === 'thinking' ? '💭 생각 중…' : '🎤 마이크 켜짐';
}

function ensureMic() {
  if (mic) { mic.setOptions(config.mic || {}); return mic; }
  mic = new MicListener({
    // 캐릭터가 말하는 동안에는 자기 목소리를 듣지 않는다
    shouldPause: () => awaitingReply || micBusy || playing || !!activeSource || performance.now() - lastAudioEnd < 600,
    // 말 구간 감지: 메인 프로세스의 Silero VAD (못 쓰면 mic.js 가 음량 방식으로 자동 전환)
    vad: window.api.vadInit ? { init: () => window.api.vadInit(), reset: () => window.api.vadReset(), frame: (f) => window.api.vadFrame(f) } : null,
    // PC 에서 나는 소리(유튜브/음악 등)를 에코 제거의 기준 신호로 받는다 (Windows). 안 되면 mic.js 가 브라우저 내장 방식으로 자동 전환
    loopback: window.api.vadInit && navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia ? systemAudioLoopback : null,
    onAecFallback: (reason) => {
      if (aecWarned) return;
      aecWarned = true;
      say(`🎤 PC 소리 기준 에코 제거를 켜지 못해서 앱 안의 소리만 지우는 방식으로 듣고 있어요.\n(${reason})\n유튜브/음악 소리가 마이크에 섞이면 헤드셋을 쓰거나 config.json 의 mic.systemAec 를 false 로 바꿔 보세요.`);
    },
    onFallback: (reason) => {
      if (vadWarned) return;
      vadWarned = true;
      say(`🎤 Silero VAD 를 쓰지 못해서 기본(음량) 방식으로 듣고 있어요.\n${reason}`);
    },
    onState: (state) => {
      refreshBadge(state);
      if (state === 'speaking') proactive.noteUserActivity();
      if (state === 'idle') { enrolling = null; window.api.micState(false); } // 트레이 메뉴의 체크 표시를 맞춘다
    },
    onUtterance: async (blob, info = {}) => {
      if (enrolling) { await collectEnrollment(blob, info); return; }
      micBusy = true;   // 인식 -> 전송이 끝날 때까지 새 입력을 받지 않는다 (shouldPause). 전송 뒤에는 awaitingReply 가 이어서 막는다
      refreshBadge('busy');
      try {
        let text = '';
        try {
          if (!(await passesSpeaker(info))) return;   // 등록한 목소리가 아니면 조용히 버린다 (finally 가 busy 를 풀어 준다)
          text = await transcribe(blob, effectiveStt(config));
          console.info('[mic] 인식:', text);
        } catch (e) {
          console.warn('[mic] 음성 인식 실패', e);
          // 켜 둔 채로 계속 듣는 모드라서, 서버가 꺼져 있으면 말할 때마다 오류가 반복된다 -> 같은 오류는 30초에 한 번만 보여 준다
          if (performance.now() - lastSttErrorAt > 30000) {
            lastSttErrorAt = performance.now();
            say(`음성 인식에 실패했어요. (${e.message || e})\n서버(Project Miku server)가 켜져 있는지, 서버에 faster-whisper 가 설치돼 있는지(pip install faster-whisper) 확인하세요.\n다른 STT 서버를 쓴다면 config.json 의 stt.url 을 확인하세요.`);
          }
          return;
        }
        if (!text || isGhostText(text)) return;   // 조용한 소리를 엉뚱한 문장으로 인식한 경우는 조용히 버린다
        bubble.show('…', `🎤 ${text}`);
        await submitText(text);
      } finally {
        micBusy = false;
        refreshBadge('listening');
      }
    },
  }, config.mic || {});
  return mic;
}

async function toggleMic() {
  const m = ensureMic();
  if (m.active) { m.stop('manual'); return; }
  if (!effectiveStt(config).url) {
    say('음성 인식 서버 주소를 알 수 없어요.\nconfig.json 의 serverUrl(또는 stt.url)을 확인해 주세요. (README 참고)');
    window.api.micState(false);
    return;
  }
  await startMic(m);
}

async function startMic(m) {
  try {
    stopAudio(); // 마이크를 켜면 말하던 음성은 끊는다
    await m.start();
    window.api.micState(true);
  } catch (e) {
    console.warn('[mic] 시작 실패', e);
    window.api.micState(false);
    say(e && e.name === 'NotAllowedError'
      ? '마이크 권한이 막혀 있어요.\nWindows 설정 > 개인 정보 및 보안 > 마이크에서 데스크톱 앱의 접근을 허용해 주세요.'
      : `마이크를 열지 못했어요. (${(e && e.message) || e})`);
  }
}

// ---------- 클릭 통과 ----------
// 창이 화면 전체를 덮고 있으므로, 모델/입력창/패널 위가 아니면 항상 클릭이 뒤로 통과해야 한다.
//  1) passthrough (기본 켜짐): 모델 몸통·입력창 위에서만 마우스를 받는다.
//  2) clickThroughModel (기본 꺼짐): 모델 몸통까지 전부 통과. 모델 뒤에 가려진 창도 클릭할 수 있다.
let ignoring = false;
function setIgnore(v) {
  if (v === ignoring) return;
  ignoring = v;
  window.api.setIgnoreMouse(v);
}

// 모델이 실제로 그려진 픽셀(알파 > 0)인지 검사한다. 네모난 영역이 아니라 모델 모양 기준.
const px = new Uint8Array(4);
// 마우스 판정(모델 위인가?)이 GPU readPixels 를 직접 부르면 GPU 가 그리던 걸 멈추고 기다려서 모델이 끊긴다.
// 그래서 WebGL2 의 비동기 읽기(PBO + fenceSync)로 모델 영역의 알파 마스크를 가끔 CPU 로 복사해 두고,
// 마우스 판정은 그 마스크만 조회한다 (GPU 를 건드리지 않음). WebGL2 가 아니면 예전 방식(동기 읽기)으로 동작.
let hitBounds = null, hitBoundsAt = 0, lastHit = { x: -999, y: -999, t: 0, v: false };
let mask = null;            // { x0, yTop, w, h, data(Uint8Array RGBA), t }
let maskPending = false;
let maskPbo = null;
const MASK_INTERVAL = 100;  // ms

function invalidateHitCache() { hitBounds = null; lastHit.t = 0; mask = null; }

// 마스크에서 머리 끝(가장 위 그려진 줄)을 다시 구해 말풍선/입력창이 머리를 따라가게 한다.
// 모션이 몸을 위아래로 흔드는 모델(예: ellen)에서 상자와 머리 사이 간격이 들쭉날쭉해지는 걸 막는다.
function followHead() {
  if (config.uiFollowHead === false || editMode || !mask || !mask.data) return;
  const { w, h, data, yTop, res } = mask;
  for (let dy = 0; dy < h; dy++) {
    const row = (h - 1 - dy) * w * 4; // 버퍼는 GL 좌표(아래가 0)
    for (let x = 0; x < w; x += 2) {
      if (data[row + x * 4 + 3] > 16) {
        const ht = (yTop + dy) / res;
        const prev = measuredHeadTop;
        measuredHeadTop = prev == null ? ht : prev + (ht - prev) * 0.5; // 부드럽게
        if (prev == null || Math.abs(measuredHeadTop - prev) > 0.5) positionUI();
        return;
      }
    }
  }
}
setInterval(() => {
  if (model && app && !document.hidden && config.uiFollowHead !== false) requestMask();
}, 250);

function requestMask() {
  if (maskPending || !model || !app) return;
  const gl = app.renderer.gl;
  if (!gl.fenceSync || !gl.getBufferSubData) return; // WebGL1: 동기 방식 사용
  try {
    const W = gl.drawingBufferWidth, H = gl.drawingBufferHeight, res = app.renderer.resolution;
    const b = model.getBounds();
    const x0 = clamp(Math.floor(b.x * res) - 8, 0, W);
    const x1 = clamp(Math.ceil((b.x + b.width) * res) + 8, 0, W);
    const yTop = clamp(Math.floor(b.y * res) - 8, 0, H);
    const yBot = clamp(Math.ceil((b.y + b.height) * res) + 8, 0, H);
    const w = x1 - x0, h = yBot - yTop;
    if (w <= 0 || h <= 0) { mask = { x0, yTop, w: 0, h: 0, data: null, t: performance.now(), res }; return; }
    maskPending = true;
    if (!maskPbo) maskPbo = gl.createBuffer();
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, maskPbo);
    gl.bufferData(gl.PIXEL_PACK_BUFFER, w * h * 4, gl.STREAM_READ);
    gl.readPixels(x0, H - yBot, w, h, gl.RGBA, gl.UNSIGNED_BYTE, 0);
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
    const sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
    gl.flush();
    const poll = () => {
      try {
        const st = gl.clientWaitSync(sync, 0, 0);
        if (st === gl.TIMEOUT_EXPIRED) { setTimeout(poll, 8); return; }
        gl.deleteSync(sync);
        const data = new Uint8Array(w * h * 4);
        gl.bindBuffer(gl.PIXEL_PACK_BUFFER, maskPbo);
        gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, data);
        gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
        mask = { x0, yTop, w, h, data, t: performance.now(), res };
        followHead();
      } catch (e) {
        console.warn('[hit] 비동기 마스크 실패, 동기 방식으로 전환', e);
        gl.fenceSync = null;
      }
      maskPending = false;
    };
    setTimeout(poll, 4);
  } catch (e) {
    maskPending = false;
    console.warn('[hit] 마스크 요청 실패, 동기 방식으로 전환', e);
    app.renderer.gl.fenceSync = null;
  }
}

function overModelPixel(x, y) {
  if (!model || !app) return false;
  const now = performance.now();
  if (!hitBounds || now - hitBoundsAt > 150) { hitBounds = model.getBounds(); hitBoundsAt = now; }
  const pad = 24;
  if (x < hitBounds.x - pad || x > hitBounds.x + hitBounds.width + pad || y < hitBounds.y - pad || y > hitBounds.y + hitBounds.height + pad) {
    return false;
  }
  const gl = app.renderer.gl;
  if (gl.fenceSync && gl.getBufferSubData) {
    if (!mask || now - mask.t > MASK_INTERVAL) requestMask();
    if (mask) {
      if (!mask.data) return false;
      const dx = Math.floor(x * mask.res) - mask.x0;
      const dy = Math.floor(y * mask.res) - mask.yTop;
      if (dx < 0 || dy < 0 || dx >= mask.w || dy >= mask.h) return false;
      // 버퍼는 GL 좌표(아래가 0)라서 행을 뒤집어 읽는다
      return mask.data[((mask.h - 1 - dy) * mask.w + dx) * 4 + 3] > 16;
    }
    // 마스크가 아직 없으면(처음/배치 직후) 이번 한 번만 동기 방식으로 판정
  }
  if (now - lastHit.t < 60 && Math.abs(x - lastHit.x) < 4 && Math.abs(y - lastHit.y) < 4) return lastHit.v;
  const v = readModelPixel(x, y);
  lastHit = { x, y, t: now, v };
  return v;
}
function readModelPixel(x, y) {
  try {
    const gl = app.renderer.gl;
    const r = app.renderer.resolution;
    const gx = Math.floor(x * r);
    const gy = Math.floor(gl.drawingBufferHeight - y * r - 1);
    if (gx < 0 || gy < 0 || gx >= gl.drawingBufferWidth || gy >= gl.drawingBufferHeight) return false;
    gl.readPixels(gx, gy, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    return px[3] > 16;
  } catch {
    // 픽셀을 못 읽으면 바운딩 박스로 대체
    const b = model.getBounds();
    return x >= b.x && x <= b.x + b.width && y >= b.y && y <= b.y + b.height;
  }
}

const inRect = (el, x, y) => {
  const r = el.getBoundingClientRect();
  return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
};
function overVisibleUI(x, y) {
  for (const el of [bubbleEl, inputBar]) {
    if (el.style.display === 'block' && inRect(el, x, y)) return true;
  }
  return sessionPanel.contains(x, y);
}

let drag = null; // { kind: 'model' | 'ui', x, y }
let lastPointer = { x: -1, y: -1 };

// 메인 프로세스가 커서 위치를 알려준다 (클릭 통과 중에도 동작)
function handlePointer(x, y) {
  lastPointer = { x, y };
  if (drag || selecting) return;

  // 대화 목록 패널 위에서는 항상 마우스를 받는다 (클릭 통과/입력창 표시 로직은 건너뜀)
  if (sessionPanel.contains(x, y) || intervalDlg.contains(x, y) || settingsDlg.contains(x, y) || lyricsPicker.contains(x, y)) { setIgnore(false); return; }

  if (editMode) { // 편집 중에도 화면 전체를 막으면 안 된다: 모델/상자/패널 위에서만 마우스를 받는다
    setIgnore(!(overModelPixel(x, y) || overVisibleUI(x, y) || inRect(panel, x, y) || overItem(x, y)));
    return;
  }

  if (config.clickThroughModel) { // 모델도 통과: 입력창은 쓸 수 없으니 숨긴다
    hideInput();
    setIgnore(true);
    return;
  }

  const overUI = overVisibleUI(x, y);
  const overModel = overUI ? false : overModelPixel(x, y);

  if (overModel || overUI) showInput();
  else scheduleHideInput();

  if (config.passthrough !== false) setIgnore(!(overModel || overUI));
  else setIgnore(false);
}

// 모드가 바뀐 직후 현재 커서 위치로 즉시 다시 판정한다
function applyIgnoreMode() {
  handlePointer(lastPointer.x, lastPointer.y);
}

// ---------- 드래그: 모델을 화면 어디로든 (편집 모드에선 말풍선 상자도) ----------
window.addEventListener('mousedown', (e) => {
  if (selecting || e.button !== 0 || e.target === input || e.target.closest('#editPanel, #sessPanel, #intervalDlg, #settingsDlg, #lyricsPicker, #bubble, #btnSessions, #btnStop')) return;
  const base = { x: e.clientX, y: e.clientY };
  if (editMode && e.target.closest('#ui')) drag = { kind: 'ui', ...base };
  else if (overItem(e.clientX, e.clientY)) drag = { kind: 'item', ...base };   // 아이템은 모델 앞에 그려지니 먼저 잡는다 (편집 모드에서만)
  else if (overModelPixel(e.clientX, e.clientY)) drag = { kind: 'model', ...base };
});

window.addEventListener('mousemove', (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.x;
  const dy = e.clientY - drag.y;
  drag.moved = (drag.moved || 0) + Math.abs(dx) + Math.abs(dy);
  drag.x = e.clientX; drag.y = e.clientY;
  if (drag.kind === 'item') {
    setItem(moveItem(lyricsItem, modelHeightPx(), dx, dy));
  } else if (drag.kind === 'model') {
    const o = clampOffset(cfgOffset());
    persist({ modelOffset: clampOffset({ x: o.x + dx, y: o.y + dy }) });
    layoutModel();
  } else {
    const u = cfgUI();
    persist({ ui: { ...u, offsetX: u.offsetX + dx, offsetY: u.offsetY + dy } });
    positionUI();
  }
});

window.addEventListener('mouseup', (e) => {
  const d = drag;
  drag = null;
  if (d && d.kind === 'item') flushItemSave();
  // 거의 안 움직이고 뗐으면 드래그가 아니라 "톡" 건드린 것
  if (d && d.kind === 'model' && !editMode && (d.moved || 0) < 6 && e && typeof e.clientX === 'number') tapModel(e.clientX, e.clientY);
});

// ---------- 터치 반응: model3.json 의 HitAreas -> Tap<부위> 모션 ----------
// 부위 판정은 라이브러리(model.hitTest)가 model3.json 의 HitAreas 로 한다. 부위 이름이 Body 면 TapBody 그룹을 재생.
// 그룹 이름은 대소문자/구분자를 무시하고 찾는다: TapBody, tap_body, tap-body ...
const normKey = (s) => String(s).toLowerCase().replace(/[^a-z0-9가-힣]/g, '');
function tapModel(x, y) {
  if (!model || config.tapReaction === false) return;
  let areas = [];
  try { areas = model.hitTest(x, y) || []; } catch (e) { console.warn('[tap] 판정 실패', e); return; }
  if (!areas.length) { console.info('[tap] 부위 없음 (model3 HitAreas 밖)'); return; }
  const defs = (model.internalModel.motionManager && model.internalModel.motionManager.definitions) || {};
  const groups = Object.keys(defs);
  for (const area of areas) {
    const want = normKey('tap' + area);
    const group = groups.find((g) => normKey(g) === want);
    if (!group) { console.info(`[tap] ${area}: 맞는 모션 그룹(Tap${area}) 없음. 그룹: ${groups.join(', ')}`); continue; }
    // 번호를 안 주면 그룹에서 무작위. NORMAL(2) 우선순위라 대기 모션은 끊고 들어가고, 재생 중인 터치 모션 위에는 겹치지 않는다
    model.motion(group, undefined, 2).then((ok) => console.info(`[tap] ${area} -> ${group} ${ok ? '재생' : '(이미 재생 중이라 무시)'}`));
    return;
  }
}

// 우클릭: 트레이와 같은 메뉴 (이 이벤트는 창이 마우스를 받는 곳 = 모델/상자 위에서만 온다)
window.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  if (selecting || e.target.closest('#sessPanel, #intervalDlg, #settingsDlg, #lyricsPicker')) return;
  window.api.showMenu();
});

// 편집 모드에서 모델 위 휠: 크기 조절
window.addEventListener('wheel', (e) => {
  if (selecting || !editMode || e.target.closest('#editPanel, #sessPanel, #intervalDlg, #settingsDlg, #lyricsPicker')) return;
  if (overItem(e.clientX, e.clientY)) { setItem(scaleItem(lyricsItem, e.deltaY < 0 ? 1.05 : 1 / 1.05)); return; }   // 스탠드 위: 스탠드 크기
  if (!overModelPixel(e.clientX, e.clientY)) return;
  const next = clamp(cfgScale() * (e.deltaY < 0 ? 1.05 : 1 / 1.05), 0.3, 2.5);
  persist({ scale: Math.round(next * 100) / 100 });
  layoutModel();
  syncPanel();
}, { passive: true });

window.addEventListener('resize', () => {
  app && app.renderer.resize(window.innerWidth, window.innerHeight);
  layoutModel();
});

// ---------- 편집 모드 ----------
const sliders = {
  scale: { r: $('rScale'), v: $('vScale'), fmt: (n) => `${Math.round(n * 100)}%` },
  width: { r: $('rWidth'), v: $('vWidth'), fmt: (n) => `${n}px` },
  font: { r: $('rFont'), v: $('vFont'), fmt: (n) => `${n}px` },
  ly1: { r: $('rLy1'), v: $('vLy1'), fmt: (n) => `${n}px` },
  ly2: { r: $('rLy2'), v: $('vLy2'), fmt: (n) => `${n}px` },
  ly3: { r: $('rLy3'), v: $('vLy3'), fmt: (n) => `${n}px` },
  item: { r: $('rItem'), v: $('vItem'), fmt: (n) => `${n}%` },
};

function syncPanel() {
  const vals = { scale: cfgScale(), width: cfgUI().width, font: cfgUI().fontSize, ly1: lyricsSizes()[0], ly2: lyricsSizes()[1], ly3: lyricsSizes()[2], item: Math.round(lyricsItem.scale * 100) };
  for (const [k, s] of Object.entries(sliders)) {
    if (document.activeElement !== s.r) s.r.value = vals[k];
    s.v.textContent = s.fmt(Number(vals[k]));
  }
}

// 패널은 모델 옆에 붙인다 (화면 전체 창에서 구석에 있으면 모델과 너무 멀다)
function positionPanel() {
  const pw = 232;
  const ph = panel.offsetHeight || 260;
  const b = model ? model.getBounds() : { x: baseX() - 100, y: window.innerHeight - 400, width: 200, height: 400 };
  let left = b.x + b.width + 12;
  if (left + pw > window.innerWidth - 8) left = b.x - pw - 12;
  panel.style.left = `${clamp(left, 8, window.innerWidth - pw - 8)}px`;
  panel.style.top = `${clamp(b.y, 8, Math.max(window.innerHeight - ph - 8, 8))}px`;
}

function setEdit(on) {
  editMode = !!on;
  document.body.classList.toggle('editing', editMode);
  ui.classList.toggle('editing', editMode);
  if (editMode) {
    drag = null;
    clearTimeout(hideTimer); hideTimer = null; // 모델에서 마우스가 벗어나며 걸린 "입력창 숨김" 예약을 취소
    inputBar.style.display = 'block';
    bubble.hold('edit');
    lyrics && lyrics.setPreview(true);
    bubble.show('말풍선 미리보기\n이 상자를 끌어서 옮기세요', '💭 *smiles softly*\n감정: happy → 표정:예시 (think)', true);
    positionPanel();
    syncPanel();
  } else {
    flushSave();
    flushItemSave();
    bubble.release('edit');
    bubble.hide();
    lyrics && lyrics.setPreview(false);
    hideInput();
  }
  applyIgnoreMode();
}

sliders.scale.r.addEventListener('input', () => {
  persist({ scale: Number(sliders.scale.r.value) });
  layoutModel(); syncPanel();
});
sliders.scale.r.addEventListener('pointerdown', () => { sliderBusy = true; });
window.addEventListener('pointerup', () => {
  if (!sliderBusy) return;
  sliderBusy = false;
  if (editMode) positionPanel(); // 손을 뗀 뒤에 한 번만 모델 옆으로 재배치
});
// 미세 조절: 버튼은 1%씩 (슬라이더 위에서 ←/→ 키도 1%씩)
const nudgeScale = (d) => {
  const next = clamp(Math.round((cfgScale() + d) * 100) / 100, 0.3, 2.5);
  persist({ scale: next });
  layoutModel(); syncPanel();
};
$('btnScaleDown').addEventListener('click', () => nudgeScale(-0.01));
$('btnScaleUp').addEventListener('click', () => nudgeScale(0.01));
sliders.width.r.addEventListener('input', () => {
  persist({ ui: { ...cfgUI(), width: Number(sliders.width.r.value) } });
  positionUI(); syncPanel();
});
sliders.font.r.addEventListener('input', () => {
  persist({ ui: { ...cfgUI(), fontSize: Number(sliders.font.r.value) } });
  positionUI(); syncPanel();
});
for (const n of [1, 2, 3]) {
  sliders[`ly${n}`].r.addEventListener('input', () => {
    persist({ lyrics: { ...(config.lyrics || {}), [`size${n}`]: Number(sliders[`ly${n}`].r.value) } });
    positionUI(); syncPanel();
  });
}
sliders.item.r.addEventListener('input', () => setItem({ ...lyricsItem, scale: Number(sliders.item.r.value) / 100 }));
$('btnItemReset').addEventListener('click', () => setItem({ ...ITEM_DEFAULT }));
$('btnReset').addEventListener('click', () => {
  setItem({ ...ITEM_DEFAULT });
  persist({
    scale: DEFAULTS.scale,
    modelOffset: { ...DEFAULTS.modelOffset },
    ui: { ...DEFAULTS.ui },
    lyrics: { ...(config.lyrics || {}), fontSize: 0, size1: 0, size2: 0, size3: 0 },
  });
  layoutModel(); syncPanel();
});
$('btnDone').addEventListener('click', () => window.api.setEditMode(false));

// ---------- 시작 ----------
async function main() {
  config = await window.api.getConfig();
  gaze.setOptions(config.gaze);
  bubble = new Bubble(bubbleEl, config.bubbleSeconds || 6);
  bubble.setEnabled(config.showBubble !== false);
  bubble.canRecall = () => !(lyrics && lyrics.isActive());   // 가사가 떠 있으면 마우스를 올려도 지난 대사로 가사를 가리지 않는다
  setupLyrics();

  Live2DModel.registerTicker(PIXI.Ticker);
  app = new Application({
    view: canvas,
    width: window.innerWidth,
    height: window.innerHeight,
    backgroundAlpha: 0,
    antialias: true,
    autoDensity: true,
    resolution: window.devicePixelRatio || 1,
    preserveDrawingBuffer: true, // 픽셀 단위 판정/측정(readPixels)에 필요
  });

  window.api.onModelSelect((name) => loadModel(name));
  window.api.onConfigChanged((cfg) => {
    const prev = config || {};
    config = { ...cfg, ...pending }; // 아직 저장 안 된 편집값은 유지
    if (prev.showBubble !== config.showBubble) {
      bubble.setEnabled(config.showBubble !== false);
      if (config.showBubble !== false) bubble.show('대사창을 켰어요.', '', true);
    }
    if ((prev.screenShare === true) !== (config.screenShare === true)) {
      bubble.show(config.screenShare === true ? '🖥 화면 공유 모드 켬: 메시지를 보낼 때마다 화면을 같이 봐요.' : '🖥 화면 공유 모드 껐어요.', '', true);
    }
    emotion && emotion.setAliases(currentAliases());
    bubble.seconds = config.bubbleSeconds || 6;
    applyLyricsOptions();
    if (socket) Object.assign(socket.opts, { streaming: config.streaming !== false, audioBaseUrl: config.audioBaseUrl });
    if (prev.scale !== config.scale && model) layoutModel();
    gaze.setOptions(config.gaze);
    if (normalizeGaze(prev.gaze).enabled !== normalizeGaze(config.gaze).enabled) {
      bubble.show(normalizeGaze(config.gaze).enabled ? '👀 시선 추적을 켰어요. 마우스를 따라 봐요.' : '👀 시선 추적을 껐어요.', '', true);
    }
    mic && mic.setOptions(config.mic || {});
    {
      const before = normalizeProactive(prev.proactive);
      const after = normalizeProactive(config.proactive);
      if (JSON.stringify(before) !== JSON.stringify(after)) {
        proactive.reschedule(); // 설정이 바뀌면 시간을 처음부터 다시 센다
        if (before.enabled !== after.enabled) {
          say(after.enabled ? `💬 먼저 말 걸기를 켰어요. (${describeProactive(after)}, 말이 없을 때만 걸어요)` : '💬 먼저 말 걸기를 껐어요.');
        } else if (after.enabled && (before.minMinutes !== after.minMinutes || before.maxMinutes !== after.maxMinutes)) {
          say(`💬 먼저 말 거는 간격을 바꿨어요. (${describeProactive(after)})`);
        }
      }
    }
    positionUI();
    applyIgnoreMode();
  });
  window.api.onEditMode((on) => setEdit(on));
  window.api.onSessionsToggle(() => sessionPanel.toggle());
  window.api.onSessionsNew(() => { proactive.noteUserActivity(); stopAudio(); socket && socket.newSession(); });
  window.api.onMicToggle(() => toggleMic());
  window.api.onSpeakerEnroll && window.api.onSpeakerEnroll(() => startEnroll());
  window.api.onSpeakerCleared && window.api.onSpeakerCleared(() => say('🎙 등록한 목소리를 지웠어요.'));
  window.api.onSettingsOpen(() => settingsDlg.open());
  window.api.onLyricsPick(() => lyricsPicker.open());
  window.api.onProactivePrompt(() => intervalDlg.open(normalizeProactive(config.proactive)));
  window.api.onProactiveNow(() => { if (!socket.sendProactive()) say('서버에 연결되어 있지 않아요.'); });
  window.api.onScheduleFire(handleScheduleFire);
  window.api.onCapture((mode) => captureAndSend(mode === 'partial' ? 'partial' : 'full'));
  window.api.onCursor(({ x, y }) => { gaze.noteCursor(x, y, performance.now()); handlePointer(x, y); });

  // 상태 점 대신: 연결 실패/복구를 말풍선으로 한 번씩만 알려준다
  let warned = false;
  socket = new MikuSocket(config.serverUrl, {
    streaming: config.streaming !== false,
    audioBaseUrl: config.audioBaseUrl,
    uploadUrl: config.uploadUrl,
    onEvent: handleEvent,
    onStatus: (ok) => {
      if (ok) {
        if (warned) bubble.show('서버에 연결됐어요.');
        warned = false;
        pushProfile();   // 서버를 다시 켰을 수도 있으니 연결될 때마다 알린다 (모델이 아직 없으면 로드가 끝날 때 보낸다)
      } else if (!warned) {
        setAwaiting(false);   // 연결이 끊기면 답이 오지 않으니 마이크를 풀어 준다
        warned = true;
        console.warn('[server] 연결 실패:', config.serverUrl);
        bubble.show(`서버에 연결할 수 없어요.\nconfig.json 의 serverUrl(포트)을 확인하세요.\n(${config.serverUrl})`);
      }
    },
  });
  socket.connect();
  proactive.start(); // 켜져 있을 때만 동작한다 (config.proactive.enabled)

  positionUI();
  await loadModel(config.model);
  if (await window.api.getEditMode()) setEdit(true);
  else applyIgnoreMode();
}

main().catch((e) => console.error(e));
