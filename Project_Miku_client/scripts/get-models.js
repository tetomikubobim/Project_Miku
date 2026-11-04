// 음성 모델 내려받기: npm run get-models
//   voice-models/silero_vad.onnx  (Silero VAD, 약 2MB)        — 말 구간 감지
//   voice-models/speaker.onnx     (WeSpeaker ResNet34, 약 25MB) — 화자 인증 (선택 기능. 안 쓰면 안 받아도 된다)
//
//   npm run get-models              둘 다 (이미 있으면 건너뜀)
//   npm run get-models -- vad       VAD 만
//   npm run get-models -- speaker   화자 모델만
//   npm run get-models -- --force   있어도 다시 받기
//
// 주소를 바꾸려면 환경 변수 MIKU_VAD_URL / MIKU_SPEAKER_URL 을 쓰거나, 받은 파일을 voice-models 폴더에 직접 넣으면 된다.

const fs = require('fs');
const path = require('path');
const { Readable } = require('stream');
const { pipeline } = require('stream/promises');

const DIR = path.join(__dirname, '..', 'voice-models');
const TARGETS = {
  vad: {
    file: 'silero_vad.onnx',
    url: process.env.MIKU_VAD_URL || 'https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx',
    minBytes: 500 * 1024,
    label: 'Silero VAD',
  },
  speaker: {
    file: 'speaker.onnx',
    url: process.env.MIKU_SPEAKER_URL || 'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx',
    minBytes: 1024 * 1024,
    label: '화자 인증 모델 (WeSpeaker ResNet34, VoxCeleb)',
  },
};

async function download(t, force) {
  const dest = path.join(DIR, t.file);
  if (!force && fs.existsSync(dest) && fs.statSync(dest).size >= t.minBytes) { console.log(`- ${t.label}: 이미 있어요 (${t.file})`); return true; }
  fs.mkdirSync(DIR, { recursive: true });
  console.log(`- ${t.label}: 내려받는 중…\n  ${t.url}`);
  const tmp = `${dest}.part`;
  try {
    const res = await fetch(t.url, { redirect: 'follow' });
    if (!res.ok) throw new Error(`서버 응답 ${res.status}`);
    await pipeline(Readable.fromWeb(res.body), fs.createWriteStream(tmp));
    const size = fs.statSync(tmp).size;
    if (size < t.minBytes) throw new Error(`파일이 너무 작아요 (${size} 바이트) — 받은 것이 모델 파일이 아닐 수 있어요`);
    fs.renameSync(tmp, dest);
    console.log(`  완료: ${(size / 1024 / 1024).toFixed(1)}MB -> voice-models/${t.file}`);
    return true;
  } catch (e) {
    try { fs.unlinkSync(tmp); } catch { /* 없으면 그만 */ }
    const why = (e && e.cause && (e.cause.code || e.cause.message)) || (e && e.message) || e;
    console.log(`  실패: ${why}\n  직접 받아서 voice-models/${t.file} 로 넣어 주세요: ${t.url}`);
    return false;
  }
}

(async () => {
  const args = process.argv.slice(2);
  const force = args.includes('--force');
  const names = args.filter((a) => !a.startsWith('--'));
  const pick = names.length ? names : Object.keys(TARGETS);
  const bad = pick.filter((n) => !TARGETS[n]);
  if (bad.length) { console.log(`알 수 없는 항목: ${bad.join(', ')} (vad, speaker)`); process.exit(2); }
  let ok = true;
  for (const n of pick) ok = (await download(TARGETS[n], force)) && ok;
  process.exit(ok ? 0 : 1);
})();
