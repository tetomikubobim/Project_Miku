// 렌더러가 보낸 '먼저 말 걸기' 설정 변경에서 허용한 항목만 골라낸다.
// 주의: Number(null), Number(''), Number(false) 는 0 이라서, 숫자/숫자 문자열만 인정해야 한다.
const asNumber = (v) => (typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN);

function sanitizePatch(patch) {
  const clean = {};
  if (!patch || typeof patch !== 'object') return clean;
  if (typeof patch.enabled === 'boolean') clean.enabled = patch.enabled;
  for (const k of ['minMinutes', 'maxMinutes']) {
    const n = asNumber(patch[k]);
    if (Number.isFinite(n)) clean[k] = Math.min(Math.max(Math.round(n), 1), 1440);
  }
  return clean;
}

// ---- 트레이 메뉴의 '먼저 말 거는 간격' (숫자는 사용자가 직접 입력: renderer/interval.js) ----
const DEFAULT_MIN = 20;
const DEFAULT_MAX = 45;

// 저장된 설정에서 지금 적용 중인 값 (렌더러의 normalizeProactive 와 같은 기본값/보정)
function effective(raw) {
  const p = raw && typeof raw === 'object' ? raw : {};
  const num = (v, d) => { const n = asNumber(v); return Number.isFinite(n) ? n : d; };
  const min = Math.min(Math.max(num(p.minMinutes, DEFAULT_MIN), 1), 720);
  const max = Math.min(Math.max(num(p.maxMinutes, DEFAULT_MAX), min), 1440);
  return { enabled: p.enabled === true, min, max };
}

// 렌더러의 describe() 와 같은 표기 (말풍선과 트레이가 다르게 보이지 않게)
const intervalLabel = (min, max) => (min === max ? `${min}분마다` : `${min}~${max}분마다`);

module.exports = { sanitizePatch, effective, intervalLabel };
