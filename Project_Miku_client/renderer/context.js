// 대화 한 턴에만 AI 에게 같이 보내는 부가 정보 (서버는 그 턴의 시스템 프롬프트에만 붙이고 기록에는 안 남긴다):
//  - 예정된 일정: 사용자가 일정 관리 창에 적어 둔 것 (곧 있을 순서대로 최대 8개)
//  - 클립보드: "방금 복사한 거" 같이 클립보드를 가리키는 말을 했을 때만, 글자 수는 설정(clipboardMaxChars)으로 자른다

const CLIPBOARD_PATTERNS = [
  /클립\s*보드/i,
  /(?:방금|아까|지금|내가|막|좀\s*전에)?\s*복사\s*(?:한|해\s*둔|해둔|했던|했는데|해서|된|한\s*거|한\s*것|한\s*내용|한\s*글|한\s*문장)/,
  /복사(?:본|된\s*(?:거|것|내용|글))/,
  /(?:붙여\s*넣|붙여넣)(?:기|을|으려|려고|은)?.{0,6}(?:거|것|내용|글)/,
  /(?:copied|clipboard|what\s+i\s+(?:just\s+)?copied)/i,
  /コピー(?:した|してた|した\s*(?:の|もの|内容|文))|クリップボード/,
];

export function asksAboutClipboard(text) {
  const t = String(text || '').normalize('NFKC');
  return !!t && CLIPBOARD_PATTERNS.some((re) => re.test(t));
}

export const CLIP_DEFAULT_MAX = 2000;

// 클립보드 글자를 설정한 길이로 자른다. 잘렸으면 끝에 알린다. 내용이 없으면 ''
export function clipText(raw, max = CLIP_DEFAULT_MAX) {
  const t = String(raw || '').replace(/\u0000/g, '').trim();
  if (!t) return '';
  const n = Math.max(100, Math.min(20000, Number(max) || CLIP_DEFAULT_MAX));
  return t.length > n ? `${t.slice(0, n)}\n…(이하 생략, 전체 ${t.length}자 중 ${n}자만 보냄)` : t;
}

// 일정 목록(main 이 준 것) -> 서버로 보낼 모양. fmt 는 { when, left, repeat } 표시 함수
export function schedulePayload(list, now, fmt) {
  return (Array.isArray(list) ? list : [])
    .filter((s) => s && !s.media && s.text && Number.isFinite(s.at) && s.at > now - 60e3)
    .slice(0, 8)
    .map((s) => ({
      when: fmt.when(s.at),
      left: fmt.left(s.at - now),
      repeat: s.repeat ? fmt.repeat(s.repeat, s.weekday) : '',
      text: s.text,
    }));
}
