"""SQLite 저장소: 대화 세션과 메시지 (이후 요약/장기 기억 테이블을 같은 파일에 추가)."""
import sqlite3
import threading
import time

LEGACY_TITLE = "이전 대화"


class Database:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    title   TEXT    NOT NULL DEFAULT '',
                    created REAL    NOT NULL,
                    updated REAL    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts      REAL    NOT NULL,
                    role    TEXT    NOT NULL CHECK (role IN ('user','assistant')),
                    content TEXT    NOT NULL,   -- 사람이 읽는 본문 (대사)
                    raw     TEXT,               -- 모델이 낸 원문 ([emotion]<think>..</think>대사)
                    emotion TEXT,
                    kind    TEXT    NOT NULL DEFAULT 'chat'
                );
                CREATE INDEX IF NOT EXISTS idx_messages_id ON messages(id);
                CREATE TABLE IF NOT EXISTS memories (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    text    TEXT    NOT NULL,
                    created REAL    NOT NULL,
                    updated REAL    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS anniversaries (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    month   INTEGER NOT NULL,
                    day     INTEGER NOT NULL,
                    label   TEXT    NOT NULL,
                    year    INTEGER,            -- 시작 연도 (있으면 N주년 계산), 없으면 NULL
                    created REAL    NOT NULL
                );
                """
            )
            self._migrate()
            self.conn.commit()
        self._init_fts()

    def _migrate(self):
        """세션 도입 이전 DB: session_id 열을 추가하고, 기존 메시지는 '이전 대화' 세션 하나로 묶는다. 여러 번 실행해도 안전."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(messages)")}
        if "session_id" not in cols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN session_id INTEGER")
        scols = {r["name"] for r in self.conn.execute("PRAGMA table_info(sessions)")}
        if "summary" not in scols:   # 긴 대화 요약 (오래된 메시지를 줄여서 문맥에 넣는다)
            self.conn.execute("ALTER TABLE sessions ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
        if "summarized_upto" not in scols:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN summarized_upto INTEGER NOT NULL DEFAULT 0")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)")
        row = self.conn.execute("SELECT MIN(ts) a, MAX(ts) b, COUNT(*) n FROM messages WHERE session_id IS NULL").fetchone()
        if row["n"]:
            cur = self.conn.execute(
                "INSERT INTO sessions (title, created, updated) VALUES (?,?,?)", (LEGACY_TITLE, row["a"], row["b"])
            )
            self.conn.execute("UPDATE messages SET session_id=? WHERE session_id IS NULL", (cur.lastrowid,))

    # ----- 세션 -----
    def create_session(self, title=""):
        now = time.time()
        with self.lock:
            cur = self.conn.execute("INSERT INTO sessions (title, created, updated) VALUES (?,?,?)", (title, now, now))
            self.conn.commit()
            return cur.lastrowid

    def session_exists(self, session_id):
        if session_id is None:
            return False
        with self.lock:
            return self.conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() is not None

    def get_session(self, session_id):
        with self.lock:
            r = self.conn.execute("SELECT id, title, created, updated FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(r) if r else None

    def list_sessions(self):
        """최근에 쓴 순서. 메시지 수(count)는 사용자+AI 합산 (화면에 보이지 않는 '먼저 말 걸기' 지시 메시지는 제외)."""
        with self.lock:
            rows = self.conn.execute(
                """SELECT s.id, s.title, s.created, s.updated,
                          COALESCE(SUM(CASE WHEN m.id IS NULL THEN 0
                                            WHEN m.role = 'user' AND m.kind = 'proactive' THEN 0 ELSE 1 END), 0) AS count
                   FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
                   GROUP BY s.id ORDER BY s.updated DESC, s.id DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def rename_session(self, session_id, title):
        with self.lock:
            cur = self.conn.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))
            self.conn.commit()
            return cur.rowcount > 0

    def delete_session(self, session_id):
        """세션과 그 메시지를 지운다. 지웠으면 True."""
        with self.lock:
            self.conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            cur = self.conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def delete_all_sessions(self):
        """모든 세션과 메시지를 지운다. 지운 세션 수를 돌려준다."""
        with self.lock:
            n = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            self.conn.execute("DELETE FROM messages")
            self.conn.execute("DELETE FROM sessions")
            self.conn.commit()
            return n

    # ----- 메시지 -----
    def add(self, role, content, raw=None, emotion=None, kind="chat", session_id=None):
        with self.lock:
            now = time.time()
            cur = self.conn.execute(
                "INSERT INTO messages (ts, role, content, raw, emotion, kind, session_id) VALUES (?,?,?,?,?,?,?)",
                (now, role, content, raw, emotion, kind, session_id),
            )
            if session_id is not None:
                self.conn.execute("UPDATE sessions SET updated=? WHERE id=?", (now, session_id))
            self.conn.commit()
            return cur.lastrowid

    def recent(self, turns, session_id=None, use_raw=True, after_id=0):
        """그 세션의 최근 turns 턴(사용자+AI 한 쌍)을 시간순 [{'role','content'}] 로. 항상 user 로 시작.
        after_id: 이 id 보다 뒤의 메시지만 (요약에 들어간 앞부분을 빼기 위해).
        session_id 가 None 이면 (아직 세션이 없는 새 대화) 빈 목록."""
        if turns <= 0 or session_id is None:
            return []
        with self.lock:
            rows = self.conn.execute(
                "SELECT role, content, raw FROM messages WHERE session_id=? AND id>? ORDER BY id DESC LIMIT ?",
                (session_id, after_id, turns * 2),
            ).fetchall()
        rows = list(reversed(rows))
        while rows and rows[0]["role"] != "user":
            rows.pop(0)
        return [
            {"role": r["role"], "content": (r["raw"] if use_raw and r["role"] == "assistant" and r["raw"] else r["content"])}
            for r in rows
        ]

    def messages(self, session_id, limit=200):
        """화면에 보여 줄 기록 (오래된 것부터, 최근 limit 개)."""
        if session_id is None:
            return []
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, ts, role, content, emotion, kind FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ----- 요약 -----
    def get_summary(self, session_id):
        """(요약 글, 요약에 들어간 마지막 메시지 id). 없으면 ('', 0)."""
        if session_id is None:
            return "", 0
        with self.lock:
            r = self.conn.execute("SELECT summary, summarized_upto FROM sessions WHERE id=?", (session_id,)).fetchone()
        return (r["summary"], r["summarized_upto"]) if r else ("", 0)

    def set_summary(self, session_id, summary, upto):
        with self.lock:
            self.conn.execute("UPDATE sessions SET summary=?, summarized_upto=? WHERE id=?", (summary, upto, session_id))
            self.conn.commit()

    def messages_after(self, session_id, after_id=0):
        """after_id 뒤의 메시지 전부 (오래된 것부터). 요약/기억 추출용: 숨김 지시문(proactive)은 뺀다."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, role, content, kind FROM messages WHERE session_id=? AND id>? ORDER BY id", (session_id, after_id)
            ).fetchall()
        return [dict(r) for r in rows if not (r["role"] == "user" and r["kind"] == "proactive")]

    def all_messages(self, session_id):
        """내보내기용: 한 대화의 모든 메시지 (제한 없음)."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, ts, role, content, emotion, kind FROM messages WHERE session_id=? ORDER BY id", (session_id,)
            ).fetchall()
        return [dict(r) for r in rows if not (r["role"] == "user" and r["kind"] == "proactive")]

    # ----- 검색 -----
    def search(self, query, limit=30):
        """대화 제목과 메시지 본문에서 찾는다. 대화별로 가장 최근에 맞은 메시지 하나씩, 최근 대화 순."""
        q = (query or "").strip()
        if not q:
            return []
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self.lock:
            rows = self.conn.execute(
                """SELECT s.id AS session_id, s.title, s.updated,
                          (SELECT m.id FROM messages m WHERE m.session_id = s.id AND m.content LIKE ? ESCAPE '\\'
                             AND NOT (m.role = 'user' AND m.kind = 'proactive') ORDER BY m.id DESC LIMIT 1) AS message_id
                   FROM sessions s
                   WHERE s.title LIKE ? ESCAPE '\\' OR message_id IS NOT NULL
                   ORDER BY s.updated DESC LIMIT ?""",
                (like, like, limit),
            ).fetchall()
            out = []
            for r in rows:
                snippet, role = "", ""
                if r["message_id"] is not None:
                    m = self.conn.execute("SELECT content, role FROM messages WHERE id=?", (r["message_id"],)).fetchone()
                    snippet, role = m["content"], m["role"]
                out.append({"session_id": r["session_id"], "title": r["title"], "updated": r["updated"],
                            "message_id": r["message_id"], "role": role, "snippet": snippet})
        return out

    # ----- 과거 대화 회상 (장기 기억 검색) -----
    def _init_fts(self):
        """메시지 본문 전문 검색(FTS5 trigram). 이 SQLite 가 못 쓰면 LIKE 검색으로 대신한다 (self.fts=False)."""
        self.fts = False
        try:
            with self.lock:
                have = self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='messages_fts'").fetchone()
                self.conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='id', tokenize='trigram');
                    CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
                        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF content ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
                        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                    END;
                    """
                )
                if not have:   # 처음 만든 거면 기존 메시지를 색인에 넣는다
                    self.conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
                self.conn.commit()
            self.fts = True
        except sqlite3.Error as e:   # trigram 토크나이저가 없는 오래된 SQLite 등
            print(f"[db] 전문 검색(FTS5)을 못 써서 LIKE 로 대신해요: {e}")

    def recent_floor_id(self, turns, session_id, after_id=0):
        """_history 가 문맥으로 넣는 가장 오래된 메시지 id (회상에서 같은 내용을 또 넣지 않으려고). 없으면 None."""
        if session_id is None or turns <= 0:
            return None
        with self.lock:
            rows = self.conn.execute(
                "SELECT id FROM messages WHERE session_id=? AND id>? ORDER BY id DESC LIMIT ?", (session_id, after_id, turns * 2)
            ).fetchall()
        return rows[-1]["id"] if rows else None

    def recall(self, tokens, limit=4, skip_session=None, skip_from_id=None):
        """tokens(검색어 묶음 목록: 각 묶음은 같은 말의 변형들, 문자열 하나도 가능)가 많이 들어 있는 과거 메시지를 점수순으로.
        [{'id','ts','role','content','score'}]. 점수 = 들어 있는 묶음마다 가장 긴 변형의 글자 수 합.
        skip_session/skip_from_id: 이 대화의 이 id 이후(=지금 문맥에 이미 들어 있는 것)는 뺀다."""
        groups = [[g] if isinstance(g, str) else [v for v in g if v] for g in tokens]
        groups = [g for g in groups if g]
        flat = list(dict.fromkeys(v for g in groups for v in g))
        if not flat or limit <= 0:
            return []
        cand = {}
        esc = lambda t: t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        base = "SELECT id, ts, role, content, session_id FROM messages WHERE kind='chat'"
        with self.lock:
            longs = [t for t in flat if len(t) >= 3]
            if self.fts and longs:
                q = " OR ".join('"' + t.replace('"', '""') + '"' for t in longs)
                try:
                    for r in self.conn.execute(
                        "SELECT m.id, m.ts, m.role, m.content, m.session_id FROM messages_fts f JOIN messages m ON m.id=f.rowid "
                        "WHERE messages_fts MATCH ? AND m.kind='chat' ORDER BY rank LIMIT 200", (q,)
                    ):
                        cand[r["id"]] = r
                except sqlite3.Error:
                    pass
            for t in ([t for t in flat if len(t) < 3] if (self.fts and longs) else flat):
                for r in self.conn.execute(base + " AND content LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 200", ("%" + esc(t) + "%",)):
                    cand[r["id"]] = r
        out = []
        for r in cand.values():
            if skip_session is not None and skip_from_id is not None and r["session_id"] == skip_session and r["id"] >= skip_from_id:
                continue
            text = r["content"]
            score = sum(max((len(v) for v in g if v in text), default=0) for g in groups)
            if score:
                out.append({"id": r["id"], "ts": r["ts"], "role": r["role"], "content": text, "score": score})
        out.sort(key=lambda x: (-x["score"], -x["id"]))
        return out[:limit]

    # ----- 기념일 -----
    def list_anniversaries(self):
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT id, month, day, label, year FROM anniversaries ORDER BY month, day, id")]

    def add_anniversary(self, month, day, label, year=None):
        """같은 날짜/이름이 이미 있으면 새로 만들지 않고 그 id 를 돌려준다."""
        with self.lock:
            r = self.conn.execute("SELECT id FROM anniversaries WHERE month=? AND day=? AND label=?", (month, day, label)).fetchone()
            if r:
                return r["id"]
            cur = self.conn.execute(
                "INSERT INTO anniversaries (month, day, label, year, created) VALUES (?,?,?,?,?)", (month, day, label, year, time.time())
            )
            self.conn.commit()
            return cur.lastrowid

    def delete_anniversary(self, ann_id):
        with self.lock:
            cur = self.conn.execute("DELETE FROM anniversaries WHERE id=?", (ann_id,))
            self.conn.commit()
            return cur.rowcount > 0

    # ----- 장기 기억 -----
    def list_memories(self):
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT id, text, created, updated FROM memories ORDER BY id")]

    def add_memory(self, text):
        now = time.time()
        with self.lock:
            cur = self.conn.execute("INSERT INTO memories (text, created, updated) VALUES (?,?,?)", (text, now, now))
            self.conn.commit()
            return cur.lastrowid

    def update_memory(self, memory_id, text):
        with self.lock:
            cur = self.conn.execute("UPDATE memories SET text=?, updated=? WHERE id=?", (text, time.time(), memory_id))
            self.conn.commit()
            return cur.rowcount > 0

    def delete_memory(self, memory_id):
        with self.lock:
            cur = self.conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def delete_all_memories(self):
        with self.lock:
            n = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            self.conn.execute("DELETE FROM memories")
            self.conn.commit()
            return n

    def kv_get(self, key, default=None):
        with self.lock:
            r = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def kv_set(self, key, value):
        with self.lock:
            self.conn.execute("INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
            self.conn.commit()

    def last_ts(self, session_id):
        """그 세션의 마지막 메시지 시각 (없으면 None)."""
        if session_id is None:
            return None
        with self.lock:
            return self.conn.execute("SELECT MAX(ts) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0]

    def count(self, session_id=None):
        with self.lock:
            if session_id is None:
                return self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            return self.conn.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0]

    def close(self):
        with self.lock:
            self.conn.close()
