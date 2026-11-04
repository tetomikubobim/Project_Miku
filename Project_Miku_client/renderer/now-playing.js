// "이 노래 누가 불렀어?" 처럼 지금 재생 중인 곡을 묻는 말인지 보고, 맞으면 AI 에게 보낼 곡 정보를 만든다.
// 곡이 바뀔 때마다 보내지 않는다: 묻는 메시지에만 그 메시지와 함께 보낸다 (서버는 그 턴의 시스템 프롬프트에만 붙이고 기록에는 안 남긴다).

// 곡을 가리키는 말 + 묻는 말 (여러 형태로 물을 수 있어서 넉넉하게 잡는다. 틀려서 붙여도 해는 없다: 곡 정보가 한 줄 더 가는 것뿐)
const PATTERNS = [
  /(?:이|이번|다음|지금|방금|현재|요즘)\s*(?:나오는|흘러나오는|들리는|듣고\s*있는|듣는|재생\s*중인|틀어진|틀어\s*놓은)?\s*(?:노래|곡|음악|bgm|배경\s*음악|배경음)/i,   // 이 노래, 지금 나오는 곡
  /(?:노래|곡|음악|bgm|배경\s*음악|가수|아티스트|앨범).{0,14}(?:누가|누구|누굴|부른|부르|불렀|제목|이름|뭐야|뭔지|뭐더라|뭐지|무슨|어떤|작곡|언제|몇\s*년)/i,   // 노래 누가 불렀어, 곡 제목이 뭐야
  /(?:무슨|뭔|무슨\s*곡의?)\s*(?:노래|곡|음악)\s*(?:이야|이지|이더라|인지|인가|야|지|임|더라)/,                                           // 이거 무슨 노래야
  /(?:누가|누구|누굴|어떤\s*가수|무슨\s*가수).{0,14}(?:부른|부르|불렀|노래|곡)/,                                                 // 누가 부른 거야
  /(?:지금|방금|현재).{0,6}(?:듣고|듣는|나오는|흘러나오|들리는|틀어|재생)/,                                                       // 지금 듣는 거 뭐야
  /(?:듣고\s*있는|듣는|나오는|흘러나오는|들리는)\s*(?:거|것|건|게|곡|노래).{0,8}(?:뭐|뭔|누구|누가|제목|가수)/,
  /(?:what(?:'s| is)?\s+(?:this|that|the)\s+(?:song|track|music)|who\s+(?:sings|sang|is\s+singing|made|wrote)|what(?:'s| is)\s+playing|song\s+(?:name|title)|this\s+song)/i,
  /(?:この|今の|今流れてる)\s*(?:曲|歌|音楽)|誰の\s*(?:曲|歌)|誰が\s*歌/,
];

export function asksAboutMusic(text) {
  const t = String(text || '').normalize('NFKC');
  return !!t && PATTERNS.some((re) => re.test(t));
}

// main 이 알려 준 지금 곡 { title, artist, album, app, duration, status, dbTitle, dbArtist, dbAlbum } -> 서버로 보낼 now_playing (곡이 없으면 null)
export function toPayload(s) {
  if (!s || !String(s.title || '').trim()) return null;
  const st = String(s.status || '').toLowerCase();
  return {
    title: s.title, artist: s.artist || '', album: s.album || '', app: s.app || '',
    duration: Math.round(Number(s.duration) || 0),
    state: st === 'playing' ? 'playing' : st === 'paused' ? 'paused' : '',
    db_title: s.dbTitle || '', db_artist: s.dbArtist || '', db_album: s.dbAlbum || '',
  };
}
