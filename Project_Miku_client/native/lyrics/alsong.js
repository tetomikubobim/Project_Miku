// 알송(ALSong) 가사 서버 조회. 비공식 SOAP 프로토콜이라 서버가 바꾸면 안 될 수 있다 (그래서 LRCLIB 이 뒤를 받친다).
// 요청 형식과 encData 상수는 오픈소스 프로젝트를 참고했다:
//   - medAndro/lyrs-eggsong (Apache-2.0), BayernMuller/alsong-time-synced-lyrics
// 개인 사용 범위로만 쓰세요.
//
//   search(title, artist, opts) -> [{ id, title, artist, album }]
//   fetchLyric(item, opts)      -> { id, title, artist, album, playtime(초, 없으면 0), lyricRaw }
//   opts: { fetchImpl, timeoutMs, log }
const ENDPOINT = 'http://lyrics.alsong.co.kr/alsongwebservice/service1.asmx';
const USER_AGENT = 'gSOAP/2.7';
const ENC_A = '7c2d15b8f51ac2f3b2a37d7a445c3158455defb8a58d621eb77a3ff8ae4921318e49cefe24e515f79892a4c29c9a3e204358698c1cfe79c151c04f9561e945096ccd1d1c0a8d8f265a2f3fa7995939b21d8f663b246bbc433c7589da7e68047524b80e16f9671b6ea0faaf9d6cde1b7dbcf1b89aa8a1d67a8bbc566664342e12';
const ENC_B = 'a8e4e6dbcd813026f6d2d832852fb72295b3862e098b213c581e9d7d5364bc58ed85e0b95304597c77fa2dfaacab73b044ebe0f63d304f902357b407affab92ed6a4600b36b767cd2e9d70ad9502f79ecc2e9941c1cc92c832dc6f1826b627f32d37fd51400a6fd2f6b396c399ffa08ed90938d484fcf50ad351f9d3d8860c2c';

// 보낼 글자가 XML 을 깨지 않게 CDATA 로 감싼다 (안에 ]]> 가 있으면 쪼갠다). 제어 문자는 뺀다.
const cdata = (v) => `<![CDATA[${String(v == null ? '' : v).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').replace(/\]\]>/g, ']]]]><![CDATA[>')}]]>`;
const paramsXml = (params, prefix = '') => Object.entries(params).map(([k, v]) => `<${prefix}${k}>${cdata(v)}</${prefix}${k}>`).join('');

// 서버가 받아 주는 봉투 형태가 몇 가지 있어서(버전에 따라 다름) 차례로 시도하고, 통한 것을 기억한다.
const DIALECTS = [
  {
    name: 'soap11',
    contentType: 'text/xml; charset=utf-8',
    soapAction: (a) => `ALSongWebServer/${a}`,
    build: (a, p) => `<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><${a} xmlns="ALSongWebServer"><encData>${ENC_A}</encData>${paramsXml(p)}</${a}></soap:Body></soap:Envelope>`,
  },
  {
    name: 'soap11-service',
    contentType: 'text/xml; charset=utf-8',
    soapAction: (a) => `ALSongWebService/${a}`,
    build: (a, p) => `<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><${a} xmlns="ALSongWebService"><encData>${ENC_A}</encData>${paramsXml(p)}</${a}></soap:Body></soap:Envelope>`,
  },
  {
    name: 'soap12-gsoap',
    contentType: 'application/soap+xml; charset=utf-8',
    soapAction: (a) => `ALSongWebServer/${a}`,
    build: (a, p) => `<?xml version="1.0" encoding="UTF-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:SOAP-ENC="http://www.w3.org/2003/05/soap-encoding" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:ns2="ALSongWebServer/Service1Soap" xmlns:ns1="ALSongWebServer" xmlns:ns3="ALSongWebServer/Service1Soap12"><SOAP-ENV:Body><ns1:${a}><ns1:encData>${ENC_B}</ns1:encData>${paramsXml(p, 'ns1:')}</ns1:${a}></SOAP-ENV:Body></SOAP-ENV:Envelope>`,
  },
];
let preferred = 0;   // 마지막으로 통한 형태

function decodeXml(s) {
  return String(s)
    .replace(/^\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*$/, '$1')
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(parseInt(d, 10)))
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, '&');
}

// <태그>값</태그> 의 첫 값 (네임스페이스 접두사는 무시). 없으면 ''
function tag(xml, name) {
  const m = new RegExp(`<(?:[\\w-]+:)?${name}(?:\\s[^>]*)?>([\\s\\S]*?)</(?:[\\w-]+:)?${name}>`).exec(xml);
  return m ? decodeXml(m[1]).trim() : '';
}

function parseSearchList(xml) {
  const out = [];
  const re = /<(?:[\w-]+:)?ST_SEARCHLYRIC_LIST(?:\s[^>]*)?>([\s\S]*?)<\/(?:[\w-]+:)?ST_SEARCHLYRIC_LIST>/g;
  let m;
  while ((m = re.exec(xml))) {
    const id = tag(m[1], 'lyricID');
    if (id) out.push({ id, title: tag(m[1], 'title'), artist: tag(m[1], 'artist'), album: tag(m[1], 'album') });
  }
  return out;
}

function parseDetail(xml) {
  // lyric 은 <lyric>...</lyric>. <lyricID> 와 헷갈리지 않게 tag() 가 정확히 이름이 같은 것만 잡는다.
  const lyricRaw = tag(xml, 'lyric');
  if (!lyricRaw) return null;
  return {
    id: tag(xml, 'lyricID'),
    title: tag(xml, 'title'),
    artist: tag(xml, 'artist'),
    album: tag(xml, 'album'),
    playtime: Number.parseInt(tag(xml, 'playtime'), 10) || 0,
    lyricRaw,
  };
}

async function soap(action, params, opts = {}) {
  const doFetch = opts.fetchImpl || globalThis.fetch;
  const timeoutMs = opts.timeoutMs || 12000;
  let lastErr = null;
  for (let k = 0; k < DIALECTS.length; k++) {
    const idx = (preferred + k) % DIALECTS.length;
    const d = DIALECTS[idx];
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await doFetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': d.contentType, 'User-Agent': USER_AGENT, SOAPAction: d.soapAction(action) },
        body: d.build(action, params),
        signal: ctl.signal,
      });
      const text = await res.text();
      if (!res.ok || /<(?:[\w-]+:)?Fault[\s>]/.test(text)) { lastErr = new Error(`알송 응답 ${res.status}${/Fault/.test(text) ? ' (SOAP Fault)' : ''}`); continue; }
      preferred = idx;
      return text;
    } catch (e) {
      lastErr = e && e.name === 'AbortError' ? new Error('알송 응답 시간 초과') : e;
      if (e && e.name === 'AbortError') break;   // 서버가 느린 거라 다른 형태로 또 기다리지 않는다
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr || new Error('알송 요청 실패');
}

async function search(title, artist, opts = {}) {
  const xml = await soap('GetResembleLyricList2', { title: title || '', artist: artist || '', pageNo: '1' }, opts);
  return parseSearchList(xml);
}

async function fetchLyric(item, opts = {}) {
  const xml = await soap('GetLyricByID2', { lyricID: item.id }, opts);
  const d = parseDetail(xml);
  if (!d) return null;
  return { ...d, id: d.id || item.id, title: d.title || item.title, artist: d.artist || item.artist, album: d.album || item.album };
}

const provider = { name: 'alsong', search, fetchLyric };
module.exports = { provider, search, fetchLyric, parseSearchList, parseDetail, tag, decodeXml, DIALECTS };
