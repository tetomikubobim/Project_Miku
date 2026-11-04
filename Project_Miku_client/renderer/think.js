// LLM 응답에서 <think>...</think> (속마음/행동 묘사)를 분리한다.
//
//   <think>*lowers head, cheeks turning slightly red*</think>That's... quite embarrassing...
//   -> spoken: "That's... quite embarrassing..."
//      think:  "*lowers head, cheeks turning slightly red*"
//
// 닫는 태그가 없는 경우(응답이 중간에 끊김)도 끝까지를 think 로 본다.
export function splitThink(raw) {
  const text = String(raw ?? '');
  const thoughts = [];
  const spoken = text
    .replace(/<think>([\s\S]*?)(?:<\/think>|$)/gi, (_m, inner) => {
      const t = inner.trim();
      if (t) thoughts.push(t);
      return '';
    })
    .replace(/[ \t]+\n/g, '\n')
    .trim();
  return { spoken, think: thoughts.join(' ') };
}
