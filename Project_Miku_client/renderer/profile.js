// 모델별 프로필(persona / 감정 태그) 중 렌더러에서 쓰는 순수 함수.
// 프로필 행 형식: { tag, expression, desc }  (파일 읽기/쓰기는 native/profile.js)

// 감정 태그 -> 표정 이름 (EmotionController.setAliases 에 넘긴다)
export const aliasMap = (rows) => Object.fromEntries((rows || []).map((r) => [r.tag, r.expression]));

// 서버에 보낼 프로필. 모델에 실제로 연결되는 태그만 AI 에게 알려 준다 (표정이 안 나오는 태그를 AI 가 고르지 않게).
// 쓸 수 있는 태그가 하나도 없으면 emotions 를 null 로 보내 서버 기본 감정을 쓰게 한다. neutral 은 서버가 항상 넣는다.
export function serverProfile(model, profile, emotion) {
  const emotions = {};
  for (const r of (profile && profile.rows) || []) {
    if (r.tag !== 'neutral' && emotion && emotion.resolves(r.tag)) emotions[r.tag] = r.desc || '';
  }
  // 음성(GPT-SoVITS 가중치/참조 음성): 적힌 것만 보낸다. 비면 서버 설정을 쓴다
  const voice = {};
  for (const [k, v] of Object.entries((profile && profile.voice) || {})) {
    if (typeof v !== 'string' || !v.trim()) continue;
    if (k === 'speed_factor') { const n = Number(v); if (Number.isFinite(n)) voice[k] = n; continue; }   // 속도는 숫자로
    voice[k] = v.trim();
  }
  return { model, persona: (profile && profile.persona) || '', emotions: Object.keys(emotions).length ? emotions : null, voice: Object.keys(voice).length ? voice : null };
}
