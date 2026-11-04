// 에코 제거기 (AEC): 스피커로 나온 소리(기준 신호)가 마이크에 섞여 들어온 만큼을 빼낸다.
// WebRTC 의 AEC3 (@ennuicastr/webrtcaec3.js, WebAssembly) 를 감싼 얇은 래퍼.
//
//   const aec = await EchoCanceller.create({}, 16000);   // 두 번째 인자: 넘겨줄 소리의 샘플레이트 (mic.js 는 AudioContext 속도를 넘긴다)
//   const clean = aec.process(micChunk, refChunk);       // 둘 다 Float32Array, 같은 길이, 시간이 맞춰진 상태. 같은 길이를 돌려준다
//   aec.free();                                          // 다 쓰면 반드시 (WASM 메모리 해제)
//
// AEC3 는 10ms(= 샘플레이트/100 샘플) 단위로 "기준 소리 넣기 -> 마이크 처리"를 번갈아 해야 지연 추정이 안정된다.
// mic.js 는 2048 샘플(16kHz 에서 128ms)씩 넘겨 주므로, 여기서 직접 10ms 로 잘라 한 프레임씩 번갈아 넣는다 (analyze -> process -> analyze -> ...).
// 남는 샘플은 다음 호출로 넘기고, 출력은 항상 입력과 같은 길이로 맞춘다. 그래서 출력은 입력보다 10ms 늦다.
// AEC3 인스턴스는 32k/48k 만 받는다 (기본 48k). 마이크/기준 소리가 다른 속도여도 라이브러리가 알아서 변환해 주므로 mic.js 는 16kHz 그대로 쓴다.
//
// 지연/울림 추정, 필터 길이, 잔여 에코 억제는 모두 AEC3 가 알아서 한다. 바깥에서 조절할 수 있는 값은 아래 DEFAULTS 뿐이다.

const PKG_DIR = '../node_modules/@ennuicastr/webrtcaec3.js/dist/webrtcaec3-0.3.0.js';   // package.json 에서 0.3.0 으로 고정 (파일명에 버전이 들어 있다)

const DEFAULTS = {
  rate: 48000,        // AEC3 내부 처리 속도. 32000 또는 48000 만 가능 (32000 이 CPU 를 덜 쓴다)
};

let modPromise = null;

// AEC3 WASM 모듈을 한 번만 불러온다. 렌더러에서는 <script> 로, Node(시험)에서는 require 로.
// 실패하면 다음에 다시 시도할 수 있게 캐시를 비운다.
export function loadModule() {
  if (modPromise) return modPromise;
  modPromise = (async () => {
    if (typeof document !== 'undefined') {
      if (typeof globalThis.WebRtcAec3 !== 'function') {
        await new Promise((resolve, reject) => {
          const s = document.createElement('script');
          s.src = new URL(PKG_DIR, import.meta.url).href;
          s.onload = resolve;
          s.onerror = () => reject(new Error('AEC3 스크립트를 불러오지 못했어요 (npm install 을 했는지 확인하세요)'));
          document.head.appendChild(s);
        });
      }
      if (typeof globalThis.WebRtcAec3 !== 'function') throw new Error('AEC3 모듈을 찾을 수 없어요');
      return globalThis.WebRtcAec3();
    }
    const { createRequire } = await import('node:module');
    const { fileURLToPath } = await import('node:url');
    return createRequire(import.meta.url)(fileURLToPath(new URL(PKG_DIR, import.meta.url)))();   // 경로에 공백/한글이 있어도 안전하게
  })();
  modPromise.catch(() => { modPromise = null; });
  return modPromise;
}

function cat(first, ...rest) {
  let n = first.length; for (const x of rest) n += x.length;
  const out = new Float32Array(n); out.set(first, 0);
  let o = first.length; for (const x of rest) { out.set(x, o); o += x.length; }
  return out;
}

export class EchoCanceller {
  // 모듈 로딩이 비동기라서 생성자 대신 이걸 쓴다.
  static async create(opts = {}, sampleRate = 16000, loader = loadModule) {
    const mod = await loader();
    return new EchoCanceller(mod, opts, sampleRate);
  }

  constructor(mod, opts = {}, sampleRate = 16000) {
    this.mod = mod;
    this.o = { ...DEFAULTS, ...opts };
    this.fs = sampleRate;                                 // 넘겨받는 소리의 속도
    this.frame = Math.floor(sampleRate / 100);            // AEC3 가 한 번에 처리하는 단위 (10ms)
    this.io = { sampleRateIn: sampleRate, sampleRateOut: sampleRate };
    this._make();
  }

  _make() {
    this.aec = new this.mod.AEC3(this.o.rate, 1, 1);
    this.pm = new Float32Array(0); this.pr = new Float32Array(0);   // 아직 10ms 가 안 찬 마이크/기준 소리 (다음 호출로 넘김)
    this.q = new Float32Array(this.frame);                // 출력 대기열. 처음에 10ms 만큼 0 을 깔아 두면 이후 항상 입력 길이만큼 돌려줄 수 있다
  }

  reset() { this._free(); this._make(); }

  _free() { if (this.aec) { try { this.aec.free(); } catch { /* ignore */ } this.aec = null; } }

  free() { this._free(); this.q = new Float32Array(0); }

  process(mic, ref) {
    const len = mic.length;
    if (!len) return new Float32Array(0);
    if (!this.aec) return Float32Array.from(mic);
    // NaN/무한대가 한 번 섞이면 내부 필터가 영원히 망가지므로 막는다. 기준이 짧으면 0 으로 채운다.
    const m = new Float32Array(len), r = new Float32Array(len);
    for (let i = 0; i < len; i++) {
      const a = mic[i], b = i < ref.length ? ref[i] : 0;
      m[i] = Number.isFinite(a) ? a : 0;
      r[i] = Number.isFinite(b) ? b : 0;
    }
    // 지난번에 남은 것과 이어 붙여서 10ms 프레임으로 자른다
    const F = this.frame;
    this.pm = cat(this.pm, m); this.pr = cat(this.pr, r);
    const frames = Math.floor(this.pm.length / F);
    const outs = [];
    try {
      for (let k = 0; k < frames; k++) {
        const mf = this.pm.slice(k * F, (k + 1) * F), rf = this.pr.slice(k * F, (k + 1) * F);   // slice: WASM 으로 넘길 때 버퍼 오프셋 문제가 없게 복사본
        this.aec.analyze([rf], this.io);                  // 이 10ms 의 기준(스피커) 소리를 먼저 넣고
        const n = this.aec.processSize([mf], this.io);
        const out = [new Float32Array(n)];
        this.aec.process(out, [mf], this.io);             // 같은 10ms 의 마이크 소리에서 에코를 뺀다
        outs.push(out[0]);
      }
    } catch (e) {
      console.warn('[aec3] 처리 오류 -> 필터를 처음부터 다시 시작합니다', e);
      try { this.reset(); } catch { /* ignore */ }
      return m;                                           // 이번 소리는 그대로 통과
    }
    this.pm = this.pm.slice(frames * F); this.pr = this.pr.slice(frames * F);
    const got = cat(new Float32Array(0), ...outs);
    for (let i = 0; i < got.length; i++) if (!Number.isFinite(got[i])) { this.reset(); return m; }

    // 대기열에 이어 붙이고 앞에서 len 개를 꺼낸다
    const all = new Float32Array(this.q.length + got.length);
    all.set(this.q, 0); all.set(got, this.q.length);
    const res = new Float32Array(len); res.set(all.subarray(0, Math.min(len, all.length)));   // 모자라면 뒤를 0 으로 (길이 보장)
    this.q = all.length > len ? all.slice(len) : new Float32Array(0);
    return res;
  }
}
