// 가사창이 뜰 때 모델 옆에 같이 뜨는 아이템(마이크 스탠드 같은 그림)의 위치/크기 계산. 화면에 그리는 건 app.js, 파일 저장은 native/profile.js.
// 아이템은 모델에 "핀 고정"이라서 모델을 옮기거나 크기를 바꾸면 같이 움직인다. 값은 모델 크기에 대한 비율이라 모델 크기를 바꿔도 맞는다.
//   x, y   그림의 윗가운데(마이크 머리) 위치. 모델 발밑 가운데가 (0,0), 단위는 모델 높이. y 가 음수면 위쪽
//   scale  그림 높이 / 모델 높이

export const ITEM_DEFAULT = { x: 0, y: -0.74, scale: 0.8 };
export const ITEM_LIMITS = { x: [-1.5, 1.5], y: [-2, 1], scale: [0.05, 3] };

const clamp = (v, [lo, hi]) => Math.min(hi, Math.max(lo, v));
const round4 = (n) => Math.round(n * 10000) / 10000;

// 어떤 값이든 올바른 { x, y, scale } 로 (빠졌거나 숫자가 아닌 값은 기본값, 범위 밖은 범위 안으로)
export function normalizeItem(v) {
  const src = v && typeof v === 'object' ? v : {};
  const out = {};
  for (const k of Object.keys(ITEM_DEFAULT)) {
    const n = Number(src[k]);
    out[k] = round4(clamp(Number.isFinite(n) && src[k] !== null && src[k] !== '' ? n : ITEM_DEFAULT[k], ITEM_LIMITS[k]));
  }
  return out;
}

// 화면에서 그림이 차지하는 칸. model: { x, y, height } (모델 발밑 가운데 좌표와 화면에 그려진 높이), aspect: 그림 가로/세로
export function itemRect(model, item, aspect) {
  const height = item.scale * model.height;
  const width = height * (aspect > 0 ? aspect : 1);
  return { left: model.x + item.x * model.height - width / 2, top: model.y + item.y * model.height, width, height };
}

// 마우스로 (dx, dy) 픽셀 끌었을 때
export function moveItem(item, modelHeight, dx, dy) {
  const H = modelHeight > 0 ? modelHeight : 1;
  return normalizeItem({ ...item, x: item.x + dx / H, y: item.y + dy / H });
}

// 크기를 factor 배로. 윗부분(마이크 머리)이 고정이라 위치는 그대로다
export function scaleItem(item, factor) {
  return normalizeItem({ ...item, scale: item.scale * factor });
}

// 지금 아이템을 보여 줄까. 가사창 전용 표정/모션과 같은 규칙이다:
//  - 편집 모드에서는 항상(위치를 잡아야 하니까)
//  - 가사창이 보이고 노래가 재생 중이면 보인다. 일시정지/곡 사이에는 releaseMs 동안만 더 보이다가 내린다. 가사창이 안 보이면 바로 내린다
// s: { enabled, editMode, lyricsVisible, playing, wasOn, offAt }  ->  { on, offAt }  (offAt: 재생이 멈춘 시각, 다음 호출에 그대로 넘긴다)
export function itemShown(s, now, releaseMs = 3000) {
  if (!s.enabled) return { on: false, offAt: 0 };
  if (s.editMode) return { on: true, offAt: 0 };
  if (!s.lyricsVisible) return { on: false, offAt: 0 };
  if (s.playing) return { on: true, offAt: 0 };
  if (!s.wasOn) return { on: false, offAt: 0 };   // 멈춘 채로 가사창이 뜬 경우엔 띄우지 않는다
  const offAt = s.offAt || now;
  return { on: now - offAt < releaseMs, offAt };
}

// 알파 값(0~255) 배열 -> 클릭 판정용 마스크. 그림이 있는 곳만 잡히게
export function buildMask(alpha, w, h) {
  const bits = new Uint8Array(w * h);
  for (let i = 0; i < bits.length; i++) bits[i] = alpha[i] > 16 ? 1 : 0;
  return { w, h, bits };
}

// 화면 좌표 (x, y) 가 그림이 있는 곳인가. 마스크가 없으면(알파를 못 읽은 이미지) 그림 전체 칸으로 본다. 얇은 기둥도 잡히게 주변 grace 픽셀까지 본다.
export function hitMask(mask, rect, x, y, grace = 3) {
  if (!rect || !(rect.width > 0) || !(rect.height > 0)) return false;
  const pts = [[0, 0], [grace, 0], [-grace, 0], [0, grace], [0, -grace]];
  for (const [ox, oy] of pts) {
    const u = (x + ox - rect.left) / rect.width;
    const v = (y + oy - rect.top) / rect.height;
    if (u < 0 || u >= 1 || v < 0 || v >= 1) continue;
    if (!mask) return true;
    if (mask.bits[Math.floor(v * mask.h) * mask.w + Math.floor(u * mask.w)]) return true;
  }
  return false;
}
