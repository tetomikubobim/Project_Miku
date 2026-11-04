// Silero VAD (ONNX) — "지금 소리가 사람 말소리일 확률"을 프레임마다 돌려준다. 메인 프로세스에서 onnxruntime-node 로 실행.
//
//   const vad = new SileroVad({ ort, modelPath });
//   await vad.init();                 // 모델 로드 (실패하면 throw)
//   vad.reset();                      // 새 녹음 세션을 시작할 때 (내부 상태 초기화)
//   const p = await vad.process(f32); // f32: 16kHz 모노 512 샘플 -> 0~1
//
// 지원: silero_vad.onnx v5 / v6 (입력 input·state·sr, 출력 output·stateN).
//       v5 는 직전 64 샘플을 앞에 붙여서 넣어야 해서 여기서 처리한다.
// process() 는 호출 순서대로 하나씩 실행된다 (LSTM 상태가 순서에 의존).

const SAMPLE_RATE = 16000;
const WINDOW = 512;
const CONTEXT = 64;
const STATE_SIZE = 2 * 1 * 128;

class SileroVad {
  constructor({ ort, modelPath, threads = 1 } = {}) {
    this.ort = ort;
    this.modelPath = modelPath;
    this.threads = threads;
    this.session = null;
    this._chain = Promise.resolve();
    this.reset();
  }

  async init() {
    if (!this.ort) throw new Error('onnxruntime-node 가 설치되어 있지 않아요 (npm install)');
    if (!this.modelPath) throw new Error('VAD 모델 경로가 비어 있어요');
    this.session = await this.ort.InferenceSession.create(this.modelPath, {
      executionProviders: ['cpu'],
      intraOpNumThreads: this.threads,
      interOpNumThreads: 1,
      graphOptimizationLevel: 'all',
    });
    this.reset();
  }

  get ready() { return !!this.session; }

  reset() {
    this.state = new Float32Array(STATE_SIZE);
    this.context = new Float32Array(CONTEXT);
  }

  // 순서 보장을 위해 이전 호출이 끝난 뒤에 실행한다
  process(frame) {
    const run = this._chain.then(() => this._run(frame));
    this._chain = run.catch(() => {});
    return run;
  }

  async _run(frame) {
    if (!this.session) throw new Error('VAD 가 아직 준비되지 않았어요');
    if (!frame || frame.length !== WINDOW) throw new Error(`VAD 프레임은 ${WINDOW} 샘플이어야 해요 (받은 값: ${frame ? frame.length : 0})`);
    const { Tensor } = this.ort;
    const input = new Float32Array(CONTEXT + WINDOW);
    input.set(this.context, 0);
    input.set(frame, CONTEXT);
    const out = await this.session.run({
      input: new Tensor('float32', input, [1, CONTEXT + WINDOW]),
      state: new Tensor('float32', this.state, [2, 1, 128]),
      sr: new Tensor('int64', BigInt64Array.from([BigInt(SAMPLE_RATE)]), []),
    });
    this.state = Float32Array.from(out.stateN.data);
    this.context = Float32Array.from(input.subarray(input.length - CONTEXT));
    return Number(out.output.data[0]);
  }
}

module.exports = { SileroVad, SAMPLE_RATE, WINDOW, CONTEXT };
