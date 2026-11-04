// 모델별 프로필: models/<모델>/persona.txt (캐릭터 설정) + models/<모델>/emotionAliases.json (감정 태그 -> 표정)
//              + models/<모델>/voice.json (GPT-SoVITS 가중치/참조 음성/속도 speed_factor. 경로는 서버 PC 기준. 없는 항목은 서버 설정을 씀).
//              + models/<모델>/lyricsPose.json ({ "expression": 표정 이름, "motion": 모션 이름 } 가사창이 떠 있는 동안만 쓰는 표정/모션. 대사의 감정으로는 쓰지 않는다).
// 파일을 읽고 쓰는 순수 함수만 둔다 (main.js 가 IPC 로 부른다).
//
// emotionAliases.json 형식 (값은 표정 파일 이름 또는 {expression, desc}):
//   { "neutral": "default.exp3.json",
//     "happy":   { "expression": "Fanged Smile.exp3.json", "desc": "기쁨, 즐거움" } }
// 키(태그)는 서버가 LLM 에게 알려 주는 감정 목록이 된다. desc 는 LLM 에게 알려 줄 설명(생략 가능).
const fs = require('fs');
const path = require('path');

const PERSONA_FILE = 'persona.txt';
const ALIAS_FILE = 'emotionAliases.json';
const VOICE_FILE = 'voice.json';
const LYRICS_POSE_FILE = 'lyricsPose.json';
const POSE_KEYS = ['expression', 'motion'];
const LYRICS_ITEM_FILE = 'lyricsItem.json';   // { x, y, scale }: 가사창이 뜰 때 모델에 핀 고정으로 같이 뜨는 그림(마이크 스탠드)의 위치/크기
const ITEM_IMAGE_NAMES = ['lyricsItem.png', 'lyricsItem.webp', 'lyricsItem.jpg', 'lyricsItem.jpeg', 'lyricsItem.gif'];   // 모델 폴더에 넣으면 기본 그림 대신 쓴다
const ITEM_DEFAULT = { x: 0, y: -0.74, scale: 0.8 };   // renderer/lyrics-item.js 와 같은 값
const ITEM_LIMITS = { x: [-1.5, 1.5], y: [-2, 1], scale: [0.05, 3] };
const MAX_POSE = 200;
const VOICE_KEYS = ['gpt_weights_path', 'sovits_weights_path', 'ref_audio_path', 'prompt_text', 'prompt_lang', 'speed_factor'];   // 서버 config.yaml 의 tts 키와 같은 이름
const SPEED_MIN = 0.5;
const SPEED_MAX = 2.0;   // speed_factor: 말하는 속도 (1.0 = 보통). 화면/메모리에서는 글자("1.2")로 다루고, voice.json 에는 숫자로 쓴다

// 속도 값(숫자 또는 글자) -> 글자("1.2"). 비었으면 ''. 숫자가 아니거나 범위 밖이면 null
function speedText(v) {
  if (v === null || v === undefined) return '';
  const t = String(v).trim();
  if (!t) return '';
  const n = Number(t);
  if (!Number.isFinite(n) || n < SPEED_MIN || n > SPEED_MAX) return null;
  return String(Math.round(n * 100) / 100);
}
const LANG_RE = /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$/;
const TAG_RE = /^[A-Za-z가-힣]{1,20}$/;   // 서버가 [태그] 로 알아보는 글자만
const MAX_TAGS = 24;
const MAX_PERSONA = 20000;
const MAX_VOICE = 500;

const clean = (s) => String(s ?? '').replace(/^\uFEFF/, '');

// 파일 내용(객체) -> [{tag, expression, desc}] (잘못된 항목은 버린다)
function parseAliases(obj) {
  const rows = [];
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return rows;
  for (const [k, v] of Object.entries(obj)) {
    const tag = String(k).trim().toLowerCase();
    if (!TAG_RE.test(tag) || rows.some((r) => r.tag === tag) || rows.length >= MAX_TAGS) continue;
    let expression = '', desc = '';
    if (typeof v === 'string') expression = v;
    else if (v && typeof v === 'object') { expression = typeof v.expression === 'string' ? v.expression : ''; desc = typeof v.desc === 'string' ? v.desc : ''; }
    else continue;
    expression = expression.trim();
    if (!expression) continue;
    rows.push({ tag, expression, desc: desc.replace(/\s+/g, ' ').trim().slice(0, 60) });
  }
  return rows;
}

// voice.json 내용 -> { 다섯 키: 문자열 } (빈 칸은 ''). 모르는 키/잘못된 언어 코드는 버린다
function parseVoice(obj) {
  const out = Object.fromEntries(VOICE_KEYS.map((k) => [k, '']));
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return out;
  for (const k of VOICE_KEYS) {
    if (k === 'speed_factor') { out[k] = speedText(obj[k]) || ''; continue; }   // 범위 밖/숫자 아님은 버린다
    const v = typeof obj[k] === 'string' ? obj[k].replace(/\s+/g, ' ').trim().slice(0, MAX_VOICE) : '';
    if (v && (k !== 'prompt_lang' || LANG_RE.test(v))) out[k] = v;
  }
  return out;
}

// 화면에서 온 음성 값을 검사한다. 잘못되면 Error(사용자에게 보여 줄 메시지)
function validateVoice(voice) {
  if (!voice || typeof voice !== 'object' || Array.isArray(voice)) throw new Error('음성 설정이 올바르지 않아요');
  const out = Object.fromEntries(VOICE_KEYS.map((k) => [k, '']));
  for (const k of VOICE_KEYS) {
    if (k === 'speed_factor') {
      const t = speedText(voice[k]);
      if (t === null) throw new Error(`음성 속도는 ${SPEED_MIN} ~ ${SPEED_MAX} 사이 숫자로 적어 주세요 (1.0 = 보통)`);
      out[k] = t;
      continue;
    }
    const v = String(voice[k] ?? '').replace(/\s+/g, ' ').trim();
    if (v.length > MAX_VOICE) throw new Error(`음성 설정이 너무 길어요 (최대 ${MAX_VOICE}자)`);
    if (v && k === 'prompt_lang' && !LANG_RE.test(v)) throw new Error('참조 음성 언어는 ko / ja / en / zh 같은 언어 코드로 적어 주세요');
    out[k] = v;
  }
  return out;
}

// 화면에서 온 행을 검사한다. 잘못되면 Error(사용자에게 보여 줄 메시지)
function validateRows(rows) {
  if (!Array.isArray(rows)) throw new Error('감정 목록이 올바르지 않아요');
  if (rows.length > MAX_TAGS) throw new Error(`감정은 최대 ${MAX_TAGS}개까지예요`);
  const seen = new Set(), out = [];
  for (const r of rows) {
    const tag = String((r && r.tag) ?? '').trim().toLowerCase();
    const expression = String((r && r.expression) ?? '').trim();
    const desc = String((r && r.desc) ?? '').replace(/\s+/g, ' ').trim().slice(0, 60);
    if (!tag && !expression && !desc) continue;   // 빈 줄은 무시
    if (!TAG_RE.test(tag)) throw new Error(`감정 태그는 영어/한글 글자만 쓸 수 있어요 (1~20자): "${tag}"`);
    if (seen.has(tag)) throw new Error(`감정 태그가 겹쳐요: "${tag}"`);
    if (!expression) throw new Error(`"${tag}" 에 연결할 표정을 골라 주세요`);
    seen.add(tag);
    out.push({ tag, expression, desc });
  }
  return out;
}

// [{tag, expression, desc}] -> 파일에 쓸 객체 (설명이 없으면 문자열 하나로 짧게)
function serializeAliases(rows) {
  const o = {};
  for (const r of rows) o[r.tag] = r.desc ? { expression: r.expression, desc: r.desc } : r.expression;
  return o;
}

// {태그: 표정} (config.json 의 옛 emotionAliases/modelAliases) -> rows
const rowsFromMap = (map) => parseAliases(map);

// 표정/모션 이름 비교용: "smirk.exp3.json", "exp/Smirk.exp3.json" -> "smirk" (renderer/emotion.js 의 cleanName 과 같은 규칙)
const cleanName = (v) => String(v ?? '').trim().split(/[\\/]/).pop().replace(/\.(exp3|motion3)\.json$/i, '').trim().toLowerCase();

// lyricsPose.json 내용 -> { expression, motion } (빈 칸은 ''). 모르는 키/문자열이 아닌 값은 버린다
function parseLyricsPose(obj) {
  const out = { expression: '', motion: '' };
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return out;
  for (const k of POSE_KEYS) out[k] = typeof obj[k] === 'string' ? obj[k].replace(/\s+/g, ' ').trim().slice(0, MAX_POSE) : '';
  return out;
}

// 화면에서 온 가사창 표정/모션을 검사한다. 감정 태그에 연결한 표정과 같으면 대사 중에도 나올 수 있으니 막는다. 잘못되면 Error
function validateLyricsPose(pose, rows) {
  if (!pose || typeof pose !== 'object' || Array.isArray(pose)) throw new Error('가사창 표정/모션 설정이 올바르지 않아요');
  const out = { expression: '', motion: '' };
  for (const k of POSE_KEYS) {
    const v = String(pose[k] ?? '').replace(/\s+/g, ' ').trim();
    if (v.length > MAX_POSE) throw new Error('가사창 표정/모션 이름이 너무 길어요');
    out[k] = v;
  }
  const used = new Map((rows || []).map((r) => [cleanName(r.expression), r.tag]));
  for (const [k, label] of [['expression', '표정'], ['motion', '모션']]) {
    const tag = out[k] && used.get(cleanName(out[k]));
    if (tag) throw new Error(`가사창 ${label} \"${out[k]}\" 은 감정 \"${tag}\" 에도 연결돼 있어요. 대사 중에 쓰이지 않게 다른 걸 골라 주세요`);
  }
  return out;
}

// lyricsItem.json 내용 -> { x, y, scale } (빠졌거나 잘못된 값은 기본값, 범위 밖은 범위 안으로)
function parseLyricsItem(obj) {
  const src = obj && typeof obj === 'object' && !Array.isArray(obj) ? obj : {};
  const out = {};
  for (const k of Object.keys(ITEM_DEFAULT)) {
    const n = Number(src[k]);
    const v = Number.isFinite(n) && src[k] !== null && src[k] !== '' ? n : ITEM_DEFAULT[k];
    out[k] = Math.round(Math.min(ITEM_LIMITS[k][1], Math.max(ITEM_LIMITS[k][0], v)) * 10000) / 10000;
  }
  return out;
}
function readLyricsItem(dir) {
  try { return parseLyricsItem(JSON.parse(clean(fs.readFileSync(path.join(dir, LYRICS_ITEM_FILE), 'utf8')))); }
  catch { return parseLyricsItem(null); }
}
function writeLyricsItem(dir, item) {
  const v = parseLyricsItem(item);
  writeAtomic(path.join(dir, LYRICS_ITEM_FILE), JSON.stringify(v, null, 2) + '\n');
  return v;
}
// 모델 폴더에 직접 넣은 그림 파일(없으면 '')
function findItemImage(dir) {
  for (const n of ITEM_IMAGE_NAMES) { const p = path.join(dir, n); try { if (fs.statSync(p).isFile()) return p; } catch { /* 없음 */ } }
  return '';
}

function writeAtomic(file, text) {
  const tmp = file + '.tmp';
  fs.writeFileSync(tmp, text, 'utf8');
  fs.renameSync(tmp, file);
}

// dir 안의 프로필을 읽는다. emotionAliases.json 이 아직 없으면 fallbackMap(config.json 의 옛 별칭)으로 한 번 만들어 준다.
// 돌려주는 값: { persona, rows, migrated, error }
function readProfile(dir, fallbackMap) {
  const res = { persona: '', rows: [], voice: parseVoice(null), lyricsPose: parseLyricsPose(null), migrated: false, error: '' };
  try { res.persona = clean(fs.readFileSync(path.join(dir, PERSONA_FILE), 'utf8')).trim(); } catch { /* 없으면 서버 기본 persona 를 씀 */ }
  const voicePath = path.join(dir, VOICE_FILE);
  if (fs.existsSync(voicePath)) {
    try { res.voice = parseVoice(JSON.parse(clean(fs.readFileSync(voicePath, 'utf8')))); }
    catch (e) { res.error = `${VOICE_FILE} 을 읽지 못했어요: ${e.message}`; }
  }
  const posePath = path.join(dir, LYRICS_POSE_FILE);
  if (fs.existsSync(posePath)) {
    try { res.lyricsPose = parseLyricsPose(JSON.parse(clean(fs.readFileSync(posePath, 'utf8')))); }
    catch (e) { res.error = res.error || `${LYRICS_POSE_FILE} 을 읽지 못했어요: ${e.message}`; }
  }
  const aliasPath = path.join(dir, ALIAS_FILE);
  if (fs.existsSync(aliasPath)) {
    try { res.rows = parseAliases(JSON.parse(clean(fs.readFileSync(aliasPath, 'utf8')))); }
    catch (e) { res.error = res.error || `${ALIAS_FILE} 을 읽지 못했어요: ${e.message}`; }
    return res;
  }
  const rows = rowsFromMap(fallbackMap);
  if (rows.length) {
    try { writeAtomic(aliasPath, JSON.stringify(serializeAliases(rows), null, 2)); res.migrated = true; }
    catch (e) { res.error = `${ALIAS_FILE} 을 만들지 못했어요: ${e.message}`; }
    res.rows = rows;
  }
  return res;
}

// persona 가 비면 persona.txt 를 지운다 (서버 기본 persona 를 쓰게). rows 는 validateRows 를 거친다.
function writeProfile(dir, { persona, rows, voice, lyricsPose }) {
  const text = String(persona ?? '').replace(/\r\n/g, '\n').trim();
  if (text.length > MAX_PERSONA) throw new Error(`persona 가 너무 길어요 (최대 ${MAX_PERSONA}자)`);
  const cleanRows = validateRows(rows);
  const v = voice === undefined ? undefined : validateVoice(voice);   // undefined 면 voice.json 은 건드리지 않는다
  const lp = lyricsPose === undefined ? undefined : validateLyricsPose(lyricsPose, cleanRows);   // undefined 면 lyricsPose.json 은 건드리지 않는다
  const pp = path.join(dir, PERSONA_FILE);
  if (text) writeAtomic(pp, text + '\n');
  else { try { fs.unlinkSync(pp); } catch { /* 이미 없음 */ } }
  writeAtomic(path.join(dir, ALIAS_FILE), JSON.stringify(serializeAliases(cleanRows), null, 2) + '\n');
  let savedVoice;
  const vp = path.join(dir, VOICE_FILE);
  if (v !== undefined) {
    const filled = Object.fromEntries(VOICE_KEYS.filter((k) => v[k]).map((k) => [k, k === 'speed_factor' ? Number(v[k]) : v[k]]));   // 속도는 voice.json 에 숫자로
    if (Object.keys(filled).length) writeAtomic(vp, JSON.stringify(filled, null, 2) + '\n');
    else { try { fs.unlinkSync(vp); } catch { /* 이미 없음 */ } }
    savedVoice = v;
  } else {
    try { savedVoice = parseVoice(JSON.parse(clean(fs.readFileSync(vp, 'utf8')))); } catch { savedVoice = parseVoice(null); }
  }
  let savedPose;
  const pp2 = path.join(dir, LYRICS_POSE_FILE);
  if (lp !== undefined) {
    const filled = Object.fromEntries(POSE_KEYS.filter((k) => lp[k]).map((k) => [k, lp[k]]));
    if (Object.keys(filled).length) writeAtomic(pp2, JSON.stringify(filled, null, 2) + '\n');
    else { try { fs.unlinkSync(pp2); } catch { /* 이미 없음 */ } }
    savedPose = lp;
  } else {
    try { savedPose = parseLyricsPose(JSON.parse(clean(fs.readFileSync(pp2, 'utf8')))); } catch { savedPose = parseLyricsPose(null); }
  }
  return { persona: text, rows: cleanRows, voice: savedVoice, lyricsPose: savedPose };
}

module.exports = { PERSONA_FILE, ALIAS_FILE, VOICE_FILE, LYRICS_POSE_FILE, LYRICS_ITEM_FILE, parseLyricsItem, readLyricsItem, writeLyricsItem, findItemImage, VOICE_KEYS, parseLyricsPose, validateLyricsPose, SPEED_MIN, SPEED_MAX, TAG_RE, MAX_TAGS, MAX_PERSONA, parseVoice, validateVoice, parseAliases, validateRows, serializeAliases, readProfile, writeProfile };
