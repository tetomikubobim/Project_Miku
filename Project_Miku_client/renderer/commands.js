// 로컬 명령 해석기. 서버로 보내기 전에 먼저 걸러서, 미디어 제어/일정 알림이면 클라이언트에서 직접 처리한다.
//
//   parseCommand(text, now) ->
//     { kind: 'media', action, value, label }
//     { kind: 'media-timer', at(ms), action, value, label, repeat, weekday }   "음악 30분 뒤에 꺼줘" 같은 예약 미디어 명령
//     { kind: 'media-timer-cancel' }                                            "음악 예약 취소"
//     { kind: 'schedule-add', at(ms), text, repeat, weekday, error? }
//     { kind: 'schedule-list' }
//     { kind: 'schedule-cancel', index(1~) | null, all }
//     { kind: 'proactive', enabled: true|false|null, minutes: number|null, factor?: 0.5|2 }   먼저 말 걸기 켜기/끄기/간격/더 자주·덜
//     null  (일반 대화 -> 서버로)
//
// 오탐을 줄이려고 미디어 명령은 "문장 전체가 명령"일 때만 인식하고,
// 일정은 "시각 표현 + 문장 끝의 알려줘/깨워줘/알림" 일 때만 인식한다.

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];
const KO_NUM = { 한: 1, 두: 2, 세: 3, 네: 4, 다섯: 5, 여섯: 6, 일곱: 7, 여덟: 8, 아홉: 9, 열: 10 };
const toNum = (s) => (/^\d+$/.test(s) ? parseInt(s, 10) : KO_NUM[s]);

// ---------- 정규화 ----------
const norm = (s) =>
  String(s || '').toLowerCase().replace(/[\s.,!?~…'"“”‘’]/g, '').replace(/좀/g, '');
const SUFFIX = /(해줘|해주세요|해줄래|해봐|맞춰줘|맞춰|바꿔줘|바꿔|줘|주세요|줄래|주라|달라고|봐|해라|해요|하자|해|요)$/;
// 캐릭터를 부르며 말하는 경우: "미쿠야 음악 멈춰줘" -> "음악 멈춰줘" (뒤에 명령 낱말이 이어질 때만 부름말로 본다)
const VOCATIVE = /^(?:[가-힣]{1,5}[아야]|야)(?=(?:음악|노래|곡|미디어|플레이어|볼륨|소리|음량|사운드|음소거|뮤트|다음|이전|일시|정지|중지|멈|스톱|재생|틀어|그만|지금))/;
function stripSuffix(n) {
  for (let i = 0; i < 3; i++) {
    const m = n.replace(SUFFIX, '');
    if (m === n || !m) break;
    n = m;
  }
  return n;
}

// ---------- 미디어 ----------
const VOL = '(?:음악|노래|미디어|시스템|컴퓨터|pc)?(?:볼륨|소리|음량|사운드|volume)';
const MEDIA_RULES = [
  [/^(?:다음|담)(?:곡|노래|트랙|거|음악)(?:으?로)?(?:틀어|재생|넘겨|넘기|가|이동)?$/, 'next', '다음 곡'],
  [/^(?:곡|노래|트랙)?(?:넘겨|넘기|스킵|skip|next|넥스트)$/, 'next', '다음 곡'],
  [/^(?:이전|전|저번)(?:곡|노래|트랙|거)(?:으?로)?(?:틀어|재생|돌려|돌아|가|이동)?$/, 'prev', '이전 곡'],
  [/^(?:곡|노래|트랙)?(?:되돌려|previous|prev)$/, 'prev', '이전 곡'],
  [/^(?:음악|노래|곡|미디어)?(?:재생|다시재생|재생해|이어서재생|이어서|틀어|다시틀어|play|resume)$/, 'play', '재생'],
  [/^(?:음악|노래|곡|미디어|플레이어)(?:다시)?(?:켜|켜기|켜자|켤래|켜봐)$/, 'play', '재생'],   // 음악 켜줘 (소리 켜줘 는 음소거 해제)
  [/^(?:음악|노래|곡|미디어|플레이어)?(?:일시정지|일시중지|정지|중지|멈춰|멈춤|멈추|스톱|stop|pause)(?:시켜)?$/, 'pause', '일시정지'],
  [/^(?:음악|노래|곡|미디어|플레이어)(?:꺼|끄기|끄자|끌래|끄|그만|그만틀어|그만재생|그만들어|그만해)$/, 'pause', '일시정지'],   // 음악 꺼줘 / 노래 그만
  [/^그만(?:틀어|재생|들어)$/, 'pause', '일시정지'],
  [/^(?:음소거|뮤트)(?:해제|풀어|풀기|끄기|꺼|취소)$|^(?:unmute|소리(?:다시)?(?:켜|켜기|나게|풀어))$/, 'unmute', '음소거 해제'],
  [/^(?:음소거|뮤트|mute)(?:모드)?(?:켜|켜기)?$|^소리(?:꺼|끄기|끄자|없애|죽여)$/, 'mute', '음소거'],
  [/^(?:지금)?(?:무슨|뭔)(?:곡|노래|음악)(?:이야|이니|나와|들려|야|임)?$/, 'nowplaying', '재생 중인 곡 확인'],
];
const clampPct = (v) => Math.min(100, Math.max(0, v));

function parseMedia(n) {
  for (const [re, action, label] of MEDIA_RULES) if (re.test(n)) return { kind: 'media', action, value: -1, label };

  let m = new RegExp(`^${VOL}(?:을|를|은|는)?(\\d{1,3})(?:%|퍼센트|프로)?(?:으?로|까지)?(?:맞춰|설정|조절|바꿔)?$`).exec(n);
  if (m) return { kind: 'media', action: 'volume-set', value: clampPct(parseInt(m[1], 10)), label: `볼륨 ${clampPct(parseInt(m[1], 10))}%` };

  if (new RegExp(`^${VOL}(?:을|를)?(?:최대|맥스|max)(?:으?로)?$`).test(n)) return { kind: 'media', action: 'volume-set', value: 100, label: '볼륨 최대' };
  if (new RegExp(`^${VOL}(?:을|를)?(?:최소|min)(?:으?로)?$`).test(n)) return { kind: 'media', action: 'volume-set', value: 0, label: '볼륨 최소' };
  if (new RegExp(`^${VOL}(?:을|를)?(?:반|절반)(?:으?로)?$`).test(n)) return { kind: 'media', action: 'volume-set', value: 50, label: '볼륨 50%' };

  m = new RegExp(`^${VOL}(?:을|를)?(\\d{1,3})?(?:%|퍼센트)?(?:조금|약간|살짝|많이|확)?(?:더)?(?:올려|높여|키워|크게|업|up)$`).exec(n);
  if (m) return { kind: 'media', action: 'volume-up', value: m[1] ? clampPct(parseInt(m[1], 10)) : -1, label: '볼륨 올리기' };

  m = new RegExp(`^${VOL}(?:을|를)?(\\d{1,3})?(?:%|퍼센트)?(?:조금|약간|살짝|많이|확)?(?:더)?(?:내려|낮춰|줄여|작게|다운|down)$`).exec(n);
  if (m) return { kind: 'media', action: 'volume-down', value: m[1] ? clampPct(parseInt(m[1], 10)) : -1, label: '볼륨 내리기' };

  if (new RegExp(`^${VOL}(?:이|가)?(?:몇|얼마)(?:이야|야|인지|임|니|예요)?$`).test(n)) return { kind: 'media', action: 'volume-get', value: -1, label: '볼륨 확인' };
  return null;
}

// ---------- 일정 ----------
const INTENT_END =
  /(?:알려\s?(?:줘|줄래|주세요|달라고)|알림|알람|리마인드|리마인더|깨워\s?줘|깨워|말해\s?줘|불러\s?줘|타이머)\s*(?:설정|등록|추가|맞춰|해)?\s*(?:줘|주세요|줄래|해줘)?[\s.!?~]*$/;
const INTENT_START = /^\s*(?:알람|알림|리마인드|타이머)/;

const REL_RE =
  /(?:(\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*시간(\s*반)?)?\s*(?:(\d+)\s*분)?\s*(?:(\d+)\s*초)?\s*(?:뒤|후|있다가|이따가)(?:에)?/g;
const TIMER_RE = /(\d+)\s*(시간|분|초)\s*(?:짜리\s*)?타이머|타이머\s*(\d+)\s*(시간|분|초)/;
const ABS_TIME_RE =
  /(?:(\d{1,2}):(\d{2})|(\d{1,2})\s*시(?!간)(?:\s*(?:(\d{1,2})\s*분|(반)))?)(?:\s*(?:에|쯤|경에|경))?/;
// 시각 바로 앞에 붙은 것만 시간대로 본다 ("저녁 먹으라고" 의 저녁은 제외)
const PERIOD_RE = /(오전|오후|아침|점심|낮|저녁|밤|새벽)\s*(?=\d{1,2}\s*(?::|시(?!간)))/;
const DATE_WORD_RE = /(오늘|내일|모레|글피)/;
const MD_RE = /(\d{1,2})\s*월\s*(\d{1,2})\s*일/;
const WD_RE = /(이번\s*주|다음\s*주|담\s*주|매주)?\s*([월화수목금토일])요일/;

function mondayOf(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x;
}

function clockCandidates(h, period, hasDate) {
  if (h > 24) return null;
  if (period) {
    if (['새벽', '오전', '아침'].includes(period)) return [h % 12];
    if (['오후', '낮', '점심'].includes(period)) return [h < 12 ? h + 12 : h];
    return [h < 12 ? h + 12 : h === 12 ? 0 : h % 24]; // 저녁/밤 (밤 12시는 자정)
  }
  if (h >= 13 || h === 0) return [h % 24];
  if (h === 12) return hasDate ? [12] : [0, 12];
  return hasDate ? [h <= 6 ? h + 12 : h] : [h, h + 12];
}

export function parseSchedule(text, now = new Date()) {
  const t = String(text || '').trim();
  if (!t || !(INTENT_END.test(t) || INTENT_START.test(t))) return null;

  let rest = t;
  const take = (re) => {
    const m = re.exec(rest);
    if (m) rest = rest.slice(0, m.index) + ' ' + rest.slice(m.index + m[0].length);
    return m;
  };

  let repeat = null;
  let weekday = null;
  if (take(/매일/)) repeat = 'daily';
  else if (take(/평일(?:마다)?/)) repeat = 'weekdays';

  // 1) 상대 시각: "30분 뒤", "1시간 30분 후", "10분 타이머"
  let relMs = null;
  const timer = TIMER_RE.exec(rest);
  if (timer) {
    const n = parseInt(timer[1] || timer[3], 10);
    const unit = timer[2] || timer[4];
    relMs = n * (unit === '시간' ? 3600e3 : unit === '분' ? 60e3 : 1e3);
    rest = rest.replace(timer[0], ' ');
  } else {
    for (const m of rest.matchAll(REL_RE)) {
      if (!(m[1] || m[2] || m[3] || m[4])) continue; // "뒤" 만 걸린 경우
      const h = m[1] ? toNum(m[1]) : 0;
      relMs = ((h || 0) * 60 + (m[2] ? 30 : 0) + (m[3] ? parseInt(m[3], 10) : 0)) * 60e3 + (m[4] ? parseInt(m[4], 10) * 1e3 : 0);
      rest = rest.slice(0, m.index) + ' ' + rest.slice(m.index + m[0].length);
      break;
    }
  }

  let at = null;
  if (relMs != null) {
    if (relMs <= 0) return null;
    at = new Date(now.getTime() + relMs);
    repeat = null;
  } else {
    // 2) 절대 시각
    const md = take(MD_RE);
    const dw = take(DATE_WORD_RE);
    const wd = take(WD_RE);
    const period = take(PERIOD_RE);
    const tm = take(ABS_TIME_RE);
    if (!tm) return null; // 시각이 없으면 일정 요청이 아님 -> 서버로

    let h;
    let min = 0;
    if (tm[1] !== undefined) { h = parseInt(tm[1], 10); min = parseInt(tm[2], 10); }
    else { h = parseInt(tm[3], 10); min = tm[5] ? 30 : tm[4] ? parseInt(tm[4], 10) : 0; }
    if (min > 59) return null;

    const hasDate = !!(md || dw || wd);
    const cands = clockCandidates(h, period ? period[1] : null, hasDate);
    if (!cands) return null;

    const day0 = new Date(now);
    day0.setHours(0, 0, 0, 0);
    const mk = (day, hh) => { const d = new Date(day); d.setHours(hh, min, 0, 0); return d; };

    if (wd && wd[1] === '매주') { repeat = 'weekly'; }
    if (wd) weekday = WEEKDAYS.indexOf(wd[2]);

    if (md) {
      let day = new Date(now.getFullYear(), parseInt(md[1], 10) - 1, parseInt(md[2], 10));
      at = mk(day, cands[0]);
      if (at <= now) { day = new Date(now.getFullYear() + 1, parseInt(md[1], 10) - 1, parseInt(md[2], 10)); at = mk(day, cands[0]); }
    } else if (dw) {
      const off = { 오늘: 0, 내일: 1, 모레: 2, 글피: 3 }[dw[1]];
      const day = new Date(day0); day.setDate(day.getDate() + off);
      at = mk(day, cands[0]);
      if (at <= now) return { kind: 'schedule-add', error: 'past', text: '' };
    } else if (wd) {
      const prefix = (wd[1] || '').replace(/\s+/g, '');
      const target = weekday;
      let day;
      if (prefix === '다음주' || prefix === '담주') {
        day = mondayOf(now); day.setDate(day.getDate() + 7 + ((target + 6) % 7));
      } else if (prefix === '이번주') {
        day = mondayOf(now); day.setDate(day.getDate() + ((target + 6) % 7));
      } else {
        day = new Date(day0); day.setDate(day.getDate() + ((target - now.getDay() + 7) % 7));
      }
      at = mk(day, cands[0]);
      if (at <= now) {
        if (prefix === '이번주') return { kind: 'schedule-add', error: 'past', text: '' };
        at.setDate(at.getDate() + 7); // 오늘 그 요일인데 시각이 지났으면 다음 주
      }
    } else {
      // 날짜 없음: 가장 가까운 미래. 오전/오후 모호하면 더 빠른 쪽
      const today = cands.map((hh) => mk(day0, hh)).filter((d) => d > now).sort((a, b) => a - b);
      if (today.length) at = today[0];
      else { const tom = new Date(day0); tom.setDate(tom.getDate() + 1); at = mk(tom, cands.slice().sort((a, b) => a - b)[0]); }
    }

    if (repeat === 'weekdays') {
      while (at.getDay() === 0 || at.getDay() === 6) at.setDate(at.getDate() + 1);
    }
  }

  // 알림 내용: 시각 표현과 문장 끝의 "알려줘" 를 걷어낸 나머지
  rest = rest.replace(INTENT_END, ' ').replace(INTENT_START, ' ').replace(/\s+/g, ' ').trim();
  let prev;
  do { prev = rest; rest = rest.replace(/^(?:에는|에서|에|부터|까지|쯤|정도|경|이랑|랑|,)(?=\s|$)\s*/, ''); } while (rest !== prev);
  rest = rest.replace(/^타이머\s*/, '').trim();

  // 내용이 비었으면 말투에 맞는 기본 문구 ("7시에 깨워줘" -> 일어날 시간이에요)
  const fallback = /깨워/.test(t) ? '일어날 시간이에요' : /타이머/.test(t) ? '타이머가 끝났어요' : '알림';
  return { kind: 'schedule-add', at: at.getTime(), text: rest || fallback, repeat, weekday };
}

// ---------- 미디어 예약 ("음악 30분 뒤에 꺼줘", "10분 뒤에 노래 틀어줘", "밤 11시에 음악 꺼줘") ----------
// 시각 해석은 parseSchedule 을 그대로 빌린다: 문장 끝에 '알림' 을 붙여 일정으로 읽게 한 뒤,
// 시각 표현을 걷어내고 남은 말("음악 꺼줘")이 통째로 미디어 명령일 때만 예약으로 본다. (오탐 방지)
const TIMED_MEDIA_ACTIONS = new Set(['play', 'pause', 'next', 'prev', 'mute', 'unmute', 'volume-set', 'volume-up', 'volume-down']);
const TIMED_LABEL = {
  pause: '음악 끄기(일시정지)', play: '음악 켜기(재생)', next: '다음 곡', prev: '이전 곡',
  mute: '음소거', unmute: '음소거 해제',
};

export function parseTimedMedia(raw, now = new Date()) {
  const t = String(raw || '').trim();
  if (!t || INTENT_END.test(t) || INTENT_START.test(t)) return null; // "~라고 알려줘" 는 그냥 알림 일정
  const sch = parseSchedule(`${t} 알림`, now);
  if (!sch || sch.error || !sch.text) return null;
  const body = stripSuffix(norm(sch.text)).replace(VOCATIVE, '');
  const media = parseMedia(body) || parseMedia(body.replace(/^(?:[가-힣]{1,5}[아야]|야)/, ''));
  if (!media || !TIMED_MEDIA_ACTIONS.has(media.action)) return null;
  return {
    kind: 'media-timer', at: sch.at, action: media.action, value: media.value,
    label: TIMED_LABEL[media.action] || media.label, repeat: sch.repeat, weekday: sch.weekday,
  };
}

// ---------- 먼저 말 걸기 ----------
const PRO_WORD = '(?:먼저말걸어|먼저말걸기(?:모드)?(?:켜|켜기|시작|on)?|자발(?:적)?(?:대화|모드)(?:켜|켜기|시작|on)?)';
const PRO_EVERY_A = new RegExp(`^(\\d+)(분|시간)(?:마다|간격(?:으로)?|에한번)${PRO_WORD}$`);
const PRO_EVERY_B = /^먼저말걸기(?:모드)?(\d+)(분|시간)(?:마다|간격(?:으로)?)$/;
const PRO_ON = new RegExp(`^${PRO_WORD}$`);
const PRO_FASTER = /^(?:먼저)?(?:더자주(?:먼저)?말걸어|말(?:을)?더자주걸어)$/;   // 더 자주 말 걸어줘 -> 간격을 절반으로
const PRO_SLOWER = /^(?:먼저)?(?:덜(?:먼저)?말걸어|말(?:을)?덜걸어)$/;           // 덜 말 걸어줘 / 말 좀 덜 걸어줘 -> 간격을 두 배로
const PRO_OFF = /^(?:먼저말걸지마|먼저말걸지말아|먼저말걸지않아|먼저말걸기(?:모드)?(?:꺼|끄기|중지|off)|자발(?:적)?(?:대화|모드)(?:꺼|끄기|중지|off))$/;

function parseProactive(n) {
  let m = PRO_EVERY_A.exec(n) || PRO_EVERY_B.exec(n);
  if (m) {
    const minutes = Math.min(Math.max(parseInt(m[1], 10) * (m[2] === '시간' ? 60 : 1), 1), 720);
    return { kind: 'proactive', enabled: true, minutes };
  }
  if (PRO_FASTER.test(n)) return { kind: 'proactive', enabled: null, minutes: null, factor: 0.5 };
  if (PRO_SLOWER.test(n)) return { kind: 'proactive', enabled: null, minutes: null, factor: 2 };
  if (PRO_OFF.test(n)) return { kind: 'proactive', enabled: false, minutes: null };
  if (PRO_ON.test(n)) return { kind: 'proactive', enabled: true, minutes: null };
  return null;
}

// ---------- 표시용 ----------
export function fmtWhen(ms, now = new Date()) {
  const d = new Date(ms);
  const h = d.getHours();
  const clock = `${h < 12 ? '오전' : '오후'} ${h % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')}`;
  const day0 = new Date(now); day0.setHours(0, 0, 0, 0);
  const dd = new Date(d); dd.setHours(0, 0, 0, 0);
  const diff = Math.round((dd - day0) / 86400e3);
  const date = diff === 0 ? '오늘' : diff === 1 ? '내일' : diff === 2 ? '모레' : `${d.getMonth() + 1}월 ${d.getDate()}일(${WEEKDAYS[d.getDay()]})`;
  return `${date} ${clock}`;
}

export function fmtRepeat(repeat, weekday) {
  if (repeat === 'daily') return '매일';
  if (repeat === 'weekdays') return '평일마다';
  if (repeat === 'weekly') return `매주 ${WEEKDAYS[weekday]}요일`;
  return '';
}

// ---------- 기념일 (/기념일 만 받는다: 평소 대화와 헷갈리지 않게) ----------
//   /기념일 | /기념일 목록          -> 목록
//   /기념일 삭제 2                  -> 2번 삭제 (먼저 목록을 본 뒤)
//   /기념일 3월 5일 생일 | /기념일 3/5 생일 | /기념일 2020년 3월 5일 사귄 날  -> 등록 (연도를 쓰면 N주년 계산)
function parseAnniversary(raw) {
  const m = /^\/\s*기념일\s*(.*)$/.exec(raw);
  if (!m) return null;
  const rest = m[1].trim();
  if (!rest || /^(?:목록|리스트|조회|보기|확인)$/.test(rest)) return { kind: 'anniv-list' };
  const del = /^(?:삭제|취소|지우기|지워)\s*(\d+)\s*번?$/.exec(rest);
  if (del) return { kind: 'anniv-delete', index: parseInt(del[1], 10) };
  const add = /^(?:(\d{4})\s*년\s*)?(\d{1,2})\s*(?:월|\/|\.|-)\s*(\d{1,2})\s*일?\s*(.+)$/.exec(rest);
  if (add) return { kind: 'anniv-add', year: add[1] ? parseInt(add[1], 10) : null, month: parseInt(add[2], 10), day: parseInt(add[3], 10), label: add[4].trim().slice(0, 40) };
  return { kind: 'anniv-list' };
}

// ---------- 진입점 ----------
export function parseCommand(text, now = new Date()) {
  const raw = String(text || '').trim();
  if (!raw || raw.length > 80) return null;

  const ann = parseAnniversary(raw, now);
  if (ann) return ann;

  const timed = parseTimedMedia(raw, now);
  if (timed) return timed;

  const sch = parseSchedule(raw, now);
  if (sch) return sch;

  const n = stripSuffix(norm(raw.replace(/^\//, ''))).replace(VOCATIVE, '');

  if (/^(?:음악|노래|미디어)?(?:예약|타이머)(?:전체|전부|모두)?(?:취소|삭제|해제|지워|지우기)$/.test(n)) return { kind: 'media-timer-cancel' };

  if (/^\//.test(raw) && /^(?:일정|알림|알람|스케줄)$/.test(n)) return { kind: 'schedule-list' };
  if (/^(?:일정|알림|알람|스케줄)(?:(?:목록|리스트|조회)(?:보여|확인)?|보여|확인|뭐있어|뭐있지)$/.test(n)) return { kind: 'schedule-list' };

  let m = /^(?:일정|알림|알람|스케줄)(?:(\d+)번?)?(전체|전부|모두)?(?:취소|삭제|지워|지우기)(?:(\d+)번?)?$/.exec(n);
  if (m) {
    const idx = m[1] || m[3];
    return { kind: 'schedule-cancel', index: idx ? parseInt(idx, 10) : null, all: !!m[2] };
  }

  return parseProactive(n) || parseMedia(n);
}
