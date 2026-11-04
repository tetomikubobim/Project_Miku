const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  getConfig: () => ipcRenderer.invoke('config:get'),
  setConfig: (patch) => ipcRenderer.invoke('config:set', patch),
  applyConfig: (patch) => ipcRenderer.invoke('config:apply', patch),   // 저장하고 바로 적용 (설정 창)
  saveText: (filename, text) => ipcRenderer.invoke('file:saveText', filename, text),
  relaunch: () => ipcRenderer.send('app:relaunch'),
  onSettingsOpen: (cb) => ipcRenderer.on('settings:open', () => cb()),
  listModels: () => ipcRenderer.invoke('models:list'),
  resolveModel: (name) => ipcRenderer.invoke('models:resolve', name),
  getProfile: (name) => ipcRenderer.invoke('profile:get', name),            // 모델 폴더의 persona.txt / emotionAliases.json
  saveProfile: (name, data) => ipcRenderer.invoke('profile:save', name, data),
  getLyricsItem: (name) => ipcRenderer.invoke('item:get', name),            // 가사창 아이템(마이크 스탠드): 모델별 위치/크기 + 그림
  saveLyricsItem: (name, item) => ipcRenderer.invoke('item:save', name, item),
  getHotkeys: () => ipcRenderer.invoke('hotkeys:get'),               // 단축키 탭: 기본값/바꾼 값/등록 상태
  saveHotkeys: (overrides) => ipcRenderer.invoke('hotkeys:save', overrides),
  pauseHotkeys: (paused) => ipcRenderer.send('hotkeys:pause', !!paused),   // 키를 눌러 정하는 동안 잠시 풀기
  setIgnoreMouse: (ignore) => ipcRenderer.send('win:setIgnore', ignore),
  setEditMode: (v) => ipcRenderer.send('edit:set', v),
  getEditMode: () => ipcRenderer.invoke('edit:get'),
  onEditMode: (cb) => ipcRenderer.on('edit:mode', (_e, v) => cb(v)),
  showMenu: () => ipcRenderer.send('menu:show'),
  focusWindow: () => ipcRenderer.send('win:focus'),
  captureScreen: () => ipcRenderer.invoke('capture:screen'),
  onCapture: (cb) => ipcRenderer.on('capture:request', (_e, mode) => cb(mode)),
  onSessionsToggle: (cb) => ipcRenderer.on('sessions:toggle', () => cb()),
  onSessionsNew: (cb) => ipcRenderer.on('sessions:new', () => cb()),
  onCursor: (cb) => ipcRenderer.on('cursor:pos', (_e, pos) => cb(pos)),
  onModelSelect: (cb) => ipcRenderer.on('model:select', (_e, name) => cb(name)),
  onConfigChanged: (cb) => ipcRenderer.on('config:changed', (_e, cfg) => cb(cfg)),
  // 미디어/볼륨
  media: (action, value) => ipcRenderer.invoke('media:run', action, value),
  // 실시간 가사 (메인 프로세스가 곡 감시/가사 찾기를 하고 여기로 보낸다)
  onLyricsTrack: (cb) => ipcRenderer.on('lyrics:track', (_e, v) => cb(v)),
  onLyricsLines: (cb) => ipcRenderer.on('lyrics:lines', (_e, v) => cb(v)),
  onLyricsPlayback: (cb) => ipcRenderer.on('lyrics:playback', (_e, v) => cb(v)),
  onLyricsOffset: (cb) => ipcRenderer.on('lyrics:offset', (_e, v) => cb(v)),
  onLyricsIdle: (cb) => ipcRenderer.on('lyrics:idle', () => cb()),
  onLyricsFlash: (cb) => ipcRenderer.on('lyrics:flash', (_e, v) => cb(v)),
  lyricsSnapshot: () => ipcRenderer.invoke('lyrics:snapshot'),
  lyricsCandidates: (query) => ipcRenderer.invoke('lyrics:candidates', query),   // 가사 직접 고르기
  lyricsPreview: (key) => ipcRenderer.invoke('lyrics:preview', key),
  lyricsChoose: (key) => ipcRenderer.invoke('lyrics:choose', key),
  onLyricsPick: (cb) => ipcRenderer.on('lyrics:pick', () => cb()),
  getNowPlaying: () => ipcRenderer.invoke('nowplaying:get'),   // 곡에 대해 물었을 때 AI 에게 알려 줄 지금 재생 중인 곡
  lyricsCommand: (action, arg) => ipcRenderer.invoke('lyrics:cmd', action, arg),
  // 먼저 말 걸기
  proactiveSet: (patch) => ipcRenderer.invoke('proactive:set', patch),
  systemState: () => ipcRenderer.invoke('system:state'),
  // 일정
  scheduleAdd: (item) => ipcRenderer.invoke('schedule:add', item),
  scheduleList: () => ipcRenderer.invoke('schedule:list'),
  scheduleRemove: (idOrAll) => ipcRenderer.invoke('schedule:remove', idOrAll),
  readClipboard: () => ipcRenderer.invoke('clipboard:read'),
  onScheduleFire: (cb) => ipcRenderer.on('schedule:fire', (_e, s) => cb(s)),
  // 알림 읽기 / 마이크
  onMicToggle: (cb) => ipcRenderer.on('mic:toggle', () => cb()),
  micState: (active) => ipcRenderer.send('mic:state', !!active),   // 트레이 메뉴의 마이크 체크 표시용
  // 음성 전처리 (메인 프로세스의 onnxruntime): VAD 프레임(16kHz 512샘플) -> 말소리 확률, 화자 인증
  sttTranscribe: (cfg, audio) => ipcRenderer.invoke('stt:transcribe', cfg, audio),   // 음성 -> 글자 (메인 프로세스가 서버로 전송)
  vadInit: () => ipcRenderer.invoke('vad:init'),
  vadReset: () => ipcRenderer.send('vad:reset'),
  vadFrame: (frame) => ipcRenderer.invoke('vad:frame', frame),
  speakerStatus: () => ipcRenderer.invoke('speaker:status'),
  speakerVerify: (pcm) => ipcRenderer.invoke('speaker:verify', pcm),
  speakerEnroll: (pcms) => ipcRenderer.invoke('speaker:enroll', pcms),
  speakerClear: () => ipcRenderer.invoke('speaker:clear'),
  onSpeakerEnroll: (cb) => ipcRenderer.on('speaker:enroll', () => cb()),
  onSpeakerCleared: (cb) => ipcRenderer.on('speaker:cleared', () => cb()),
  onProactiveNow: (cb) => ipcRenderer.on('proactive:now', () => cb()),
  onProactivePrompt: (cb) => ipcRenderer.on('proactive:prompt', () => cb()),   // 트레이의 "간격 직접 입력…"
});
