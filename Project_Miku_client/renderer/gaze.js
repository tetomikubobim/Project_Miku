// 시선 추적: 캐릭터가 마우스 포인터를 따라 눈/고개/몸을 돌린다.
//
// 포인터 위치는 메인 프로세스가 직접 읽어 알려주므로(30Hz) 클릭 통과 중이거나 다른 모니터에 있어도 따라온다.
// pixi-live2d-display 의 model.focus() 는 거리를 무시하고 항상 끝까지 돌아가서(단위 방향), 직접 계산한다:
//   - 머리에서 포인터까지의 거리에 비례해서 돌아간다 (가까우면 조금, radius 이상 멀면 최대)
//   - 눈은 빠르게, 고개/몸은 천천히 따라가서 자연스럽다
//   - 포인터가 idleSeconds 동안 안 움직이면 정면으로 돌아온다
// 파라미터 값은 Cubism 기본 규칙(오른쪽/위가 +)이고, 모션이 정한 값에 더해서(additive) 쓴다.
// 순수 함수/클래스 위주라서 PIXI 없이도 테스트할 수 있다.

export const DEFAULTS = {
  enabled: true,
  radius: 400,        // px: 이 거리 이상 떨어지면 최대로 돌아본다
  deadZone: 24,       // px: 머리 바로 근처에서는 정면 (떨림 방지)
  strength: 1,        // 0~1 전체 세기
  head: true,         // false 면 눈동자만 움직인다
  idleSeconds: 20,    // 포인터가 이만큼 멈춰 있으면 정면으로 (0 이면 계속 바라봄)
};

const num = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v)) ? Number(v) : d);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function normalizeGaze(c) {
  const raw = c && typeof c === 'object' ? c : {};
  return {
    enabled: raw.enabled !== false,
    radius: clamp(num(raw.radius, DEFAULTS.radius), 50, 4000),
    deadZone: clamp(num(raw.deadZone, DEFAULTS.deadZone), 0, 500),
    strength: clamp(num(raw.strength, DEFAULTS.strength), 0, 1),
    head: raw.head !== false,
    idleSeconds: Math.max(num(raw.idleSeconds, DEFAULTS.idleSeconds), 0),
  };
}

// 머리 위치(head)에서 포인터(cursor)를 바라보는 방향/세기. x: 오른쪽 +, y: 위쪽 +, 각각 -1~1 (크기는 1 이하)
export function gazeTarget(cursor, head, opts = DEFAULTS) {
  const o = normalizeGaze(opts);
  if (!cursor || !head) return { x: 0, y: 0 };
  const dx = cursor.x - head.x;
  const dy = head.y - cursor.y;          // 화면 좌표는 아래가 +, 파라미터는 위가 +
  const dist = Math.hypot(dx, dy);
  if (!Number.isFinite(dist) || dist <= o.deadZone) return { x: 0, y: 0 };
  const mag = clamp((dist - o.deadZone) / Math.max(o.radius - o.deadZone, 1), 0, 1) * o.strength;
  return { x: (dx / dist) * mag, y: (dy / dist) * mag };
}

// 지수 감쇠로 목표에 다가간다. tau(ms): 작을수록 빠름. dt 가 커도(프레임 끊김) 튀지 않는다.
export function approach(cur, target, dtMs, tauMs) {
  const k = 1 - Math.exp(-Math.max(dtMs, 0) / Math.max(tauMs, 1));
  return cur + (target - cur) * k;
}

export class GazeController {
  constructor(opts = {}, tau = {}) {
    this.tau = { eye: 70, head: 220, ...tau };
    this.o = normalizeGaze(opts);
    this.eye = { x: 0, y: 0 };
    this.head = { x: 0, y: 0 };
    this.cursor = null;
    this.lastMoveAt = -Infinity;
  }

  setOptions(opts) { this.o = normalizeGaze(opts); }

  // 포인터가 (실제로) 움직였을 때 부른다
  noteCursor(x, y, now) {
    const c = this.cursor;
    if (!c || c.x !== x || c.y !== y) { this.cursor = { x, y }; this.lastMoveAt = now; }
  }

  // 지금 시각의 목표 방향
  target(headPoint, now) {
    const o = this.o;
    const idle = o.idleSeconds > 0 && now - this.lastMoveAt > o.idleSeconds * 1000;
    if (!o.enabled || idle || !this.cursor) return { x: 0, y: 0 };
    return gazeTarget(this.cursor, headPoint, o);
  }

  // 한 프레임 진행. 모델에 더할 값 { eye:{x,y}, head:{x,y} } 를 돌려준다
  step(dtMs, headPoint, now) {
    const t = this.target(headPoint, now);
    this.eye.x = approach(this.eye.x, t.x, dtMs, this.tau.eye);
    this.eye.y = approach(this.eye.y, t.y, dtMs, this.tau.eye);
    const ht = this.o.head ? t : { x: 0, y: 0 };
    this.head.x = approach(this.head.x, ht.x, dtMs, this.tau.head);
    this.head.y = approach(this.head.y, ht.y, dtMs, this.tau.head);
    for (const v of [this.eye, this.head]) { if (Math.abs(v.x) < 0.002) v.x = 0; if (Math.abs(v.y) < 0.002) v.y = 0; }
    return { eye: { ...this.eye }, head: { ...this.head } };
  }

  // 정면으로 완전히 돌아와서 더할 값이 없다 (꺼져 있을 때 매 프레임 일을 하지 않기 위함)
  get settled() { return !this.eye.x && !this.eye.y && !this.head.x && !this.head.y; }
}

// Cubism 기본 시선 파라미터와 값의 크기 (pixi-live2d-display 의 updateFocus 와 같은 비율)
export const GAZE_PARAMS = [
  { id: 'ParamEyeBallX', from: 'eye', value: (e, h) => e.x },
  { id: 'ParamEyeBallY', from: 'eye', value: (e, h) => e.y },
  { id: 'ParamAngleX', from: 'head', value: (e, h) => h.x * 30 },
  { id: 'ParamAngleY', from: 'head', value: (e, h) => h.y * 30 },
  { id: 'ParamAngleZ', from: 'head', value: (e, h) => h.x * h.y * -30 },
  { id: 'ParamBodyAngleX', from: 'head', value: (e, h) => h.x * 10 },
];

// 모델에 시선 값을 더하는 함수를 만든다. 모델에 없는 파라미터는 건너뛴다. 하나도 없으면 ok:false
// 1순위: Core 모델의 parameters 배열에 직접 더하기(범위 제한), 2순위: 프레임워크의 addParameterValueById
export function createGazeWriter(coreModel) {
  let raw = null;
  try { raw = typeof coreModel.getModel === 'function' ? coreModel.getModel() : coreModel._model; } catch { /* ignore */ }

  if (raw && raw.parameters && raw.parameters.ids && raw.parameters.values) {
    const ids = Array.from(raw.parameters.ids);
    const slots = GAZE_PARAMS.map((p) => ({ p, i: ids.indexOf(p.id) })).filter((s) => s.i >= 0);
    if (slots.length) {
      const { values, minimumValues: mins, maximumValues: maxs } = raw.parameters;
      return {
        ok: true,
        names: slots.map((s) => s.p.id),
        apply(eye, head) {
          for (const { p, i } of slots) {
            let v = values[i] + p.value(eye, head);
            if (mins && maxs) v = clamp(v, mins[i], maxs[i]);
            values[i] = v;
          }
        },
      };
    }
  }
  if (typeof coreModel.addParameterValueById === 'function') {
    return {
      ok: true,
      names: GAZE_PARAMS.map((p) => p.id),
      apply(eye, head) {
        for (const p of GAZE_PARAMS) { try { coreModel.addParameterValueById(p.id, p.value(eye, head)); } catch { /* 없는 파라미터 */ } }
      },
    };
  }
  return { ok: false, names: [], apply() {} };
}
