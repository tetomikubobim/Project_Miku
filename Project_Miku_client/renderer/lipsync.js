// 립싱크 계산/적용 유틸. (pixi-live2d-display 0.4.0 에는 speak() 가 없어서 직접 구현)
// 순수 함수 위주로 만들어서 PIXI 없이도 테스트할 수 있게 했다.

// AnalyserNode.getByteTimeDomainData() 결과(0~255, 무음=128)의 RMS 음량 (0~1)
export function rmsOf(buf) {
  let sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const d = (buf[i] - 128) / 128;
    sum += d * d;
  }
  return Math.sqrt(sum / buf.length);
}

// 입 벌림 값을 부드럽게 갱신한다. 열릴 때는 빠르게, 닫힐 때는 조금 느리게.
export function nextMouth(cur, rms, gain = 5) {
  return smoothMouth(cur, Math.min(1, rms * gain));
}

// 목표 입 벌림(0~1)으로 부드럽게 다가간다 (노래 가사처럼 음량이 아닌 값으로 직접 정할 때)
export function smoothMouth(cur, target) {
  const k = target > cur ? 0.6 : 0.3;
  const v = cur + (target - cur) * k;
  return v < 0.01 ? 0 : v;
}

// 모델의 입 파라미터(ParamMouthOpenY 등)에 값을 쓰는 함수를 만든다.
// 1순위: Core 모델의 parameters 배열에 직접 쓰기, 2순위: 프레임워크의 setParameterValueById
export function createMouthWriter(coreModel, ids) {
  let raw = null;
  try { raw = typeof coreModel.getModel === 'function' ? coreModel.getModel() : coreModel._model; } catch { /* ignore */ }

  const indexes = raw && raw.parameters && raw.parameters.ids
    ? ids.map((id) => Array.from(raw.parameters.ids).indexOf(id)).filter((i) => i >= 0)
    : [];

  if (indexes.length) {
    return { ok: true, write(v) { for (const i of indexes) raw.parameters.values[i] = v; } };
  }
  if (typeof coreModel.setParameterValueById === 'function') {
    return {
      ok: true,
      write(v) { for (const id of ids) { try { coreModel.setParameterValueById(id, v); } catch { /* ignore */ } } },
    };
  }
  return { ok: false, write() {} };
}

// 오디오 버퍼(지연) 설정 값. 'playback'(기본, 버퍼 넉넉) | 'balanced' | 'interactive'(버퍼 최소) 또는 초 단위 숫자(0.005~1).
// 비었거나 올바르지 않으면 'playback'. config.json 에 숫자(0.2)나 글자("0.2") 어느 쪽으로 적어도 받는다.
export const AUDIO_LATENCY_HINTS = ['playback', 'balanced', 'interactive'];
export function audioLatencyError(raw) {
  const s = String(raw ?? '').trim().toLowerCase();
  if (s === '' || AUDIO_LATENCY_HINTS.includes(s)) return '';
  const n = Number(s);
  if (Number.isFinite(n) && n >= 0.005 && n <= 1) return '';
  return '오디오 버퍼는 playback / balanced / interactive 중 하나이거나, 0.005 ~ 1 사이의 초 단위 숫자여야 해요 (예: 0.2)';
}
export function normalizeAudioLatency(raw) {
  if (audioLatencyError(raw)) return 'playback';
  const s = String(raw ?? '').trim().toLowerCase();
  if (s === '') return 'playback';
  return AUDIO_LATENCY_HINTS.includes(s) ? s : Number(s);
}
