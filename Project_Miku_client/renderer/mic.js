// 마이크 입력: 말하는 구간만 잘라서 WAV 로 넘겨준다.
//
//   const mic = new MicListener({ onUtterance(blob, info), onState(state, detail), shouldPause(), vad, onFallback(reason) }, options);
//   await mic.start();   mic.stop();
//
//   켜 두면 끌 때까지 계속 듣고, 말이 끝날 때마다 onUtterance(blob, { seconds, pcm }) 로 넘긴다. (pcm: 16kHz 모노 Float32Array — 화자 인증용)
//   shouldPause() 가 true 인 동안(캐릭터가 말하는 중)은 입력을 버려서 자기 목소리를 듣지 않는다.
//   state: 'listening'(대기) | 'speaking'(말하는 중) | 'idle'(꺼짐)
//   mic.speaking: 지금 사용자가 말하는 중인가
//
//   말 구간 감지(VAD):
//     handlers.vad = { init() -> {ok, reason}, reset(), frame(Float32Array 512 @16kHz) -> Promise<확률 0~1> }  (Silero VAD, 메인 프로세스에서 실행)
//     vad 가 없거나 init 이 실패하면 예전 방식(음량 RMS)으로 자동 전환하고 onFallback(reason) 으로 알린다.
//
//   오디오 처리: 에코 제거(AEC)만 켠다. 노이즈 억제/자동 이득은 끈다 (options 로 바꿀 수 있음).
//     에코 제거는 두 가지: ① 브라우저 내장(앱 안에서 나는 소리만 지움) ② 시스템 소리 기준(aec3.js = WebRTC AEC3: 유튜브/음악 등 PC 에서 나는 모든 소리를 기준으로 지움)
//     handlers.loopback = { open() -> {ok, stream, reason}, close() }  (PC 에서 나는 소리를 받아 오는 장치. Electron 의 loopback)
//     handlers.createAec(opts, sampleRate) -> Promise<{process(mic, ref), free()}>  (선택. 시험용으로 에코 제거기를 바꿔 끼울 때. 기본은 aec3.js)
//     AEC3 나 loopback 을 못 열면 ① 로 돌아가고 onAecFallback(reason) 으로 알린다. options.systemAec=false 면 처음부터 ① 만 쓴다.

import { EchoCanceller } from './aec3.js';

const TARGET_RATE = 16000;
const VAD_FRAME = 512;                              // Silero 가 한 번에 보는 샘플 수 (16kHz 에서 32ms)
const VAD_FRAME_MS = (VAD_FRAME / TARGET_RATE) * 1000;

function downsample(f32, from, to) {
  if (to >= from) return { data: f32, rate: from };
  const ratio = from / to;
  const out = new Float32Array(Math.floor(f32.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const a = Math.floor(i * ratio);
    const b = Math.min(f32.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = a; j < b; j++) sum += f32[j];
    out[i] = sum / Math.max(1, b - a);
  }
  return { data: out, rate: to };
}

// 장치 샘플레이트 -> 16kHz 로 흐름 그대로 줄여 주는 간단한 평균 리샘플러
class Resampler {
  constructor(from, to) { this.step = from / to; this.t = 0; this.acc = 0; this.n = 0; }
  push(f32) {
    const out = [];
    for (let i = 0; i < f32.length; i++) {
      this.acc += f32[i]; this.n++; this.t += 1;
      if (this.t >= this.step) { out.push(this.acc / this.n); this.acc = 0; this.n = 0; this.t -= this.step; }
    }
    return Float32Array.from(out);
  }
}

function concat(chunks) {
  let total = 0;
  for (const c of chunks) total += c.length;
  const all = new Float32Array(total);
  let o = 0;
  for (const c of chunks) { all.set(c, o); o += c.length; }
  return all;
}

function encodeWav(chunks, sampleRate) {
  const all = concat(chunks);
  const { data, rate } = downsample(all, sampleRate, TARGET_RATE);

  const buf = new ArrayBuffer(44 + data.length * 2);
  const dv = new DataView(buf);
  const w = (off, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); dv.setUint32(4, 36 + data.length * 2, true); w(8, 'WAVE');
  w(12, 'fmt '); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, rate, true); dv.setUint32(28, rate * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
  w(36, 'data'); dv.setUint32(40, data.length * 2, true);
  for (let i = 0; i < data.length; i++) {
    const s = Math.max(-1, Math.min(1, data[i]));
    dv.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return { blob: new Blob([buf], { type: 'audio/wav' }), seconds: data.length / rate, pcm: data };
}

export class MicListener {
  constructor(handlers = {}, options = {}) {
    this.h = handlers;
    this.setOptions(options);
    this.stream = null;
    this.ctx = null;
    this.vadMode = false;
    this.gen = 0;
  }

  setOptions(o = {}) {
    this.o = {
      // --- 말 구간 감지: Silero VAD (확률 기준) ---
      vadThreshold: 0.5,      // 이 확률 이상이면 말소리로 본다
      vadNegThreshold: null,  // 이 확률 미만이면 조용한 것으로 본다 (비우면 vadThreshold - 0.15)
      vadSilenceMs: 800,      // 이만큼 조용하면 한 문장이 끝난 것으로 본다
      vadMinSpeechMs: 192,    // 이보다 짧은 소리는 무시
      vadMinUtteranceMs: 500, // 말소리가 처음부터 끝까지 이보다 짧으면(앞뒤 여유 구간 제외) 인식하지 않는다
      vad: true,              // false 면 Silero 를 쓰지 않고 음량 방식만 쓴다
      // --- 예전 방식(음량 RMS): Silero 를 못 쓸 때 ---
      threshold: 0.015,   // 이 음량(RMS) 이상이면 말소리로 본다. 주변 소음이 크면 자동으로 더 높아진다
      silenceMs: 1100,    // 이만큼 조용하면 한 문장이 끝난 것으로 본다
      minSpeechMs: 150,   // 이보다 짧은 소리는 무시 (기침/클릭음)
      // --- 공통 ---
      minUtteranceMs: 400,
      maxSeconds: 30,
      preRollMs: 300,     // 말이 감지되기 직전 소리도 포함 (첫 음절 잘림 방지)
      deviceId: '',
      systemAec: true,           // PC 에서 나는 소리(유튜브/음악 등)를 기준으로 에코 제거. 안 되면 아래 브라우저 내장 방식으로 자동 전환
      aec: {},                   // 에코 제거기 세부 설정 (aec3.js 의 DEFAULTS: rate = AEC3 내부 처리 속도 32000/48000)
      echoCancellation: true,    // 브라우저 내장 에코 제거 (앱 안에서 나는 소리만 지운다). systemAec 가 켜져 있는 동안은 자동으로 끈다
      noiseSuppression: false,   // 노이즈 억제는 끈다 (Silero 가 소음을 걸러 주고, 켜면 인식률이 떨어질 때가 있다)
      autoGainControl: false,
      ...o,
    };
  }

  get active() { return !!this.stream; }

  _state(s, detail) { try { this.h.onState && this.h.onState(s, detail); } catch (e) { console.warn(e); } }

  _fallback(reason) {
    this.vadMode = false;
    console.warn(`[mic] Silero VAD 를 쓰지 못해 음량 방식으로 듣습니다: ${reason}`);
    try { this.h.onFallback && this.h.onFallback(reason); } catch (e) { console.warn(e); }
  }

  _aecFallback(reason) {
    console.warn(`[mic] 시스템 소리 기준 에코 제거를 쓰지 못합니다: ${reason}`);
    try { this.h.onAecFallback && this.h.onAecFallback(reason); } catch (e) { console.warn(e); }
  }

  async start() {
    if (this.active) return;

    // 말 구간 감지기 준비: 실패해도 음량 방식으로 계속 간다
    this.vadMode = false;
    if (this.o.vad !== false && this.h.vad) {
      try {
        const r = await this.h.vad.init();
        if (r && r.ok) this.vadMode = true; else this._fallback((r && r.reason) || '알 수 없는 이유');
      } catch (e) { this._fallback((e && e.message) || String(e)); }
    }

    // 가능하면 처음부터 16kHz 로 받는다 (안 되면 장치 기본 속도로 받아서 직접 줄인다)
    let ctx;
    try { ctx = new AudioContext({ sampleRate: TARGET_RATE }); } catch { ctx = new AudioContext(); }

    // 시스템 소리 기준 에코 제거 (AEC3): PC 에서 나는 소리를 기준 신호로 받아 온다.
    // AEC3(WASM)를 먼저 준비하고(실패하면 loopback 은 열지도 않는다), 그다음 loopback 을 연다. 소리 속도는 AEC3 가 알아서 맞춘다.
    let refStream = null, aec = null;
    if (this.o.systemAec !== false && this.h.loopback) {
      if (!ctx.createChannelMerger) this._aecFallback('이 환경에서는 마이크와 PC 소리를 한 노드로 합칠 수 없어요');
      else {
        try {
          aec = await (this.h.createAec ? this.h.createAec(this.o.aec || {}, ctx.sampleRate) : EchoCanceller.create(this.o.aec || {}, ctx.sampleRate));
          const r = await this.h.loopback.open();
          if (r && r.stream) refStream = r.stream; else this._aecFallback((r && r.reason) || '알 수 없는 이유');
        } catch (e) { this._aecFallback((e && e.message) || String(e)); }
        if (!refStream && aec) { try { aec.free(); } catch { /* ignore */ } aec = null; }
      }
    }

    // 시스템 소리 기준 에코 제거를 쓰는 동안에는 브라우저 내장 에코 제거를 끈다 (두 번 처리하면 필터가 서로 엇갈린다)
    const audio = { echoCancellation: refStream ? false : this.o.echoCancellation !== false, noiseSuppression: this.o.noiseSuppression === true, autoGainControl: this.o.autoGainControl === true };
    if (this.o.deviceId) audio.deviceId = { exact: this.o.deviceId };
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio });
    } catch (e) {
      try { ctx.close(); } catch { /* ignore */ }
      if (refStream) { try { this.h.loopback.close(); } catch { /* ignore */ } }
      if (aec) { try { aec.free(); } catch { /* ignore */ } }
      throw e;
    }
    this.ctx = ctx;

    const src = this.ctx.createMediaStreamSource(this.stream);
    const sink = this.ctx.createGain(); // ScriptProcessor 는 출력에 연결돼야 동작한다. 소리는 0 으로 막는다.
    sink.gain.value = 0;
    if (refStream) {
      // 마이크(채널 0) 와 기준 소리(채널 1)를 한 노드로 합쳐서 받는다 -> 두 신호의 시간이 같은 클럭으로 정확히 맞는다
      const refSrc = this.ctx.createMediaStreamSource(refStream);
      const merger = this.ctx.createChannelMerger(2);
      src.connect(merger, 0, 0); refSrc.connect(merger, 0, 1);
      this.proc = this.ctx.createScriptProcessor(2048, 2, 1);
      merger.connect(this.proc);
      this.nodes = [src, refSrc, merger, this.proc, sink];
      this.aec = aec;
    } else {
      this.proc = this.ctx.createScriptProcessor(2048, 1, 1);
      src.connect(this.proc);
      this.nodes = [src, this.proc, sink];
      this.aec = null;
    }
    this.proc.connect(sink); sink.connect(this.ctx.destination);

    this.gen++;
    this._paused = false;
    this._reset();
    this.noise = 0;
    this.proc.onaudioprocess = (e) => {
      const ib = e.inputBuffer;
      let chunk = new Float32Array(ib.getChannelData(0));
      if (this.aec && ib.numberOfChannels >= 2) {
        try { chunk = this.aec.process(chunk, ib.getChannelData(1)); } catch (err) { console.warn('[mic] 에코 제거 오류 -> 이번 소리는 그대로 사용', err); }
      }
      this._frame(chunk);
    };
    console.info(`[mic] 시작 rate=${this.ctx.sampleRate}Hz 감지=${this.vadMode ? 'Silero VAD' : '음량'} AEC=${this.aec ? '시스템 소리 기준' : audio.echoCancellation ? '브라우저 내장' : '없음'} NS=${audio.noiseSuppression}`);
    this._state('listening');
  }

  stop(reason = 'manual') {
    if (!this.active) return;
    this.gen++;   // 처리 중이던 프레임 결과는 버린다
    try { this.proc.onaudioprocess = null; this.nodes.forEach((n) => n.disconnect()); } catch { /* ignore */ }
    try { this.stream.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
    if (this.aec) { try { this.h.loopback.close(); } catch { /* ignore */ } try { this.aec.free(); } catch { /* ignore */ } }   // WASM 메모리 해제
    try { this.ctx.close(); } catch { /* ignore */ }
    this.stream = null; this.ctx = null; this.proc = null; this.nodes = []; this.aec = null;
    this._reset();
    console.info(`[mic] 종료 (${reason})`);
    this._state('idle', reason);
  }

  _reset(resetVad = false) {
    this.speaking = false;
    this.voicedMs = 0;
    this.preRoll = [];
    this.preRollMs = 0;
    this.chunks = [];
    // VAD 모드용
    this.resampler = null;
    this.pending = new Float32Array(0);
    this.silentMs = 0;
    this.speechMs = 0;
    this.voiceStartMs = 0;
    this.voiceEndMs = 0;
    this._vadQueue = Promise.resolve();
    if (resetVad && this.vadMode && this.h.vad && this.h.vad.reset) { try { this.h.vad.reset(); } catch { /* ignore */ } }
  }

  _frame(chunk) {
    if (!this.active) return;
    const rate = this.ctx.sampleRate;
    const dt = (chunk.length / rate) * 1000;
    const now = Date.now();

    // 캐릭터가 말하는 중에는 자기 목소리를 듣지 않는다
    if (this.h.shouldPause && this.h.shouldPause()) {
      const wasSpeaking = this.speaking;
      if (!this._paused) { this._paused = true; this.gen++; this._reset(true); }   // 멈추는 순간 한 번: 처리 중이던 프레임은 버리고 VAD 도 처음 상태로
      else this._reset();
      if (wasSpeaking) this._state('listening');
      return;
    }
    this._paused = false;

    if (this.vadMode) { this._vadFeed(chunk, rate); return; }

    let sum = 0;
    for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i];
    const rms = Math.sqrt(sum / chunk.length);

    if (!this.speaking) {
      this.preRoll.push(chunk); this.preRollMs += dt;
      while (this.preRollMs > this.o.preRollMs && this.preRoll.length > 1) this.preRollMs -= (this.preRoll.shift().length / rate) * 1000;

      const thr = Math.max(this.o.threshold, this.noise * 3);
      if (rms > thr) {
        this.voicedMs += dt;
        if (this.voicedMs >= this.o.minSpeechMs) {
          this.speaking = true;
          this.chunks = this.preRoll; this.preRoll = []; this.preRollMs = 0;
          this.speechStart = now; this.lastVoice = now;
          this._state('speaking');
        }
      } else {
        this.voicedMs = 0;
        this.noise = this.noise ? this.noise * 0.97 + rms * 0.03 : rms; // 주변 소음 크기를 천천히 따라간다
      }
      return;
    }

    this.chunks.push(chunk);
    if (rms > Math.max(this.o.threshold, this.noise * 2)) this.lastVoice = now;
    if (now - this.lastVoice > this.o.silenceMs || now - this.speechStart > this.o.maxSeconds * 1000) this._finish(rate);
  }

  _finish(rate) {
    const chunks = this.chunks;
    this._reset();
    const { blob, seconds, pcm } = encodeWav(chunks, rate);
    this._state('listening');
    if (seconds * 1000 < this.o.minUtteranceMs) return;   // 너무 짧은 소리(기침/클릭음)는 버린다
    console.info(`[mic] 발화 ${seconds.toFixed(1)}s ${(blob.size / 1024).toFixed(0)}KB`);
    try { this.h.onUtterance && this.h.onUtterance(blob, { seconds, pcm }); } catch (e) { console.warn(e); }
  }

  // ---------- Silero VAD 방식 ----------
  // 들어온 소리를 16kHz 로 맞추고 512 샘플(32ms)씩 잘라 VAD 에 넣는다. 확률은 비동기로 오지만 순서대로 처리한다.
  _vadFeed(chunk, rate) {
    let s16 = chunk;
    if (rate !== TARGET_RATE) {
      if (!this.resampler) this.resampler = new Resampler(rate, TARGET_RATE);
      s16 = this.resampler.push(chunk);
    }
    const buf = new Float32Array(this.pending.length + s16.length);
    buf.set(this.pending, 0); buf.set(s16, this.pending.length);
    let off = 0;
    while (buf.length - off >= VAD_FRAME) {
      const frame = buf.slice(off, off + VAD_FRAME); off += VAD_FRAME;
      const gen = this.gen;
      this._vadQueue = this._vadQueue.then(() => this._vadStep(frame, gen));
    }
    this.pending = buf.slice(off);
  }

  async _vadStep(frame, gen) {
    if (gen !== this.gen || !this.active) return;
    let p;
    try { p = await this.h.vad.frame(frame); } catch (e) {
      if (gen === this.gen && this.vadMode) this._fallback((e && e.message) || String(e));   // 이후 소리는 음량 방식으로
      return;
    }
    if (gen !== this.gen || !this.active) return;   // 기다리는 사이 꺼졌거나 초기화됨

    const th = this.o.vadThreshold;
    const neg = this.o.vadNegThreshold != null ? this.o.vadNegThreshold : Math.max(0.01, th - 0.15);

    if (!this.speaking) {
      this.preRoll.push(frame); this.preRollMs += VAD_FRAME_MS;
      while (this.preRollMs > this.o.preRollMs && this.preRoll.length > 1) { this.preRoll.shift(); this.preRollMs -= VAD_FRAME_MS; }
      if (p >= th) {
        this.voicedMs += VAD_FRAME_MS;
        if (this.voicedMs >= this.o.vadMinSpeechMs) {
          this.speaking = true;
          this.chunks = this.preRoll; this.preRoll = []; this.preRollMs = 0;
          this.speechMs = this.chunks.length * VAD_FRAME_MS; this.silentMs = 0;
          this.voiceStartMs = Math.max(0, this.speechMs - this.voicedMs); this.voiceEndMs = this.speechMs;   // 말소리가 실제로 있던 구간 (앞뒤 여유 제외)
          this._state('speaking');
        }
      } else if (p < neg) {
        this.voicedMs = 0;
      }
      return;
    }

    this.chunks.push(frame); this.speechMs += VAD_FRAME_MS;
    if (p >= th) { this.silentMs = 0; this.voiceEndMs = this.speechMs; }
    else if (p < neg) this.silentMs += VAD_FRAME_MS;
    if (this.silentMs >= this.o.vadSilenceMs || this.speechMs >= this.o.maxSeconds * 1000) this._finishVad();
  }

  _finishVad() {
    const span = this.voiceEndMs - this.voiceStartMs;
    if (span < this.o.vadMinUtteranceMs) {   // 너무 짧은 말(기침/노랫소리 조각 등)은 버린다
      this._reset(true); this._state('listening');
      console.info(`[mic] 너무 짧은 말(${Math.round(span)}ms)은 무시`);
      return;
    }
    // 끝의 조용한 구간은 0.3초만 남기고 잘라낸다 (인식 서버로 보낼 양과 화자 확인 대상이 줄어든다)
    const drop = Math.max(0, Math.floor((this.silentMs - 320) / VAD_FRAME_MS));
    const chunks = drop > 0 ? this.chunks.slice(0, Math.max(1, this.chunks.length - drop)) : this.chunks;
    this._finish16(chunks);
  }

  _finish16(chunks) {
    this._reset(true);
    const { blob, seconds, pcm } = encodeWav(chunks, TARGET_RATE);
    this._state('listening');
    if (seconds * 1000 < this.o.minUtteranceMs) return;
    console.info(`[mic] 발화 ${seconds.toFixed(1)}s ${(blob.size / 1024).toFixed(0)}KB (Silero)`);
    try { this.h.onUtterance && this.h.onUtterance(blob, { seconds, pcm }); } catch (e) { console.warn(e); }
  }
}
