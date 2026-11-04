// 음성 입력 전처리 관리자: Silero VAD + (선택) 화자 인증. main.js 의 IPC 핸들러가 이걸 부른다.
//
// config.json:
//   "vad":     { "model": "voice-models/silero_vad.onnx" }
//   "speaker": { "enabled": false, "model": "voice-models/speaker.onnx", "threshold": 0.5, "minSeconds": 0.5, "shortPolicy": "reject" }
//
// 필요한 것이 없으면 (onnxruntime-node / 모델 파일) 예외를 던지지 않고 { ok:false, reason } 으로 알려 준다.
// 마이크 쪽은 VAD 를 못 쓰면 예전 음량 방식으로, 화자 인증을 못 쓰면 건너뛰고 그냥 동작한다.

const fs = require('fs');
const path = require('path');
const { SileroVad } = require('./vad');
const { SpeakerVerifier } = require('./speaker');

const DEFAULT_VAD_MODEL = 'voice-models/silero_vad.onnx';
const DEFAULT_SPEAKER_MODEL = 'voice-models/speaker.onnx';
const PROFILE_FILE = 'speaker-profile.json';

// IPC 로 넘어온 값을 Float32Array 로 맞춘다 (Buffer/Uint8Array 로 오는 경우 대비)
function toF32(x) {
  if (x instanceof Float32Array) return x;
  if (ArrayBuffer.isView(x)) {
    const copy = new Uint8Array(x.byteLength); copy.set(new Uint8Array(x.buffer, x.byteOffset, x.byteLength));
    return new Float32Array(copy.buffer);
  }
  if (x instanceof ArrayBuffer) return new Float32Array(x.slice(0));
  return Float32Array.from(x || []);
}

class Voice {
  constructor({ baseDir, getConfig = () => ({}), loadOrt } = {}) {
    this.baseDir = baseDir;
    this.getConfig = getConfig;
    this.loadOrt = loadOrt || (() => require('onnxruntime-node'));
    this._ort = undefined;
    this.vad = null; this.vadKey = '';
    this.speaker = null; this.speakerKey = '';
  }

  _ortOrNull() {
    if (this._ort === undefined) {
      try { this._ort = this.loadOrt(); } catch (e) { this._ort = null; this.ortError = (e && e.message) || String(e); }
    }
    return this._ort;
  }

  _resolve(p, fallback) { const v = p || fallback; return path.isAbsolute(v) ? v : path.join(this.baseDir, v); }

  // ---------- VAD ----------
  async initVad() {
    const ort = this._ortOrNull();
    if (!ort) return { ok: false, reason: `onnxruntime-node 를 불러오지 못했어요. 클라이언트 폴더에서 npm install 을 실행하세요. (${this.ortError || ''})`.trim() };
    const modelPath = this._resolve((this.getConfig().vad || {}).model, DEFAULT_VAD_MODEL);
    if (!fs.existsSync(modelPath)) return { ok: false, reason: `VAD 모델 파일이 없어요: ${modelPath}\nnpm run get-models 로 받을 수 있어요.` };
    if (this.vad && this.vad.ready && this.vadKey === modelPath) { this.vad.reset(); return { ok: true }; }
    try {
      const v = new SileroVad({ ort, modelPath });
      await v.init();
      this.vad = v; this.vadKey = modelPath;
      return { ok: true };
    } catch (e) {
      this.vad = null;
      return { ok: false, reason: `VAD 모델을 불러오지 못했어요: ${(e && e.message) || e}` };
    }
  }

  vadReset() { if (this.vad) this.vad.reset(); }

  async vadFrame(frame) {
    if (!this.vad || !this.vad.ready) throw new Error('VAD 가 준비되지 않았어요');
    return this.vad.process(toF32(frame));
  }

  // ---------- 화자 인증 ----------
  _speakerCfg() {
    const s = this.getConfig().speaker || {};
    return {
      enabled: s.enabled === true,
      model: this._resolve(s.model, DEFAULT_SPEAKER_MODEL),
      threshold: Number.isFinite(Number(s.threshold)) ? Number(s.threshold) : 0.5,
      minSeconds: Number.isFinite(Number(s.minSeconds)) ? Number(s.minSeconds) : 0.5,
      shortPolicy: s.shortPolicy === 'pass' ? 'pass' : 'reject',
    };
  }

  // 설정이 바뀌었으면 다시 만든다
  async _speaker() {
    const c = this._speakerCfg();
    const key = JSON.stringify(c);
    if (this.speaker && key === this.speakerKey) {
      if (!this.speaker.available) await this.speaker.init();   // 실행 중에 모델을 받아 넣었을 수 있으니 다시 확인 (재시작 불필요)
      return this.speaker;
    }
    const sp = new SpeakerVerifier({
      ort: this._ortOrNull(), modelPath: c.model, storePath: path.join(this.baseDir, PROFILE_FILE),
      threshold: c.threshold, minSeconds: c.minSeconds, shortPolicy: c.shortPolicy,
    });
    await sp.init();
    this.speaker = sp; this.speakerKey = key;
    return sp;
  }

  async speakerStatus() {
    const c = this._speakerCfg();
    const sp = await this._speaker();
    return { enabled: c.enabled, available: sp.available, enrolled: sp.enrolled, reason: sp.reason };
  }

  async speakerVerify(pcm) {
    if (!this._speakerCfg().enabled) return { ok: true, skipped: 'disabled' };
    const sp = await this._speaker();
    return sp.verify(toF32(pcm));
  }

  async speakerEnroll(pcms) {
    const sp = await this._speaker();
    if (!sp.available) return { ok: false, reason: sp.reason || '화자 모델이 준비되지 않았어요' };
    try {
      const r = await sp.enroll((pcms || []).map(toF32));
      return { ok: true, ...r };
    } catch (e) {
      return { ok: false, reason: (e && e.message) || String(e) };
    }
  }

  async speakerClear() {
    const sp = await this._speaker();
    sp.clear();
    return { ok: true };
  }
}

module.exports = { Voice, toF32 };
