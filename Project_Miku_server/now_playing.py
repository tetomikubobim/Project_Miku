"""지금 사용자가 재생 중인 곡 정보를 이번 대화 턴에만 시스템 프롬프트에 붙인다.

클라이언트는 사용자가 곡에 대해 물은 것 같을 때만(이 노래 누가 불렀어? 등) 메시지에 now_playing 을 실어 보낸다.
기록(DB)에는 남기지 않고, 곡이 바뀔 때마다 받지도 않는다. 값은 사용자 PC의 재생기가 알려 준 것이라 길이/글자만 다듬어서 쓴다.
"""
import re

MAX = 120
BROWSER_RE = re.compile(r"chrome|msedge|edge|firefox|brave|opera|vivaldi|whale|browser|iexplore|safari", re.I)
STATES = {"playing": "재생 중", "paused": "일시정지 중"}


def _s(v, n=MAX):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def sanitize(raw):
    """클라이언트가 보낸 값 -> 정리된 dict. 제목이 없으면 None (곡 정보가 없는 걸로 본다)."""
    if not isinstance(raw, dict):
        return None
    out = {k: _s(raw.get(k)) for k in ("title", "artist", "album", "app", "state", "db_title", "db_artist", "db_album")}
    if not out["title"]:
        return None
    try:
        out["duration"] = max(0, min(int(raw.get("duration") or 0), 24 * 3600))
    except (TypeError, ValueError):
        out["duration"] = 0
    return out


def block(np):
    """시스템 프롬프트에 붙일 안내 글. np 는 sanitize 한 값 (없으면 '')."""
    if not np:
        return ""
    lines = [
        "[지금 사용자가 재생 중인 곡]",
        "사용자가 지금 듣고 있는 곡에 대해 물을 때만 참고하세요. 묻지 않았으면 먼저 꺼내지 않아도 됩니다.",
        "곡은 중간에 바뀔 수 있어요. 이전 대화에서 얘기한 곡과 아래 정보가 다르면, 항상 아래 정보(지금 곡)를 기준으로 답하세요.",
        f"- 제목: {np['title']}",
    ]
    if np.get("artist"):
        lines.append(f"- 가수: {np['artist']}")
    if np.get("album"):
        lines.append(f"- 앨범: {np['album']}")
    if np.get("app"):
        lines.append(f"- 재생 앱: {np['app']}")
    if np.get("duration"):
        lines.append(f"- 길이: 약 {np['duration'] // 60}분 {np['duration'] % 60}초")
    if STATES.get(np.get("state")):
        lines.append(f"- 상태: {STATES[np['state']]}")
    if np.get("db_title") or np.get("db_artist"):
        same = " - ".join(x for x in (np.get("db_artist"), np.get("db_title")) if x)
        lines.append(f"- 가사 DB에서 찾은 같은 곡 정보(참고, 표기가 다를 수 있음): {same}")
    if BROWSER_RE.search(np.get("app", "")):
        lines.append("주의: 브라우저(유튜브 등)에서 재생 중이라 '가수'가 실제 가수가 아니라 채널 이름일 수 있어요. 확실하지 않으면 그렇게 말하세요.")
    lines.append("이 정보에 없는 것(작곡가, 곡 배경 등)은 아는 만큼만 말하고, 모르면 모른다고 하세요. 지어내지 마세요.")
    return "\n".join(lines)
