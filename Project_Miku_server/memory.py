"""장기 기억과 대화 요약.

- 기억(memories 표): 사용자에 대해 오래 가져갈 사실(이름, 취향, 약속, 있었던 일). 대화가 몇 번 오갈 때마다 LLM 이 새 대화에서 뽑아
  저장하고, 매 대화의 시스템 프롬프트에 넣는다. 설정 창의 '기억' 탭에서 보고/고치고/지울 수 있다.
- 요약(sessions.summary): 한 대화가 길어지면 오래된 메시지를 요약으로 줄여서 문맥에 넣는다 (최근 메시지는 원문 그대로).

LLM 호출은 응답을 돌려준 뒤 백그라운드 스레드에서 한다 (대화 속도에 영향 없음). 실패해도 대화는 계속된다.
config.yaml:
  memory:
    enabled: true        # 기본 켜짐
    extract_every: 6     # 사용자 메시지가 이만큼 쌓일 때마다 기억 추출 (0 이면 추출 안 함)
    max_items: 60        # 저장/프롬프트에 쓰는 기억 최대 개수 (넘으면 오래된 것부터 지움)
    summary: true        # 긴 대화 요약
    keep_turns: 20       # 요약이 켜져 있을 때 원문 그대로 남기는 최근 턴 수
    summary_batch: 8     # 원문 창을 넘은 메시지가 이만큼 쌓이면 요약에 합친다
    consolidate: true    # max_items 를 넘으면 오래된 것을 지우지 않고 LLM 이 비슷한 기억을 합쳐서 정리한다
"""
import json
import re

DEFAULTS = {"enabled": True, "extract_every": 6, "max_items": 60, "summary": True, "keep_turns": 20, "summary_batch": 8, "consolidate": True}
CONSOLIDATE_COOLDOWN = 1800   # 합치기가 실패하거나 모자랄 때 다시 시도하기까지 기다리는 초 (매 대화마다 LLM 을 부르지 않으려고)
MAX_DELETE_RATIO = 0.2        # 한 번의 정리에서 LLM 이 그냥 지울 수 있는 비율 (나머지는 합치기만 허용)
MAX_MEMORY_CHARS = 200


def settings(cfg):
    m = dict(DEFAULTS)
    m.update({k: v for k, v in (cfg.get("memory") or {}).items() if v is not None})
    return m


def enabled(cfg):
    return settings(cfg)["enabled"] is True


def clean_text(t):
    return " ".join(str(t or "").split())[:MAX_MEMORY_CHARS]


def prompt_block(memories, limit):
    """시스템 프롬프트에 넣을 기억 목록 (없으면 빈 글자)."""
    items = [clean_text(m["text"]) for m in memories if clean_text(m["text"])][-limit:] if limit > 0 else []
    if not items:
        return ""
    return "[기억하고 있는 것] (대화 상대에 대해 알고 있는 사실입니다. 자연스러울 때만 언급하고, 억지로 꺼내지 마세요.)\n" + "\n".join(f"- {t}" for t in items)


def summary_block(summary):
    s = (summary or "").strip()
    return f"[이 대화의 앞부분 요약]\n{s}" if s else ""


def _transcript(messages, who, character):
    lines = []
    for m in messages:
        name = who if m["role"] == "user" else character
        lines.append(f"{name}: {' '.join(str(m['content']).split())}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 기억 추출
def extraction_prompt(existing, messages, who, character, anniversaries=None):
    cur = "\n".join(f"{m['id']}: {m['text']}" for m in existing) or "(없음)"
    ann = "\n".join(f"{a['month']}월 {a['day']}일 {a['label']}" for a in (anniversaries or [])) or "(없음)"
    return (
        "당신은 대화에서 '오래 기억할 가치가 있는 사실'만 골라 정리하는 기록 담당자입니다.\n\n"
        f"[지금까지 저장된 기억] (번호: 내용)\n{cur}\n\n"
        f"[저장된 기념일]\n{ann}\n\n"
        f"[새 대화]\n{_transcript(messages, who, character)}\n\n"
        f"규칙:\n"
        f"- {who} 에 대한 사실만 적습니다: 이름/호칭, 나이·직업 같은 신상, 취향, 습관, 계획·약속, 중요한 사건, 관계, 싫어하는 것.\n"
        f"- 한 항목은 한 문장, 짧게. 예: \"{who}는 매운 음식을 좋아한다\".\n"
        "- 인사, 잡담, 일시적인 기분, 이미 저장된 내용의 반복은 적지 않습니다.\n"
        "- 저장된 기억과 겹치면 새로 추가하지 말고, 내용이 바뀌었으면 update, 더 이상 사실이 아니면 delete 합니다.\n"
        f"- {who} 가 자신의 생일, 사귄 날, 기념일처럼 해마다 챙길 날짜를 직접 말했고 아직 저장돼 있지 않으면 anniversaries 에 적습니다 (month, day 는 숫자, label 은 짧게, 시작 연도를 말했으면 year). 일회성 약속은 적지 않습니다.\n"
        "- 아무 변화가 없으면 모두 빈 목록으로 답합니다.\n\n"
        '반드시 아래 JSON 한 덩어리로만 답하세요 (설명, 마크다운 금지):\n'
        '{"add": ["새 기억", ...], "update": [{"id": 번호, "text": "고친 내용"}], "delete": [번호, ...], "anniversaries": [{"month": 3, "day": 5, "label": "생일", "year": null}]}'
    )


def parse_ops(text):
    """LLM 답에서 {'add': [...], 'update': [...], 'delete': [...]} 를 꺼낸다. 못 읽으면 빈 변경."""
    empty = {"add": [], "update": [], "delete": [], "anniversaries": []}
    t = re.sub(r"```(?:json)?", "", text or "").strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return empty
    try:
        j = json.loads(t[a:b + 1])
    except ValueError:
        return empty
    if not isinstance(j, dict):
        return empty
    as_list = lambda v: v if isinstance(v, list) else []
    out = dict(empty)
    out["add"] = [clean_text(x) for x in as_list(j.get("add")) if isinstance(x, str) and clean_text(x)]
    for u in as_list(j.get("update")):
        if isinstance(u, dict) and isinstance(u.get("id"), int) and clean_text(u.get("text")):
            out["update"].append({"id": u["id"], "text": clean_text(u["text"])})
    out["delete"] = [i for i in as_list(j.get("delete")) if isinstance(i, int)]
    out["anniversaries"] = [a for a in (clean_anniversary(x) for x in as_list(j.get("anniversaries"))) if a]
    return out


def clean_anniversary(a):
    """{month, day, label, year?} 를 검증해서 정리한다. 올바르지 않으면 None."""
    if not isinstance(a, dict):
        return None
    try:
        m, d = int(a.get("month")), int(a.get("day"))
    except (TypeError, ValueError):
        return None
    label = clean_text(a.get("label"))[:40]
    if not (1 <= m <= 12 and 1 <= d <= 31) or not label:
        return None
    if d > [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]:
        return None
    y = a.get("year")
    y = y if isinstance(y, int) and 1900 <= y <= 2100 else None
    return {"month": m, "day": d, "label": label, "year": y}


def hard_cap(max_items):
    """합치기가 계속 실패해도 기억이 끝없이 늘지 않게 하는 마지막 안전장치 (이 개수를 넘으면 그제서야 오래된 것부터 지운다)."""
    return max(max_items + 1, int(max_items * 1.5))


def apply_ops(db, ops, max_items):
    """변경을 DB 에 반영한다. 반영한 개수를 돌려준다. 같은 내용은 중복 저장하지 않는다.
    max_items 를 조금 넘는 건 그대로 둔다 (곧 LLM 이 합쳐서 정리한다). hard_cap 을 넘으면 오래된 것부터 지운다."""
    changed = 0
    for i in ops["delete"]:
        changed += db.delete_memory(i)
    for u in ops["update"]:
        changed += db.update_memory(u["id"], u["text"])
    for a in ops.get("anniversaries", []):
        changed += 1 if db.add_anniversary(a["month"], a["day"], a["label"], a.get("year")) else 0
    have = {clean_text(m["text"]) for m in db.list_memories()}
    for t in ops["add"]:
        if t not in have:
            db.add_memory(t)
            have.add(t)
            changed += 1
    cap = hard_cap(max_items)
    extra = len(db.list_memories()) - cap
    if max_items > 0 and extra > 0:   # 안전장치: 합치기로도 못 줄였을 때만 오래된 것부터
        for m in db.list_memories()[:extra]:
            db.delete_memory(m["id"])
    return changed


# ---------------------------------------------------------------- 넘칠 때 합치기
def consolidation_prompt(memories, target, who):
    cur = "\n".join(f"{m['id']}: {clean_text(m['text'])}" for m in memories)
    return (
        f"당신은 {who} 에 대해 저장된 '기억 목록'을 정리하는 담당자입니다. 지금 {len(memories)}개인데 {target}개 이하로 줄여야 합니다.\n\n"
        f"[기억 목록] (번호: 내용)\n{cur}\n\n"
        "규칙:\n"
        "- 같은 주제나 비슷한 내용(예: 음식 취향 여러 개, 같은 사람/일에 대한 여러 사실)은 하나의 문장으로 합칩니다. 합칠 때 구체적인 정보(이름, 날짜, 숫자)는 빼먹지 않습니다.\n"
        "- 사실이 겹치거나 이미 더 최신 내용으로 바뀐 것, 지금은 의미 없는 일회성 정보만 삭제합니다. 삭제는 꼭 필요한 것에만, 최소한으로.\n"
        "- 이름, 가족/친구 관계, 건강, 중요한 약속이나 기념일처럼 오래 남아야 할 것은 지우지 말고 보존합니다.\n"
        "- 합친 문장은 한 문장, 200자 이내.\n\n"
        '반드시 아래 JSON 한 덩어리로만 답하세요 (설명, 마크다운 금지):\n'
        '{"merge": [{"ids": [합칠 번호들], "text": "합친 한 문장"}], "delete": [지울 번호들]}'
    )


def parse_consolidation(text):
    empty = {"merge": [], "delete": []}
    t = re.sub(r"```(?:json)?", "", text or "").strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return empty
    try:
        j = json.loads(t[a:b + 1])
    except ValueError:
        return empty
    if not isinstance(j, dict):
        return empty
    as_list = lambda v: v if isinstance(v, list) else []
    out = dict(empty)
    for g in as_list(j.get("merge")):
        if isinstance(g, dict) and clean_text(g.get("text")):
            ids = [i for i in as_list(g.get("ids")) if isinstance(i, int) and not isinstance(i, bool)]
            out["merge"].append({"ids": ids, "text": clean_text(g["text"])})
    out["delete"] = [i for i in as_list(j.get("delete")) if isinstance(i, int) and not isinstance(i, bool)]
    return out


def apply_consolidation(db, plan):
    """합치기/삭제를 검증해서 반영한다. 돌려주는 값: (합쳐서 줄어든 개수, 삭제한 개수).
    없는 번호, 겹치는 번호, 1개짜리 합치기는 무시하고, 그냥 지우는 건 전체의 MAX_DELETE_RATIO 까지만 허용한다
    (LLM 이 한꺼번에 너무 많이 지워도 기억을 잃지 않게)."""
    have = {m["id"] for m in db.list_memories()}
    used, reduced, deleted = set(), 0, 0
    for g in plan["merge"]:
        ids = sorted({i for i in g["ids"] if i in have and i not in used})
        if len(ids) < 2:
            continue
        db.update_memory(ids[0], g["text"])   # 가장 오래된 번호에 합쳐 둔다 (순서 유지)
        for i in ids[1:]:
            db.delete_memory(i)
        used.update(ids)
        reduced += len(ids) - 1
    allowed = int(len(have) * MAX_DELETE_RATIO)
    for i in plan["delete"]:
        if deleted >= allowed:
            break
        if i in have and i not in used:
            deleted += db.delete_memory(i)
            used.add(i)
    return reduced, deleted


# ---------------------------------------------------------------- 요약
def summary_prompt(old_summary, messages, who, character):
    old = (old_summary or "").strip() or "(없음)"
    return (
        "아래는 두 사람의 대화입니다. 앞부분 요약과 새로 오간 대화를 합쳐, 이어서 대화하는 데 필요한 내용만 남긴 새 요약을 만드세요.\n\n"
        f"[지금까지의 요약]\n{old}\n\n[새로 오간 대화]\n{_transcript(messages, who, character)}\n\n"
        "규칙: 한국어로 8문장 이내. 오간 이야기의 흐름, 정해진 것, 아직 답이 안 난 질문, 분위기를 적습니다. "
        "말투를 흉내 내지 말고 3인칭으로 담백하게. 요약 본문만 답하세요."
    )
