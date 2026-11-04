"""config.yaml 을 주석/순서/들여쓰기를 그대로 둔 채 값만 고친다 (ruamel.yaml 없이).

- 고치는 대상은 `a.b.c` 같은 경로의 단순 값(글자/숫자/참거짓)이다. 이미 있는 줄은 값만 바꾸고 줄 끝 주석은 그대로 둔다 (주석 위치도 맞춘다).
- 없는 항목은 부모 블록 끝에 새 줄로 넣는다. 부모도 없으면 만든다.
- 저장하기 전에 PyYAML 로 다시 읽어서 "요청한 값만 바뀌고 나머지는 그대로"인지 확인한다. 하나라도 어긋나면 아무것도 쓰지 않는다.
- 저장은 임시 파일 -> 교체(원자적)이고, 직전 내용은 config.yaml.bak 으로 남긴다.
- 지원하지 않는 모양({ ... } 로 적은 값 안쪽, 여러 줄 글자 등)을 고쳐야 하면 PatchError 로 알린다 (손으로 고쳐 달라는 뜻).
"""
import copy
import json
import os
import re
import time

import yaml


class PatchError(Exception):
    """사용자에게 그대로 보여 줘도 되는 메시지."""


class ConflictError(PatchError):
    """읽은 뒤에 파일이 다른 곳에서 바뀌었다."""


_KEY_RE = re.compile(r"""^(?P<indent> *)(?P<key>"[^"]*"|'[^']*'|[A-Za-z0-9_.\-가-힣]+)[ \t]*:(?=[ \t]|$)(?P<rest>.*)$""")


def _unquote_key(k):
    return k[1:-1] if k[:1] in ("'", '"') and k[-1:] == k[:1] and len(k) >= 2 else k


def _scan_value(rest):
    """콜론 뒤 문자열에서 (앞 공백, 값 토큰, 뒤 나머지[공백+주석]) 을 나눈다. 값이 없으면 토큰은 ''."""
    m = re.match(r"[ \t]*", rest)
    lead = m.group(0)
    body = rest[len(lead):]
    if not body or body.startswith("#"):
        return lead, "", body
    if body[0] == '"':
        i = 1
        while i < len(body):
            if body[i] == "\\":
                i += 2
                continue
            if body[i] == '"':
                break
            i += 1
        else:
            raise PatchError("닫는 따옴표가 없는 줄이 있어요")
        end = i + 1
    elif body[0] == "'":
        i = 1
        while i < len(body):
            if body[i] == "'":
                if body[i + 1:i + 2] == "'":
                    i += 2
                    continue
                break
            i += 1
        else:
            raise PatchError("닫는 따옴표가 없는 줄이 있어요")
        end = i + 1
    elif body[0] in "{[":
        depth, i, q = 0, 0, None
        while i < len(body):
            c = body[i]
            if q:
                if c == "\\" and q == '"':
                    i += 1
                elif c == q:
                    q = None
            elif c in "\"'":
                q = c
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        end = i + 1
    else:
        m2 = re.search(r"[ \t]+#", body)
        end = m2.start() if m2 else len(body)
        body_stripped = body[:end].rstrip()
        end = len(body_stripped)
    tail = body[end:]
    if tail.strip() and not tail.lstrip().startswith("#"):
        raise PatchError(f"이해할 수 없는 줄이에요: {rest.strip()[:60]}")
    return lead, body[:end], tail


def dump_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise PatchError("숫자가 올바르지 않아요")
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)   # JSON 글자는 YAML 쌍따옴표 글자로도 유효하다 ("no" 같은 값도 안전)
    raise PatchError(f"단순 값만 저장할 수 있어요: {type(v).__name__}")


class _Doc:
    def __init__(self, text):
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.final_nl = text.endswith("\n")
        self.lines = text.replace("\r\n", "\n").split("\n")
        if self.final_nl:
            self.lines.pop()   # 마지막 빈 조각
        self.index()

    def text(self):
        return self.nl.join(self.lines) + (self.nl if self.final_nl else "")

    def index(self):
        """경로 -> {line, indent} 와 줄별 '내용 줄인가'."""
        self.entries = {}
        self.content = [False] * len(self.lines)
        stack = []            # (indent, key)
        skip_indent = None    # 여러 줄 글자(| >) 안쪽
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            if skip_indent is not None:
                ind = len(line) - len(line.lstrip(" "))
                if not stripped or ind > skip_indent:
                    continue
                skip_indent = None
            if not stripped or stripped.startswith("#"):
                continue
            self.content[i] = True
            m = _KEY_RE.match(line)
            if not m:
                continue
            indent = len(m.group("indent"))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            key = _unquote_key(m.group("key"))
            path = tuple(k for _, k in stack) + (key,)
            lead, tok, tail = _scan_value(m.group("rest"))
            self.entries[path] = {"line": i, "indent": indent, "tok": tok}
            if tok[:1] in ("|", ">"):
                skip_indent = indent
            stack.append((indent, key))

    def subtree_end(self, path):
        """path 아래 마지막 '내용 줄'의 번호 (자식이 없으면 그 줄 자신)."""
        e = self.entries[path]
        last = e["line"]
        for j in range(e["line"] + 1, len(self.lines)):
            if not self.content[j]:
                continue
            ind = len(self.lines[j]) - len(self.lines[j].lstrip(" "))
            if ind <= e["indent"]:
                break
            last = j
        return last

    def child_indent(self, path):
        e = self.entries[path]
        for j in range(e["line"] + 1, self.subtree_end(path) + 1):
            if self.content[j]:
                return len(self.lines[j]) - len(self.lines[j].lstrip(" "))
        return e["indent"] + 2


def _replace_token(line, new_tok):
    m = _KEY_RE.match(line)
    prefix_len = len(line) - len(m.group("rest"))
    lead, tok, tail = _scan_value(m.group("rest"))
    head = line[:prefix_len] + lead
    if tail.strip():   # 줄 끝 주석: 같은 칸에 맞춘다
        old_col = len(head) + len(tok) + (len(tail) - len(tail.lstrip()))
        gap = max(1, old_col - (len(head) + len(new_tok)))
        return head + new_tok + " " * gap + tail.lstrip()
    return head + new_tok


def _set_one(doc, path, value):
    tok = dump_scalar(value)
    path = tuple(path)
    if path in doc.entries:
        e = doc.entries[path]
        old = e["tok"]
        is_flow = old[:1] in ("{", "[") and old.replace(" ", "") not in ("{}", "[]")
        if is_flow or old[:1] in ("|", ">"):
            raise PatchError(f"'{'.'.join(path)}' 는 {{ }} 나 여러 줄로 적혀 있어서 자동으로 고칠 수 없어요. config.yaml 을 직접 고쳐 주세요")
        if doc.subtree_end(path) > e["line"]:
            raise PatchError(f"'{'.'.join(path)}' 아래에 다른 항목이 있어서 단순 값으로 바꿀 수 없어요")
        doc.lines[e["line"]] = _replace_token(doc.lines[e["line"]], tok)
        doc.index()
        return
    # 없는 항목: 가장 깊은 부모를 찾아 그 끝에 넣는다
    depth = len(path) - 1
    while depth > 0 and path[:depth] not in doc.entries:
        depth -= 1
    missing = path[depth:]
    if depth == 0:
        indent = 0
        at = len(doc.lines)
        while at > 0 and not doc.lines[at - 1].strip():
            at -= 1
        # 마지막 내용 줄 뒤에 (뒤쪽 주석 블록이 있으면 그 앞이 아니라 파일 끝)
        new = ([""] if at > 0 else []) + _chain(missing, indent, 2, tok)
        doc.lines[at:at] = new
    else:
        parent = path[:depth]
        pe = doc.entries[parent]
        p_tok = pe["tok"]
        if p_tok.replace(" ", "") in ("{}", "[]"):   # `params: {}` 처럼 비어 있는 값은 블록으로 바꿔서 쓴다
            doc.lines[pe["line"]] = _replace_token(doc.lines[pe["line"]], "").rstrip()
        elif p_tok[:1] == "{" and p_tok[-1:] == "}" and len(missing) == 1:
            # `happy: { rate: "+8%", pitch: "+12Hz" }` 처럼 한 줄 { } 안의 값: 그 키만 고치거나 끝에 덧붙인다
            doc.lines[pe["line"]] = _replace_token(doc.lines[pe["line"]], _patch_flow(p_tok, missing[0], tok))
            doc.index()
            return
        elif p_tok:
            raise PatchError(f"'{'.'.join(parent)}' 에 이미 값이 있어서 그 아래에 항목을 추가할 수 없어요")
        indent = doc.child_indent(parent)
        at = doc.subtree_end(parent) + 1
        doc.lines[at:at] = _chain(missing, indent, 2, tok)
    doc.index()


def _patch_flow(flow, key, tok):
    """한 줄짜리 `{ a: 1, b: "x" }` 에서 key 의 값만 tok 으로 바꾼다 (없으면 끝에 덧붙인다). 나머지 모양은 그대로."""
    pat = re.compile(r"""(?<![\w"'.\-])(""" + re.escape(key) + r""")(\s*:\s*)("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^,}\s]+)""")
    m = pat.search(flow)
    if m:
        return flow[:m.start(3)] + tok + flow[m.end(3):]
    inner = flow[1:-1].strip()
    return "{ " + (inner + ", " if inner else "") + f"{key}: {tok} }}"


def _chain(keys, indent, step, tok):
    out = []
    for i, k in enumerate(keys):
        pad = " " * (indent + step * i)
        out.append(f"{pad}{k}:" if i < len(keys) - 1 else f"{pad}{k}: {tok}")
    return out


def _get(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return KeyError
        d = d[k]
    return d


def _set_data(d, path, value):
    for k in path[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[path[-1]] = value


def patch_text(text, changes):
    """changes: {'a.b.c': 값}. 고친 문장 전체를 돌려준다. 검증에 실패하면 PatchError."""
    if not changes:
        return text
    try:
        before = yaml.safe_load(text.lstrip("﻿")) or {}
    except yaml.YAMLError as e:
        raise PatchError(f"config.yaml 에 문법 오류가 있어서 고칠 수 없어요:\n{e}") from None
    if not isinstance(before, dict):
        raise PatchError("config.yaml 은 '항목: 값' 형태여야 해요")
    doc = _Doc(text)
    expected = copy.deepcopy(before)
    for dotted, value in changes.items():
        path = tuple(dotted.split("."))
        if not all(path):
            raise PatchError(f"항목 이름이 올바르지 않아요: {dotted}")
        _set_one(doc, path, value)
        _set_data(expected, path, value)
    new_text = doc.text()
    try:
        after = yaml.safe_load(new_text.lstrip("﻿")) or {}
    except yaml.YAMLError as e:
        raise PatchError(f"고친 결과를 읽을 수 없어서 저장하지 않았어요 (원본은 그대로예요):\n{e}") from None
    if after != expected:
        raise PatchError("고친 결과가 예상과 달라서 저장하지 않았어요 (원본은 그대로예요). 이 항목은 config.yaml 을 직접 고쳐 주세요")
    return new_text


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    return raw.decode("utf-8-sig"), bom, os.stat(path).st_mtime_ns


def apply_file(path, changes, expected_mtime_ns=None, backup=True):
    """config.yaml 에 changes 를 반영해 저장한다. expected_mtime_ns 를 주면 읽은 뒤 파일이 바뀌었는지 확인한다 (ConflictError)."""
    text, bom, mtime = read_text(path)
    if expected_mtime_ns is not None and mtime != expected_mtime_ns:
        raise ConflictError("config.yaml 이 다른 곳에서 바뀌었어요. 다시 불러온 뒤에 고쳐 주세요")
    new_text = patch_text(text, changes)
    if new_text == text:
        return mtime
    if backup:
        with open(path + ".bak", "wb") as f:
            f.write((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write((b"\xef\xbb\xbf" if bom else b"") + new_text.encode("utf-8"))
    os.replace(tmp, path)
    return os.stat(path).st_mtime_ns


# ---------------------------------------------------------------- 예전 config.local.json 을 config.yaml 에 합치기
def flatten(d, prefix=()):
    """중첩된 딕셔너리를 {'a.b.c': 값} 으로 편다. 리스트/빈 딕셔너리는 지원하지 않는다 (PatchError)."""
    out = {}
    for k, v in d.items():
        p = prefix + (str(k),)
        if isinstance(v, dict):
            if not v:
                continue
            out.update(flatten(v, p))
        elif isinstance(v, (bool, int, float, str)) or v is None:
            out[".".join(p)] = v
        else:
            raise PatchError(f"자동으로 합칠 수 없는 값이에요: {'.'.join(p)} ({type(v).__name__})")
    return out


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return a == b


def local_path(server_dir):
    return os.path.join(server_dir, "config.local.json")


def preview_local(server_dir):
    """합치면 config.yaml 값이 어떻게 바뀌는지: [(경로, yaml 의 지금 값(없으면 KeyError 클래스), local 값)] — 같은 값은 뺀다."""
    lp = local_path(server_dir)
    if not os.path.exists(lp):
        return []
    try:
        with open(lp, encoding="utf-8-sig") as f:
            local = json.load(f)
    except (OSError, ValueError) as e:
        raise PatchError(f"config.local.json 을 읽을 수 없어요: {e}") from None
    if not isinstance(local, dict):
        raise PatchError("config.local.json 의 모양이 올바르지 않아요")
    flat = flatten(local)
    text, _bom, _m = read_text(os.path.join(server_dir, "config.yaml"))
    cur = yaml.safe_load(text.lstrip("﻿")) or {}
    out = []
    for dotted, v in flat.items():
        have = _get(cur, tuple(dotted.split(".")))
        if have is KeyError or not _same(have, v):
            out.append((dotted, have, v))
    return out


def migrate_local(server_dir):
    """config.local.json 의 값을 config.yaml 에 합치고, local 파일은 이름을 바꿔 보관한다. 합친 항목 목록을 돌려준다."""
    changes = preview_local(server_dir)
    lp = local_path(server_dir)
    if changes:
        apply_file(os.path.join(server_dir, "config.yaml"), {p: v for p, _h, v in changes})
    if os.path.exists(lp):
        dst = lp + ".migrated.bak"
        if os.path.exists(dst):
            dst = f"{lp}.{time.strftime('%Y%m%d-%H%M%S')}.migrated.bak"
        os.replace(lp, dst)
    return changes
