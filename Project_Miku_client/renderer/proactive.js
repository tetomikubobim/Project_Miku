// '먼저 말 걸기' 모드: 사용자가 한동안 말이 없으면 캐릭터가 먼저 말을 건다.
//
//  - 사용자가 마지막으로 뭔가 한 뒤 minMinutes~maxMinutes 사이의 (무작위) 시간이 지나면 서버에 'proactive' 를 보낸다.
//  - 사용자가 말/입력/마이크를 쓰면 타이머가 처음부터 다시 시작한다.
//  - 말을 걸었는데 답이 없으면 간격이 점점 늘어나고(1배, 2배, 3배…), maxUnanswered 번 연속 무시되면 사용자가 다시 반응할 때까지 멈춘다.
//  - 말을 걸면 안 되는 때는 조건이 풀릴 때까지 기다린다: 이미 말하는 중/마이크 사용 중/입력 중, 창이 숨겨짐, 자리를 비움, 조용한 시간대.
//    (자리를 비웠다가 돌아오면 기다리던 말을 바로 건다)

export const DEFAULTS = {
  enabled: false,
  minMinutes: 20,
  maxMinutes: 45,
  awayMinutes: 10,        // 이 시간 넘게 키보드/마우스 입력이 없으면 자리를 비운 것으로 보고 말하지 않는다 (0 이면 확인 안 함)
  maxUnanswered: 3,
  onlyWhenVisible: true,  // 모델을 숨겨 둔 동안에는 말하지 않는다
  quietHours: null,       // 예: ["23:00", "08:00"] (자정을 넘겨도 됨)
};

const num = (v, d) => (Number.isFinite(Number(v)) && v !== null && v !== '' ? Number(v) : d);

export function normalizeProactive(c = {}) {
  const raw = { ...DEFAULTS, ...(c || {}) };
  const min = Math.min(Math.max(num(raw.minMinutes, DEFAULTS.minMinutes), 1), 720);
  const max = Math.min(Math.max(num(raw.maxMinutes, DEFAULTS.maxMinutes), min), 1440);
  return {
    enabled: raw.enabled === true,
    minMinutes: min,
    maxMinutes: max,
    awayMinutes: Math.max(num(raw.awayMinutes, DEFAULTS.awayMinutes), 0),
    maxUnanswered: Math.max(Math.floor(num(raw.maxUnanswered, DEFAULTS.maxUnanswered)), 1),
    onlyWhenVisible: raw.onlyWhenVisible !== false,
    quietHours: Array.isArray(raw.quietHours) && raw.quietHours.length === 2 ? raw.quietHours.map(String) : null,
  };
}

const toMin = (hhmm) => {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm).trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
};

export function inQuietHours(quiet, date = new Date()) {
  if (!quiet) return false;
  const a = toMin(quiet[0]);
  const b = toMin(quiet[1]);
  if (a == null || b == null || a === b) return false;
  const now = date.getHours() * 60 + date.getMinutes();
  return a < b ? now >= a && now < b : now >= a || now < b;   // 23:00~08:00 처럼 자정을 넘는 구간
}

// 지금 캐릭터가 먼저 말을 걸어도 되는 상황인가. 상태는 호출한 쪽(app.js)이 모아서 넘긴다.
// 주의: 마이크가 '켜져만 있는' 것은 막는 이유가 아니다. 사용자가 실제로 말하는 중이거나 인식 중일 때만 막는다.
//       (예전에는 마이크가 켜져 있으면 무조건 막아서, 마이크를 켜 둔 동안에는 영영 말을 걸지 못했다)
export function canInterrupt(s) {
  return !!s.connected
    && !s.audioPlaying   // 캐릭터가 이미 말하는 중
    && !s.micSpeaking    // 사용자가 지금 말하는 중
    && !s.micBusy        // 방금 한 말을 글자로 바꾸는 중
    && !s.editMode && !s.selecting
    && !s.typing         // 입력창에 쓰던 글이 있음
    && !s.panelOpen      // 대화 목록을 보는 중
    && !s.lyricsPlaying; // 노래 가사가 나오는 중 (노래 듣는 데 끼어들지 않는다)
}

export function describe(c) {
  return `${c.minMinutes === c.maxMinutes ? `${c.minMinutes}분` : `${c.minMinutes}~${c.maxMinutes}분`}마다`;
}

export class Proactive {
  // deps: getConfig(), canSpeak() -> bool, getSystemState() -> Promise<{idleSec, visible}|null>, speak() -> bool(전송 성공)
  //       now(), rand(), setInterval(), clearInterval(), pollMs  (시험용으로 바꿀 수 있다)
  constructor(deps) {
    this.d = { now: () => Date.now(), rand: Math.random, setInterval: (...a) => setInterval(...a), clearInterval: (...a) => clearInterval(...a), pollMs: 15000, ...deps };
    this.unanswered = 0;
    this.dueAt = Infinity;
    this.timer = null;
    this.busy = false;
  }

  cfg() { return normalizeProactive(this.d.getConfig()); }

  _interval(c) {
    const minutes = c.minMinutes + this.d.rand() * (c.maxMinutes - c.minMinutes);
    return minutes * 60000 * (1 + this.unanswered);   // 무시당할수록 간격을 늘린다
  }

  start() {
    if (!this.timer) this.timer = this.d.setInterval(() => { this.tick(); }, this.d.pollMs);
    this.reschedule();
  }

  stop() { if (this.timer) this.d.clearInterval(this.timer); this.timer = null; }

  // 설정이 바뀌었거나 사용자가 뭔가 했을 때: 다음 시각을 처음부터 다시 잡는다
  reschedule() { this.dueAt = this.d.now() + this._interval(this.cfg()); }

  // 사용자가 말/입력/마이크 등으로 반응했다
  noteUserActivity() { this.unanswered = 0; this.reschedule(); }

  async tick() {
    if (this.busy) return false;
    const c = this.cfg();
    if (!c.enabled) return false;
    const t = this.d.now();
    if (t < this.dueAt) return false;
    if (this.unanswered >= c.maxUnanswered) return false;           // 계속 무시당했으면 사용자가 먼저 반응할 때까지 쉰다
    if (inQuietHours(c.quietHours, new Date(t))) return false;
    if (!this.d.canSpeak()) return false;
    this.busy = true;
    try {
      const st = await this.d.getSystemState();
      if (st) {
        if (c.onlyWhenVisible && st.visible === false) return false;
        if (c.awayMinutes > 0 && st.idleSec > c.awayMinutes * 60) return false;   // 자리를 비웠다: 돌아오면 다음 tick 에서 바로 말한다
      }
      if (!(await this.d.speak())) return false;                             // 서버 연결이 없으면 다음 tick 에 다시 시도
      this.unanswered += 1;
      this.dueAt = t + this._interval(c);
      return true;
    } finally {
      this.busy = false;
    }
  }
}
