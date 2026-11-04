// 감정 -> 표정/모션 적용.
//  - 서버 감정 라벨(neutral, happy, sad, angry, surprise)로 찾기: play(label)
//  - LLM 의 <think> 속마음 텍스트에서 단서를 찾기: matchText(text)

// 라벨과 같은 뜻으로 보고 자동 매칭할 이름들. 표정 파일 이름이 happy 가 아니라 smile / joy / 기쁨 이어도 연결된다.
const SYNONYMS = {
  happy: ['happy', 'joy', 'smile', 'glad', '기쁨', '행복', '웃음'],
  sad: ['sad', 'sadness', 'cry', 'crying', '슬픔', '울음'],
  angry: ['angry', 'anger', 'mad', 'annoyed', '분노', '화남', '화'],
  surprise: ['surprise', 'surprised', 'shock', 'shocked', '놀람', '당황'],
  neutral: ['neutral', 'default', 'normal', '중립', '보통'],
  blush: ['blush', 'shy', 'embarrassed', 'red', '수줍', '부끄'],
  smirk: ['smirk', 'sly'],
  disgust: ['disgust', '혐오'],
};
const GROUP_OF = new Map();
for (const words of Object.values(SYNONYMS)) for (const w of words) GROUP_OF.set(w, words);

// <think> 텍스트에서 감정 단서로 볼 단어(영어는 어근, 한국어는 일부)
const hint = (ascii, ko = []) =>
  new RegExp(`(?<![a-z])(?:${ascii.join('|')})` + (ko.length ? `|(?:${ko.join('|')})` : ''), 'i');
const HINTS = {
  happy: hint(['smil', 'laugh', 'giggl', 'grin', 'beam', 'happ', 'joy', 'delight', 'cheer', 'glad', 'proud', 'excit'], ['웃', '미소', '기쁘', '기뻐', '행복', '즐거', '뿌듯']),
  sad: hint(['sad', 'cry', 'tear', 'sob', 'sorrow', 'lonely', 'upset', 'disappoint', 'gloom', 'droop', 'pout'], ['슬프', '슬퍼', '눈물', '울먹', '서운', '시무룩', '우울']),
  angry: hint(['angr', 'anger', 'furious', 'glare', 'scowl', 'irritat', 'annoy', 'frown', 'fume'], ['화가', '화난', '짜증', '분노', '노려', '찡그']),
  surprise: hint(['surpris', 'shock', 'gasp', 'startl', 'astonish', 'wide-eyed', 'jolt'], ['놀라', '놀란', '깜짝', '당황']),
  blush: hint(['blush', 'flush', 'embarrass', 'shy', 'fluster', 'red\\b'], ['붉', '빨개', '발그', '수줍', '부끄']),
  smirk: hint(['smirk', 'sly', 'mischiev', 'cocky', 'teas'], ['씩', '능글', '장난']),
  disgust: hint(['disgust', 'yuck', 'eww', 'gross', 'repuls', 'cringe'], ['역겨', '혐오', '질색']),
};

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// "smirk.exp3.json", "exp/Smirk.exp3.json" -> "smirk"  (확장자/경로를 붙여 써도 되게)
export const cleanName = (v) =>
  String(v).trim().split(/[\\/]/).pop().replace(/\.(exp3|motion3)\.json$/i, '').trim().toLowerCase();

export class EmotionController {
  constructor(model, emotionMap, aliases = {}) {
    this.model = model;
    this.map = emotionMap;
    this.lastMatch = ''; // 마지막으로 적용한 것의 설명 (말풍선 태그 표시용)
    this.reserved = new Set(); // 가사창 전용 표정/모션 이름: 대사의 감정으로는 절대 고르지 않는다 (playPose 로만 재생)
    this.setAliases(aliases);
  }

  // 키(서버 라벨/단서 단어)와 값(표정 이름) 모두 대소문자 무시. 값의 확장자(.exp3.json)와 경로는 자동으로 뗀다.
  setAliases(aliases) {
    this.aliases = {};
    for (const [k, v] of Object.entries(aliases || {})) {
      this.aliases[String(k).trim().toLowerCase()] = cleanName(v);
    }
  }

  list() {
    return [...this.map.keys()];
  }

  // 표정 이름 목록과 모션 이름 목록을 나눠서 (설정 창에서 따로 고르게)
  listTyped() {
    const out = { expressions: [], motions: [] };
    for (const [k, v] of this.map) (v.type === 'expression' ? out.expressions : out.motions).push(k);
    return out;
  }

  // 가사창 전용 이름들(표정/모션 파일 이름, 확장자/경로는 자동으로 뗀다). 이 이름들은 감정 태그/think 단서/동의어 매칭에서 빠진다.
  setReserved(names) {
    this.reserved = new Set((names || []).map((n) => cleanName(n)).filter(Boolean));
  }

  // 가사창이 뜰 때 쓰는 표정과 모션을 재생한다. pose: { expression, motion } (비어 있는 쪽은 건너뜀). 하나라도 재생했으면 true
  playPose(pose) {
    let any = false;
    for (const k of ['expression', 'motion']) {
      const name = pose && pose[k];
      if (name && this.preview(name)) any = true;
    }
    if (any) this.lastMatch = '가사창 표정/모션';
    return any;
  }

  // 가사창이 끝났을 때: 표정을 중립으로 돌린다 (neutral 에 표정이 연결돼 있으면 그걸로)
  releasePose() {
    this.play('neutral');
  }

  _lookup(label) {
    const raw = String(label || '').trim().toLowerCase();
    if (!raw) return null;
    // 우선순위: 사용자 별칭 > 라벨과 같은 이름 > 동의어
    const candidates = [this.aliases[raw], raw, ...(GROUP_OF.get(raw) || [])];
    for (const c of candidates) {
      if (c && !this.reserved.has(c) && this.map.has(c)) return this.map.get(c);
    }
    return null;
  }

  // <think> 텍스트에서 가장 먼저 나오는 감정 단서를 찾아 "모델에 실제로 연결되는" 라벨을 돌려준다. 없으면 null.
  //  1) 표정/모션 이름이 글자 그대로 나온 경우 (예: "cheeks turning red" -> red)
  //  2) 감정 단어 힌트 (smiles -> happy, tears -> sad, 얼굴이 붉어지며 -> blush ...)
  //  3) 사용자 별칭의 키가 글자 그대로 나온 경우
  matchText(text) {
    const t = String(text || '').toLowerCase();
    if (!t.trim()) return null;
    const found = []; // { pos, label }

    const ascii = (s) => /^[\x00-\x7f]*$/.test(s);
    // 단어 시작에서 매칭. 짧은 영어 이름(red, sad 등)은 단어가 정확히 끝나야 한다 (reducing 안의 red 오탐 방지)
    const wordAt = (word) => {
      const tail = ascii(word) && word.length <= 4 ? '(?![a-z0-9])' : '';
      const m = new RegExp(`(^|[^a-z0-9가-힣])${escapeRe(word)}${tail}`).exec(t);
      return m ? m.index + m[1].length : -1;
    };
    for (const key of this.map.keys()) {
      if (this.reserved.has(key)) continue; // 가사창 전용 이름은 think 텍스트에 나와도 고르지 않는다
      if (key.length < (ascii(key) ? 3 : 2) || /^\d+$/.test(key)) continue; // 너무 짧거나 숫자뿐인 이름은 오탐이 많음
      const pos = wordAt(key);
      if (pos >= 0) found.push({ pos, label: key });
    }
    for (const [label, re] of Object.entries(HINTS)) {
      const m = re.exec(t);
      if (m) found.push({ pos: m.index, label });
    }
    for (const key of Object.keys(this.aliases)) {
      if (key.length < (ascii(key) ? 3 : 2)) continue;
      const pos = wordAt(key);
      if (pos >= 0) found.push({ pos, label: key });
    }

    found.sort((a, b) => a.pos - b.pos); // 같은 위치면 먼저 넣은 쪽(이름 그대로) 우선: sort 는 안정 정렬
    for (const f of found) if (this._lookup(f.label)) return f.label;
    return null;
  }

  // 이 라벨이 모델의 표정/모션에 실제로 연결되는지
  resolves(label) { return !!this._lookup(label); }

  // 표정/모션 이름(list() 의 값)을 별칭을 거치지 않고 바로 재생 (설정 창 미리보기)
  preview(name) {
    const entry = this.map.get(cleanName(name));
    return entry ? this._apply(entry, name) : false;
  }

  play(label) {
    const entry = this._lookup(label);
    if (!entry) {
      this.lastMatch = '';
      // neutral 계열이면 표정 초기화, 그 외 알 수 없는 라벨은 무시
      if (['neutral', '중립', 'normal'].includes(String(label).toLowerCase())) { this.reset(); this.lastMatch = '표정 초기화'; return true; }
      return false;
    }
    return this._apply(entry, label);
  }

  _apply(entry, label) {
    this.lastMatch = entry.type === 'expression' ? `표정:${entry.name}` : `모션:${entry.group}#${entry.index}`;
    try {
      if (entry.type === 'expression') {
        this.model.expression(entry.name);
      } else {
        // 3 = MotionPriority.FORCE
        this.model.motion(entry.group, entry.index, 3);
      }
      return true;
    } catch (e) {
      console.warn('감정 적용 실패', label, e);
      return false;
    }
  }

  reset() {
    try {
      const mgr = this.model.internalModel.motionManager.expressionManager;
      if (mgr && mgr.resetExpression) mgr.resetExpression();
    } catch { /* ignore */ }
  }
}
