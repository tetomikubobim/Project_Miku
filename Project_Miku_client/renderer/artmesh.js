// VTube Studio 의 아트메시 색/투명도 설정을 클라이언트에도 적용한다.
//
// VTube Studio 에서 아트메시의 색(곱하기/스크린)과 투명도를 바꾸면 그 값이 *.vtube.json 의
// ArtMeshDetails.ArtMeshMultiplyAndScreenColors 에 이렇게 저장된다:  { "ID": "WaterMark3", "Value": "FFFFFF00|000000FF" }
//                                                                        곱하기 색(RRGGBBAA) | 스크린 색(RRGGBBAA)
// 곱하기 색의 알파가 그 아트메시의 투명도다 (00 이면 완전히 안 보임). Live2D 렌더러(pixi-live2d-display)는 이 값을 모르기 때문에
// 스튜디오에서는 투명한 워터마크가 클라이언트에서는 그대로 보였다. 그래서 모델이 한 프레임을 계산한 직후에 그 값을 직접 덮어쓴다.
//   - 투명도: drawable 불투명도 *= 곱하기 알파        (항상 적용)
//   - 색:     drawables.multiplyColors / screenColors 에 기록 (렌더러가 지원하면 보이고, 아니면 무해하게 무시됨)

const clamp01 = (n) => Math.min(1, Math.max(0, n));

// "RRGGBBAA" -> {r,g,b,a} (각 0~1). 형식이 틀리면 null.
export function parseColor(hex) {
  const m = /^([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || '').trim());
  if (!m) return null;
  const [r, g, b, a] = m.slice(1).map((h) => parseInt(h, 16) / 255);
  return { r, g, b, a };
}

const isWhite = (c) => c.r > 0.999 && c.g > 0.999 && c.b > 0.999;
const isBlack = (c) => c.r < 0.001 && c.g < 0.001 && c.b < 0.001;

// vtube.json -> [{ id, multiply:{r,g,b,a}, screen:{r,g,b,a}, opacity, tint }]
// 아무것도 안 바꾸는 항목(흰색 곱하기, 불투명, 검정 스크린)과 형식이 틀린 항목은 뺀다. 같은 ID 가 또 나오면 뒤의 것이 이긴다.
export function parseOverrides(vts) {
  const list = vts && vts.ArtMeshDetails && vts.ArtMeshDetails.ArtMeshMultiplyAndScreenColors;
  if (!Array.isArray(list)) return [];
  const byId = new Map();
  for (const e of list) {
    if (!e || typeof e.ID !== 'string' || !e.ID || typeof e.Value !== 'string') continue;
    const [mul, scr = '000000FF'] = e.Value.split('|');
    const multiply = parseColor(mul);
    const screen = parseColor(scr);
    if (!multiply || !screen) continue;
    const opacity = clamp01(multiply.a);
    const tint = !isWhite(multiply) || !isBlack(screen);
    if (opacity >= 0.999 && !tint) { byId.delete(e.ID); continue; }
    byId.set(e.ID, { id: e.ID, multiply, screen, opacity, tint });
  }
  return [...byId.values()];
}

// 모델의 drawables(Core 가 한 프레임 계산한 결과)에 덮어쓴다. 돌려주는 값: { applied, missing:[없는 ID...] }
export function applyOverrides(drawables, overrides, indexOf) {
  let applied = 0;
  const missing = [];
  for (const o of overrides) {
    const i = indexOf(o.id);
    if (i < 0) { missing.push(o.id); continue; }
    if (o.opacity < 0.999 && drawables.opacities) drawables.opacities[i] *= o.opacity;
    if (o.tint) {
      const mc = drawables.multiplyColors, sc = drawables.screenColors;
      if (mc && mc.length >= (i + 1) * 4) { mc[i * 4] = o.multiply.r; mc[i * 4 + 1] = o.multiply.g; mc[i * 4 + 2] = o.multiply.b; mc[i * 4 + 3] = 1; }
      if (sc && sc.length >= (i + 1) * 4) { sc[i * 4] = o.screen.r; sc[i * 4 + 1] = o.screen.g; sc[i * 4 + 2] = o.screen.b; sc[i * 4 + 3] = 1; }
    }
    applied++;
  }
  return { applied, missing };
}

function drawablesOf(coreModel) {
  const raw = typeof coreModel.getModel === 'function' ? coreModel.getModel() : coreModel._model;
  return raw && raw.drawables ? raw.drawables : null;
}

// 모델이 매 프레임 계산을 끝낸 직후(coreModel.update 직후, 그리기 직전)에 덮어쓰도록 update 를 감싼다.
// isEnabled(): 설정에서 껐으면 false. 돌려주는 값: { count, applied, missing } (적용할 게 없거나 지원 안 되면 count 0)
export function setupArtMeshOverrides(internalModel, vts, { isEnabled = () => true, log = () => {} } = {}) {
  const overrides = parseOverrides(vts);
  if (!overrides.length) return { count: 0, applied: 0, missing: [] };
  const core = internalModel && internalModel.coreModel;
  const drawables = core && drawablesOf(core);
  if (!core || !drawables || !drawables.ids || typeof core.update !== 'function') {
    log('warn', '[artmesh] 이 렌더러에서는 아트메시 접근을 지원하지 않아서 적용하지 못했어요');
    return { count: overrides.length, applied: 0, missing: [], unsupported: true };
  }
  const index = new Map();
  for (let i = 0; i < drawables.ids.length; i++) index.set(drawables.ids[i], i);
  const indexOf = (id) => (index.has(id) ? index.get(id) : -1);

  const probe = applyOverrides({}, overrides, indexOf);   // 모델에 있는 ID 만 센다 (실제 값은 쓰지 않음)
  const hidden = overrides.filter((o) => o.opacity < 0.001 && indexOf(o.id) >= 0).length;
  log('info', `[artmesh] vtube.json 의 아트메시 설정 ${overrides.length}개 중 ${probe.applied}개 적용 (완전 투명 ${hidden}개)` +
    (probe.missing.length ? ` / 모델에 없는 ID: ${probe.missing.slice(0, 8).join(', ')}${probe.missing.length > 8 ? ' …' : ''}` : ''));

  if (!core._artMeshPatched) {
    core._artMeshPatched = true;
    const orig = core.update.bind(core);
    core.update = (...args) => {
      const r = orig(...args);
      if (isEnabled()) {
        try { applyOverrides(drawablesOf(core) || drawables, overrides, indexOf); } catch { /* 그리기를 막지 않는다 */ }
      }
      return r;
    };
  }
  return { count: overrides.length, applied: probe.applied, missing: probe.missing };
}
