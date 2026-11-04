// 재생 중인 곡 감시: native/media-watch.ps1 (Windows 미디어 전송 제어 = 스포티파이/유튜브 등이 윈도우에 알리는 정보)을 계속 띄워 두고
// 바뀔 때마다 한 줄씩 받는다. 비정상 종료하면 알아서 다시 띄운다 (2초 -> 최대 30초 간격).
//   const w = new MediaWatcher(); w.on('state', s => ...); w.on('problem', msg => ...); w.start({ apps, intervalMs }); w.stop();
//   state: { found, app, title, artist, album, status('Playing'|'Paused'|...), pos(초), duration(초), rate, at(받은 시각 ms) }
const { EventEmitter } = require('events');
const { spawn } = require('child_process');
const path = require('path');

const SCRIPT = path.join(__dirname, 'media-watch.ps1');

// 한 줄(base64 JSON) -> 상태 객체. 해석할 수 없으면 null
function parseLine(line, now = Date.now()) {
  const t = String(line || '').trim();
  if (!t) return null;
  let j;
  try { j = JSON.parse(Buffer.from(t, 'base64').toString('utf8')); } catch { return null; }
  if (!j || typeof j !== 'object') return null;
  if (j.error) return { error: String(j.error) };
  if (!j.found) return { found: false, at: now };
  const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
  return {
    found: true,
    app: String(j.app || ''),
    title: String(j.title || ''),
    artist: String(j.artist || ''),
    album: String(j.album || ''),
    status: String(j.status || ''),
    pos: Math.max(0, num(j.pos)),
    duration: Math.max(0, num(j.duration)),
    rate: num(j.rate) > 0 ? num(j.rate) : 1,
    rawPos: j.rawPos == null ? null : num(j.rawPos),   // 진단용: 윈도우가 알려 준 위치 그대로 / 마지막 갱신 후 지난 시간(초)
    age: j.age == null ? null : num(j.age),
    // PowerShell 이 보낸 시각(ts)과 받은 시각의 차이는 몇 ms 라서, 받은 시각을 기준으로 쓴다
    at: now,
  };
}

// "spotify, chrome" -> "spotify|chrome" (정규식 특수문자는 막는다). 비면 '' = 전부
function appsPattern(text) {
  return String(text || '').split(/[,\s|]+/).map((s) => s.trim()).filter(Boolean)
    .map((s) => s.replace(/[.*+?^${}()[\]\\]/g, '\\$&')).join('|');
}

class MediaWatcher extends EventEmitter {
  constructor({ spawnImpl = spawn, script = SCRIPT } = {}) {
    super();
    this.spawnImpl = spawnImpl;
    this.script = script;
    this.child = null;
    this.opts = null;
    this.wanted = false;
    this.backoff = 2000;
    this.restartTimer = null;
  }

  get running() { return !!this.child; }

  start(opts = {}) {
    this.opts = { apps: appsPattern(opts.apps), intervalMs: Math.max(200, Number(opts.intervalMs) || 500) };
    this.wanted = true;
    if (!this.child) this._spawn();
  }

  stop() {
    this.wanted = false;
    clearTimeout(this.restartTimer);
    this.restartTimer = null;
    const c = this.child;
    this.child = null;
    if (c) { try { c.kill(); } catch { /* 이미 끝남 */ } }
  }

  restart(opts) { this.stop(); this.start(opts); }

  _spawn() {
    const args = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', this.script,
      '-IntervalMs', String(this.opts.intervalMs), '-ParentPid', String(process.pid)];
    if (this.opts.apps) args.push('-Apps', this.opts.apps);
    let child;
    try { child = this.spawnImpl('powershell.exe', args, { windowsHide: true }); }
    catch (e) { this.emit('problem', `감시 프로그램을 띄우지 못했어요: ${e.message}`); this._scheduleRestart(); return; }
    this.child = child;
    let buf = '';
    let gotLine = false;
    child.stdout.on('data', (d) => {
      buf += d;
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 1);
        const s = parseLine(line);
        if (!s) continue;
        gotLine = true;
        this.backoff = 2000;   // 정상 동작 확인: 재시작 간격 초기화
        if (s.error) this.emit('problem', s.error); else this.emit('state', s);
      }
    });
    let err = '';
    child.stderr.on('data', (d) => { err += d; if (err.length > 2000) err = err.slice(-2000); });
    child.on('error', (e) => this.emit('problem', `감시 프로그램 오류: ${e.message}`));
    child.on('close', (code) => {
      if (this.child !== child) return;   // stop()/restart() 로 일부러 끊은 것
      this.child = null;
      if (!this.wanted) return;
      this.emit('problem', `감시 프로그램이 꺼졌어요 (code ${code})${err.trim() ? `: ${err.trim().split(/\r?\n/)[0].slice(0, 120)}` : ''}${gotLine ? '' : ' - 시작도 못 했어요'}`);
      this._scheduleRestart();
    });
  }

  _scheduleRestart() {
    if (!this.wanted) return;
    clearTimeout(this.restartTimer);
    this.restartTimer = setTimeout(() => { this.restartTimer = null; if (this.wanted && !this.child) this._spawn(); }, this.backoff);
    this.backoff = Math.min(this.backoff * 2, 30000);
  }
}

module.exports = { MediaWatcher, parseLine, appsPattern };
