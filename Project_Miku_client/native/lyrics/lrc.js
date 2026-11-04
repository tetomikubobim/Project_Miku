// LRC / 알송 가사 해석: "[mm:ss.xx]글자" 줄들 -> 시간순 줄 목록.
//   parseLrc(raw) -> { lines: [{ t(초), text, subs: [글자...] }] } | null (싱크 정보가 없거나 쓸 만한 줄이 모자라면)
//   - 줄바꿈은 \n, \r\n, <br> 모두 인정 (알송은 <br> 로 줄을 나누기도 한다)
//   - 같은 시각에 줄이 여러 개면(원문 + 번역) 첫 줄이 text, 나머지는 subs
//   - 글자가 빈 줄([00:12.30] 만 있는 줄)은 '쉼' 표시로 남긴다 (노래가 끝난 자리라서 입 움직임/표시를 멈추는 데 쓴다)
const TAG = /\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;

function tagSeconds(m) {
  const frac = m[3] == null ? 0 : m[3].length === 1 ? Number(m[3]) / 10 : m[3].length === 2 ? Number(m[3]) / 100 : Number(m[3]) / 1000;
  return Number(m[1]) * 60 + Number(m[2]) + frac;
}

function parseLrc(raw) {
  const text = String(raw == null ? '' : raw);
  const entries = [];
  let order = 0;
  for (const rawLine of text.split(/\r?\n|<br\s*\/?>/i)) {
    const tags = [...rawLine.matchAll(TAG)];
    if (!tags.length) continue;
    const body = rawLine.replace(TAG, '').replace(/\s+/g, ' ').trim();
    for (const m of tags) entries.push({ t: tagSeconds(m), text: body, order: order++ });
  }
  if (!entries.length) return null;
  entries.sort((a, b) => a.t - b.t || a.order - b.order);

  const lines = [];
  for (const e of entries) {
    const last = lines[lines.length - 1];
    if (last && Math.abs(last.t - e.t) < 0.005) {
      if (!e.text) continue;
      if (!last.text) { last.text = e.text; continue; }
      if (e.text !== last.text && !last.subs.includes(e.text)) last.subs.push(e.text);
      continue;
    }
    lines.push({ t: Math.round(e.t * 1000) / 1000, text: e.text, subs: [] });
  }
  if (lines.filter((l) => l.text).length < 2) return null;
  return { lines };
}

module.exports = { parseLrc };
