// 화자 인증: 등록해 둔 내 목소리와 지금 들린 말이 같은 사람인지 확인한다. 메인 프로세스에서 onnxruntime-node 로 실행.
//
//   const sp = new SpeakerVerifier({ ort, modelPath, storePath, threshold: 0.5, minSeconds: 0.5 });
//   await sp.init();                         // 모델 + 등록된 목소리 불러오기 (실패해도 throw 하지 않고 available=false)
//   await sp.enroll([pcm1, pcm2, ...]);     // 16kHz 모노 Float32Array 여러 개 -> 샘플별 임베딩을 모두 저장 (+ 본인 점수 통계)
//   const r = await sp.verify(pcm);          // { ok, score, skipped }
//
// 화자 인증은 "있으면 쓰고 없으면 건너뛰는" 선택 기능이다:
//   모델이 없거나 / 목소리를 등록하지 않았으면 ok:true, skipped:'이유' 로 그냥 통과시킨다.
//   minSeconds(기본 0.5초)보다 짧은 발화는 shortPolicy 대로: 'reject'(기본)면 차단, 'pass'면 확인 없이 통과.
//   점수 = 등록해 둔 샘플들 중 가장 비슷한 몇 개(상위 k개)와의 코사인 유사도 평균. 등록 문장이 다양할수록 내 목소리가 잘 맞는다.
// 모델: WeSpeaker ResNet34 처럼 입력 [1, 프레임, 80](fbank) -> 출력 [1, 차원](임베딩) 인 ONNX 모델.

const fs = require('fs');
const path = require('path');
const { fbank } = require('./fbank');

const TOP_K_MAX = 3;      // 비교할 샘플 수 상한 (등록 샘플이 많을수록 상위 몇 개만 본다)
const MAX_SECONDS = 10;   // 임베딩에는 앞부분 최대 10초만 쓴다 (길어도 정확도는 비슷하고 계산만 늘어난다)

function l2norm(v) {
  let s = 0; for (let i = 0; i < v.length; i++) s += v[i] * v[i];
  const n = Math.sqrt(s) || 1;
  const out = new Float32Array(v.length);
  for (let i = 0; i < v.length; i++) out[i] = v[i] / n;
  return out;
}
function cosine(a, b) {
  const n = Math.min(a.length, b.length);
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < n; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return dot / (Math.sqrt(na) * Math.sqrt(nb) || 1);
}

function centroid(embs) {
  const mean = new Float32Array(embs[0].length);
  for (const e of embs) for (let i = 0; i < mean.length; i++) mean[i] += e[i] / embs.length;
  return l2norm(mean);
}
// 샘플 개수에 따라 상위 1~3개만 본다 (샘플 3개 이하면 가장 비슷한 1개, 12개면 상위 3개)
function topK(n) { return Math.max(1, Math.min(TOP_K_MAX, Math.round(n / 4))); }
function scoreAgainst(emb, embs) {
  const sims = embs.map((e) => cosine(emb, e)).sort((a, b) => b - a);
  const k = topK(sims.length);
  let s = 0; for (let i = 0; i < k; i++) s += sims[i];
  return s / k;
}
const r2 = (x) => Math.round(x * 100) / 100;

class SpeakerVerifier {
  constructor({ ort, modelPath, storePath, threshold = 0.5, minSeconds = 0.5, shortPolicy = 'reject', threads = 1 } = {}) {
    this.ort = ort; this.modelPath = modelPath; this.storePath = storePath;
    this.threshold = threshold; this.minSeconds = minSeconds; this.shortPolicy = shortPolicy; this.threads = threads;
    this.session = null; this.profile = null; this.embs = []; this.reason = '';
    this._chain = Promise.resolve();
  }

  get available() { return !!this.session; }
  get enrolled() { return !!this.profile; }

  async init() {
    this._loadProfile();
    this.session = null;
    try {
      if (!this.ort) throw new Error('onnxruntime-node 가 설치되어 있지 않아요');
      if (!this.modelPath || !fs.existsSync(this.modelPath)) throw new Error(`화자 모델 파일이 없어요 (${this.modelPath || '경로 없음'})`);
      this.session = await this.ort.InferenceSession.create(this.modelPath, {
        executionProviders: ['cpu'], intraOpNumThreads: this.threads, interOpNumThreads: 1,
      });
      this.reason = '';
    } catch (e) {
      this.reason = (e && e.message) || String(e);
    }
    return this.available;
  }

  _loadProfile() {
    this.profile = null; this.embs = [];
    try {
      const j = JSON.parse(fs.readFileSync(this.storePath, 'utf8'));
      let list = [];
      if (Array.isArray(j.embeddings)) list = j.embeddings.filter((e) => Array.isArray(e) && e.length > 0).map((e) => Float32Array.from(e));   // 버전 2: 샘플별 임베딩
      else if (Array.isArray(j.embedding) && j.embedding.length > 0) list = [Float32Array.from(j.embedding)];                                      // 버전 1: 평균 하나
      list = list.filter((e) => list.length && e.length === list[0].length);
      if (list.length) { this.embs = list; this.profile = centroid(list); }
    } catch { /* 등록 안 됨 */ }
  }

  // 모델 실행은 하나씩 순서대로
  _serial(fn) {
    const run = this._chain.then(fn);
    this._chain = run.catch(() => {});
    return run;
  }

  embed(pcm) {
    return this._serial(async () => {
      if (!this.session) throw new Error(this.reason || '화자 모델이 준비되지 않았어요');
      const src = pcm.length > MAX_SECONDS * 16000 ? pcm.subarray(0, MAX_SECONDS * 16000) : pcm;
      const { feats, frames, bins } = fbank(src);
      if (frames < 10) throw new Error('소리가 너무 짧아요');
      const { Tensor } = this.ort;
      const inName = this.session.inputNames[0];
      const out = await this.session.run({ [inName]: new Tensor('float32', feats, [1, frames, bins]) });
      const outName = this.session.outputNames[0];
      return l2norm(Float32Array.from(out[outName].data));
    });
  }

  async enroll(pcms) {
    if (!this.available) throw new Error(this.reason || '화자 모델이 준비되지 않았어요');
    const embs = [];
    for (const p of pcms || []) embs.push(await this.embed(p));
    if (!embs.length) throw new Error('등록할 음성이 없어요');
    this.embs = embs; this.profile = centroid(embs);
    fs.mkdirSync(path.dirname(this.storePath), { recursive: true });
    // embedding(평균)은 예전 버전과의 호환용, 실제 비교에는 embeddings(샘플별)를 쓴다
    fs.writeFileSync(this.storePath, JSON.stringify({ version: 2, samples: embs.length, embedding: Array.from(this.profile), embeddings: embs.map((e) => Array.from(e)) }), 'utf8');
    // 본인 점수 확인용: 샘플 하나씩 빼고 나머지와 비교 (검증 때와 같은 방식). 임계값을 이 최저값보다 낮게 두면 내 말이 안 막힌다.
    let selfMean = null, selfMin = null;
    if (embs.length >= 3) {
      const scores = embs.map((e, i) => scoreAgainst(e, embs.filter((_, j) => j !== i)));
      selfMean = r2(scores.reduce((a, b) => a + b, 0) / scores.length);
      selfMin = r2(Math.min(...scores));
    }
    return { samples: embs.length, selfMean, selfMin };
  }

  clear() {
    this.profile = null;
    try { fs.unlinkSync(this.storePath); } catch { /* 없으면 그만 */ }
  }

  async verify(pcm) {
    if (!this.available) return { ok: true, skipped: 'unavailable' };
    if (!this.profile) return { ok: true, skipped: 'not-enrolled' };
    const seconds = pcm.length / 16000;
    if (seconds < this.minSeconds) return { ok: this.shortPolicy !== 'reject', skipped: 'too-short', seconds };
    try {
      const score = scoreAgainst(await this.embed(pcm), this.embs);
      return { ok: score >= this.threshold, score, seconds };
    } catch (e) {
      return { ok: true, skipped: 'error', error: (e && e.message) || String(e) };   // 확인에 실패하면 막지 않고 통과
    }
  }
}

module.exports = { SpeakerVerifier, cosine, l2norm, scoreAgainst };
