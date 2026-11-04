// 가사 디스크 캐시. 폴더: <dir>/<sha1(키)>.json  (곡 하나당 파일 하나)
//   항목: { v:1, at, provider, info, lines, lyricId, duration, offset?, tr? }
//     lyricId  원문 글자의 해시. 같은 가사를 다시 찾았을 때만 싱크 보정/번역을 이어 쓴다 (다른 가사면 버린다)
//     duration 저장할 때 곡 길이(초). 길이가 3초 넘게 다른 같은 이름의 곡(라이브/다른 버전)에는 싱크 보정을 적용하지 않는다
//     offset   곡별 싱크 보정(초)
//     tr       번역 { lang, model, at, lines: [줄마다 번역 글자, 쉼은 ''] }  (원문 줄과 인덱스가 같다)
//   예전 버전의 <dir>/offsets.json 은 처음 읽을 때 항목 안으로 옮기고 비운다.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const DURATION_TOLERANCE = 3;

const sha1 = (s) => crypto.createHash('sha1').update(String(s)).digest('hex');
function lyricIdOf(lines) { return sha1((lines || []).map((l) => l.text || '').join('\n')).slice(0, 16); }

class LyricsCache {
  constructor(dir) {
    this.dir = dir;
    this._legacy = null;
  }
  _file(key) { return path.join(this.dir, `${sha1(key)}.json`); }
  _ensure() { try { fs.mkdirSync(this.dir, { recursive: true }); } catch { /* 아래 쓰기에서 실패하면 그때 알린다 */ } }
  _read(key) {
    try {
      const j = JSON.parse(fs.readFileSync(this._file(key), 'utf8'));
      return j && j.v === 1 && Array.isArray(j.lines) && j.lines.length ? j : null;
    } catch { return null; }
  }
  _write(key, entry) {
    this._ensure();
    try { fs.writeFileSync(this._file(key), JSON.stringify(entry), 'utf8'); return true; } catch { return false; }
  }

  get(key) {
    const e = this._read(key);
    if (e && !e.lyricId) e.lyricId = lyricIdOf(e.lines);   // 예전에 저장된 항목
    return e;
  }

  // 가사를 저장한다. 같은 가사(lyricId 가 같음)를 다시 받은 거면 싱크 보정/번역은 그대로 둔다.
  set(key, value) {
    const lyricId = lyricIdOf(value.lines);
    const old = this.get(key);
    const entry = { v: 1, at: Date.now(), ...value, lyricId };
    if (old && old.lyricId === lyricId) {
      if (Number.isFinite(old.offset)) entry.offset = old.offset;
      if (old.tr) entry.tr = old.tr;
    }
    return this._write(key, entry);
  }

  // 항목 일부만 고친다. expectLyricId 를 주면 그 가사일 때만 (번역이 오는 사이 가사가 바뀐 경우를 막는다)
  patch(key, fields, expectLyricId = null) {
    const e = this.get(key);
    if (!e) return false;
    if (expectLyricId && e.lyricId !== expectLyricId) return false;
    const next = { ...e, ...fields };
    for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k];
    return this._write(key, next);
  }
  remove(key) { try { fs.unlinkSync(this._file(key)); } catch { /* 없으면 그만 */ } }

  _legacyOffsets() {
    if (this._legacy) return this._legacy;
    try { this._legacy = JSON.parse(fs.readFileSync(path.join(this.dir, 'offsets.json'), 'utf8')) || {}; } catch { this._legacy = {}; }
    return this._legacy;
  }
  _saveLegacy() {
    const o = this._legacyOffsets();
    try {
      if (Object.keys(o).length) fs.writeFileSync(path.join(this.dir, 'offsets.json'), JSON.stringify(o), 'utf8');
      else fs.unlinkSync(path.join(this.dir, 'offsets.json'));
    } catch { /* 없으면 그만 */ }
  }

  // 곡별 싱크 보정(초). duration: 지금 곡 길이(0 이면 모름 -> 길이 확인 없이 믿는다)
  getOffset(key, duration = 0) {
    const e = this.get(key);
    if (!e) return 0;
    let off = e.offset;
    if (!Number.isFinite(off)) {
      const old = this._legacyOffsets()[key];
      if (!Number.isFinite(old)) return 0;
      off = old;                                   // 예전 offsets.json 값을 항목 안으로 옮긴다
      this.patch(key, { offset: off });
      delete this._legacy[key]; this._saveLegacy();
    }
    if (duration > 0 && e.duration > 0 && Math.abs(e.duration - duration) > DURATION_TOLERANCE) return 0;
    return off;
  }
  setOffset(key, sec, duration = 0) {
    const e = this.get(key);
    if (!e) return false;
    const fields = { offset: sec ? sec : undefined };
    if (sec && duration > 0) fields.duration = duration;   // 이 길이의 곡에서 맞춘 보정값
    const ok = this.patch(key, fields);
    if (key in this._legacyOffsets()) { delete this._legacy[key]; this._saveLegacy(); }
    return ok;
  }
}

module.exports = { LyricsCache, lyricIdOf, DURATION_TOLERANCE };
