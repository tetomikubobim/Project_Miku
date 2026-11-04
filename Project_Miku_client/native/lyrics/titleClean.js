// 곡 제목/가수 정리 (유튜브 제목의 잡음 제거) + 검색 후보 만들기. 순수 함수.
//   cleanQuery(title, artist) -> { direct, variants: [{ title, artist }] }   (앞쪽이 먼저 시도할 후보)
//   - 제목이 "??" 로 시작하면 직접 검색: 자동 정리/후보 없이 입력 그대로 (제목만 검색은 마지막에 한 번 더)
//   keyOf(title, artist) -> 캐시 키용 정규화 문자열
const NOISE_WORDS = [
  'official', 'mv', 'm/v', 'music video', 'lyric video', 'lyrics', 'lyric', 'audio', 'video', 'visualizer', 'hd', '4k', '1080p', '8k',
  'live', 'performance', 'teaser', 'trailer', 'full ver', 'full version', 'ver.', 'version', 'remaster', 'remastered', 'mono', 'stereo',
  'inst', 'instrumental', 'mr', 'karaoke', 'cover', 'prod.', 'color coded', 'eng sub', 'sub', 'subs', 'subtitle', 'subtitles',
  '가사', '뮤직비디오', '뮤비', '공식', '자막', '라이브', '직캠', '음원', '발음', '해석', '번역', '歌詞', '歌ってみた', '公式', 'ミュージックビデオ', '字幕', '和訳',
];
const NOISE_RE = new RegExp(`(?:^|[^a-z0-9])(?:${NOISE_WORDS.map((w) => w.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')).join('|')})(?:$|[^a-z0-9])`, 'i');
const FEAT_RE = /\b(?:feat|ft|featuring)\b\.?/i;
const OPEN = '\\(\\[【〔（［{「『<';
const CLOSE = '\\)\\]】〕）］}」』>';
const BRACKET_RE = new RegExp(`[${OPEN}]([^${OPEN}${CLOSE}]*)[${CLOSE}]`, 'g');
const CHANNEL_RE = /(vevo|\btopic\b|records?\b|entertainment|\blabels?\b|\bmusic\b|\bofficial\b|channel|\btv\b|\bent\.?$|레코드|엔터테인먼트|뮤직|채널|공식|1thek|원더케이|hybe|smtown|dingo|mnet|\bkbs\b|\bmbc\b|\bsbs\b|genie|melon|\bofficial\b|公式|レコード|ミュージック)/i;

function stripBrackets(s) {
  return s.replace(BRACKET_RE, (all, inner) => (NOISE_RE.test(inner) || FEAT_RE.test(inner) ? ' ' : all));
}

// 제목 한 덩어리 정리
function cleanTitle(raw) {
  let s = String(raw || '').normalize('NFKC').replace(/\s+/g, ' ').trim();
  for (let i = 0; i < 3; i++) s = stripBrackets(s);          // 중첩된 괄호까지
  s = s.replace(/\s*[-–—~|]\s*(?:official|mv|m\/v|music video|lyric|lyrics|audio|video|live|remaster(?:ed)?|\d{4} remaster(?:ed)?|remastered \d{4}|mono|stereo|ver\.?|version)\b.*$/i, '');
  s = s.replace(new RegExp(`\\s*${FEAT_RE.source}.*$`, 'i'), '');  // 괄호 없는 feat. 는 끝까지 뗀다
  s = s.replace(/\s*(?:\|\s*)?(?:official\s+)?(?:m\/v|mv|music video|lyric video|audio|가사|lyrics?)\s*$/i, '');
  s = s.replace(/(\S)\s*[「『][^」』]*[」』]\s*$/, '$1');            // "YOASOBI「Yoru ni Kakeru」" 처럼 뒤에 붙은 로마자 표기
  s = s.replace(/^[「『]([^」』]+)[」』]$/, '$1');                // 제목 전체를 감싼 꺾쇠
  return s.replace(/\s+/g, ' ').replace(/^[\s\-–—~|:：·]+|[\s\-–—~|:：·]+$/g, '').trim();
}

// 가수/채널 이름 정리. 채널명 같으면 '' (모름)
function cleanArtist(raw) {
  let s = String(raw || '').normalize('NFKC').replace(/\s+/g, ' ').trim();
  s = s.replace(/\s*-\s*topic$/i, '').replace(/vevo$/i, '').replace(/\s*(?:official|공식)(?:\s*(?:channel|youtube|채널))?$/i, '').trim();
  return s;
}
// "IU - Topic", "TaylorSwiftVEVO" 는 꼬리표만 떼면 가수 이름이라 채널로 보지 않는다
const looksLikeChannel = (raw) => !/(\s-\s*topic|[a-z0-9]vevo)$/i.test(String(raw || '').trim()) && CHANNEL_RE.test(String(raw || ''));
const firstArtist = (a) => String(a || '').split(/\s*(?:,|;|\/|&|＆|、| x | X | feat\.?| ft\.?| with )\s*/i)[0].trim();

function keyOf(title, artist) {
  const n = (s) => String(s || '').normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim();
  return `${n(title)}\x1f${n(artist)}`;
}

function cleanQuery(title, artist) {
  const rawTitle = String(title || '').trim();
  const rawArtist = String(artist || '').trim();
  if (rawTitle.startsWith('??')) {
    let t = rawTitle.slice(2).trim();
    let a = rawArtist;
    const m = t.match(/^(.+?)\s+[-–—]\s+(.+)$/);   // "가수 - 제목"
    if (m) { a = m[1].trim(); t = m[2].trim(); }
    const variants = [{ title: t, artist: a }];
    if (a) variants.push({ title: t, artist: '' });
    return { direct: true, variants };
  }

  const out = [];
  const add = (t, a) => {
    t = String(t || '').trim(); a = String(a || '').trim();
    if (!t) return;
    // ref: 플레이어가 알려 준 원래 가수. 가수를 빼고 제목만 검색해도 결과가 이 가수의 곡인지 확인하는 데 쓴다 (resolver 의 scoreItem)
    if (!out.some((v) => v.title.toLowerCase() === t.toLowerCase() && v.artist.toLowerCase() === a.toLowerCase())) out.push({ title: t, artist: a, ref: artistName });
  };

  const channelish = looksLikeChannel(rawArtist);
  const artist1 = cleanArtist(rawArtist);
  const artistName = channelish ? '' : artist1;
  const t1 = cleanTitle(rawTitle);

  // "가수 - 제목" 꼴 (유튜브): 두 가지 순서를 다 시도한다. 채널이 가수가 아니면 이쪽을 먼저.
  const m = t1.match(/^(.+?)\s+[-–—|/_]\s+(.+)$/);
  const dash = m ? { left: cleanTitle(m[1]), right: cleanTitle(m[2]) } : null;
  if (dash && dash.left && dash.right) {
    const leftIsArtist = artistName && dash.left.toLowerCase().includes(artistName.toLowerCase());
    if (leftIsArtist) add(dash.right, dash.left);
    else if (artistName && dash.right.toLowerCase().includes(artistName.toLowerCase())) add(dash.left, dash.right);
    else { add(dash.right, dash.left); add(dash.left, dash.right); }
  }

  add(t1, artistName);
  if (artistName) { const fa = firstArtist(artistName); if (fa && fa !== artistName) add(t1, fa); }
  add(t1, '');
  if (dash) {
    const isArtist = (x) => artistName && x.toLowerCase() === artistName.toLowerCase();
    if (!isArtist(dash.right)) add(dash.right, '');
    if (!isArtist(dash.left)) add(dash.left, '');
  }
  if (rawTitle && rawTitle !== t1) add(rawTitle.normalize('NFKC'), artistName);   // 정리가 과해서 망가졌을 때를 대비해 원본도
  return { direct: false, variants: out.slice(0, 8) };
}

module.exports = { cleanQuery, cleanTitle, cleanArtist, keyOf, looksLikeChannel };
