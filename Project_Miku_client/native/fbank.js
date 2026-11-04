// Kaldi 호환 log-mel filterbank (WeSpeaker / 3D-Speaker 계열 화자 모델의 입력 특징).
//
//   const { feats, frames, bins } = fbank(pcm16k /* Float32Array, -1~1 */);
//   feats: frames x bins (행 우선, 프레임별 평균을 뺀 값 = CMN 적용)
//
// 설정은 WeSpeaker 기본값과 같다: 16kHz, 창 25ms / 이동 10ms, hamming, 프리엠퍼시스 0.97, 80 mel (20Hz~나이퀴스트),
// dither 없음, 샘플은 int16 범위(x32768)로 키운다.

const SAMPLE_RATE = 16000;
const FRAME_LEN = 400;       // 25ms
const FRAME_SHIFT = 160;     // 10ms
const FFT_SIZE = 512;
const PREEMPH = 0.97;
const LOW_FREQ = 20;
const EPS = 1.1920929e-07;   // float32 epsilon (Kaldi 가 log 앞에서 쓰는 하한)

const melOf = (f) => 1127 * Math.log(1 + f / 700);

// ---- 512 점 복소 FFT (radix-2) ----
const COS = new Float64Array(FFT_SIZE / 2);
const SIN = new Float64Array(FFT_SIZE / 2);
for (let i = 0; i < FFT_SIZE / 2; i++) { COS[i] = Math.cos((2 * Math.PI * i) / FFT_SIZE); SIN[i] = -Math.sin((2 * Math.PI * i) / FFT_SIZE); }
const REV = new Uint16Array(FFT_SIZE);
for (let i = 0, bits = Math.log2(FFT_SIZE); i < FFT_SIZE; i++) {
  let r = 0; for (let b = 0; b < bits; b++) if (i & (1 << b)) r |= 1 << (bits - 1 - b);
  REV[i] = r;
}
function fft(re, im) {
  for (let i = 0; i < FFT_SIZE; i++) {
    const j = REV[i];
    if (j > i) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
  }
  for (let size = 2; size <= FFT_SIZE; size <<= 1) {
    const half = size >> 1, step = FFT_SIZE / size;
    for (let start = 0; start < FFT_SIZE; start += size) {
      for (let k = 0, w = 0; k < half; k++, w += step) {
        const a = start + k, b = a + half;
        const tr = re[b] * COS[w] - im[b] * SIN[w];
        const ti = re[b] * SIN[w] + im[b] * COS[w];
        re[b] = re[a] - tr; im[b] = im[a] - ti;
        re[a] += tr; im[a] += ti;
      }
    }
  }
}

// ---- 창 / mel 필터 (한 번만 계산) ----
const WINDOW = new Float64Array(FRAME_LEN);
for (let i = 0; i < FRAME_LEN; i++) WINDOW[i] = 0.54 - 0.46 * Math.cos((2 * Math.PI * i) / (FRAME_LEN - 1));

const melBanks = new Map();
function getBanks(numBins) {
  if (melBanks.has(numBins)) return melBanks.get(numBins);
  const nyquist = SAMPLE_RATE / 2;
  const fftBins = FFT_SIZE / 2;                  // 0..255 (나이퀴스트 bin 은 쓰지 않는다: Kaldi 와 동일)
  const binWidth = SAMPLE_RATE / FFT_SIZE;
  const melLow = melOf(LOW_FREQ), melHigh = melOf(nyquist);
  const delta = (melHigh - melLow) / (numBins + 1);
  const banks = [];
  for (let b = 0; b < numBins; b++) {
    const left = melLow + b * delta, center = melLow + (b + 1) * delta, right = melLow + (b + 2) * delta;
    const idx = [], w = [];
    for (let j = 0; j < fftBins; j++) {
      const mel = melOf(binWidth * j);
      if (mel > left && mel < right) { idx.push(j); w.push(mel <= center ? (mel - left) / (center - left) : (right - mel) / (right - center)); }
    }
    banks.push({ idx: Uint16Array.from(idx), w: Float64Array.from(w) });
  }
  melBanks.set(numBins, banks);
  return banks;
}

function fbank(pcm, { numBins = 80, cmn = true } = {}) {
  const n = pcm ? pcm.length : 0;
  if (n < FRAME_LEN) return { feats: new Float32Array(0), frames: 0, bins: numBins };
  const frames = 1 + Math.floor((n - FRAME_LEN) / FRAME_SHIFT);
  const banks = getBanks(numBins);
  const feats = new Float32Array(frames * numBins);
  const re = new Float64Array(FFT_SIZE), im = new Float64Array(FFT_SIZE), x = new Float64Array(FRAME_LEN), pow = new Float64Array(FFT_SIZE / 2);

  for (let f = 0; f < frames; f++) {
    const off = f * FRAME_SHIFT;
    let mean = 0;
    for (let i = 0; i < FRAME_LEN; i++) { x[i] = pcm[off + i] * 32768; mean += x[i]; }
    mean /= FRAME_LEN;
    for (let i = 0; i < FRAME_LEN; i++) x[i] -= mean;
    for (let i = FRAME_LEN - 1; i > 0; i--) x[i] -= PREEMPH * x[i - 1];
    x[0] -= PREEMPH * x[0];
    re.fill(0); im.fill(0);
    for (let i = 0; i < FRAME_LEN; i++) re[i] = x[i] * WINDOW[i];
    fft(re, im);
    for (let k = 0; k < FFT_SIZE / 2; k++) pow[k] = re[k] * re[k] + im[k] * im[k];
    for (let b = 0; b < numBins; b++) {
      const { idx, w } = banks[b];
      let e = 0;
      for (let t = 0; t < idx.length; t++) e += pow[idx[t]] * w[t];
      feats[f * numBins + b] = Math.log(Math.max(e, EPS));
    }
  }

  if (cmn) {
    for (let b = 0; b < numBins; b++) {
      let m = 0;
      for (let f = 0; f < frames; f++) m += feats[f * numBins + b];
      m /= frames;
      for (let f = 0; f < frames; f++) feats[f * numBins + b] -= m;
    }
  }
  return { feats, frames, bins: numBins };
}

module.exports = { fbank, SAMPLE_RATE, FRAME_LEN, FRAME_SHIFT };
