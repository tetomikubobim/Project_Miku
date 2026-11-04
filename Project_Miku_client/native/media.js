// 미디어/볼륨 제어: native/media.ps1 을 PowerShell 로 실행하고 결과를 한국어 메시지로 바꿔 돌려준다.
const { spawn } = require('child_process');
const path = require('path');

const SCRIPT = path.join(__dirname, 'media.ps1');
const ACTIONS = new Set(['next', 'prev', 'play', 'pause', 'nowplaying', 'volume-set', 'volume-up', 'volume-down', 'volume-get', 'mute', 'unmute']);

function runPs(action, value, timeoutMs = 10000) {
  return new Promise((resolve) => {
    const args = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', SCRIPT, '-Action', action, '-Value', String(value)];
    let out = '';
    let err = '';
    let done = false;
    const finish = (r) => { if (!done) { done = true; clearTimeout(timer); resolve(r); } };
    const child = spawn('powershell.exe', args, { windowsHide: true });
    const timer = setTimeout(() => { try { child.kill(); } catch { /* ignore */ } finish({ ok: false, error: '시간 초과' }); }, timeoutMs);
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
    child.on('error', (e) => finish({ ok: false, error: e.message }));
    child.on('close', () => {
      const line = out.trim().split(/\r?\n/).filter(Boolean).pop();
      if (!line) return finish({ ok: false, error: err.trim().split('\n')[0] || '응답 없음' });
      try { finish(JSON.parse(Buffer.from(line, 'base64').toString('utf8'))); }
      catch { finish({ ok: false, error: `해석 실패: ${line.slice(0, 80)}` }); }
    });
  });
}

function messageOf(action, r) {
  switch (action) {
    case 'next': return '⏭ 다음 곡으로 넘겼어요.';
    case 'prev': return '⏮ 이전 곡으로 돌렸어요.';
    case 'play':
      return r.did === 'already-playing' ? '▶ 이미 재생 중이에요.' : r.did === 'toggled' ? '▶ 재생/일시정지 키를 눌렀어요.' : '▶ 재생할게요.';
    case 'pause':
      return r.did === 'already-paused' ? '⏸ 이미 멈춰 있어요.' : r.did === 'toggled' ? '⏸ 재생/일시정지 키를 눌렀어요.' : '⏸ 일시정지했어요.';
    case 'nowplaying':
      if (!r.found) return '🎵 재생 중인 곡을 찾지 못했어요.';
      return `🎵 ${[r.artist, r.title].filter(Boolean).join(' - ') || '제목 정보 없음'}${r.status === 'Paused' ? ' (일시정지 중)' : ''}`;
    case 'volume-set': return `🔊 볼륨을 ${r.volume}%로 맞췄어요.`;
    case 'volume-up':
    case 'volume-down':
      if (r.fallback) return `🔊 볼륨을 ${r.dir === 'up' ? '올렸어요' : '내렸어요'}. (약 ${r.steps}%)`;   // 정확한 값을 읽을 수 없을 때(볼륨 키로 대신 조절)
      return r.before === r.volume ? `🔊 볼륨이 이미 ${r.volume}%예요.` : `🔊 볼륨 ${r.before}% → ${r.volume}%`;
    case 'volume-get': return `🔊 지금 볼륨은 ${r.volume}%예요.${r.muted ? ' (음소거 중)' : ''}`;
    case 'mute': return r.fallback ? '🔇 음소거 키를 눌렀어요. (이미 음소거였다면 풀려요)' : '🔇 음소거했어요.';
    case 'unmute': return r.fallback ? '🔊 소리를 켰어요.' : `🔊 음소거를 풀었어요. (볼륨 ${r.volume}%)`;
    default: return '완료했어요.';
  }
}

async function run(action, value = -1) {
  if (process.platform !== 'win32') return { ok: false, message: '미디어 제어는 Windows에서만 지원돼요.' };
  if (!ACTIONS.has(action)) return { ok: false, message: `알 수 없는 미디어 명령: ${action}` };
  const v = Number.isFinite(Number(value)) ? Math.round(Number(value)) : -1;
  const r = await runPs(action, v);
  if (!r.ok) {
    console.warn('[media] 실패', action, r.error);
    return { ok: false, message: `미디어 제어에 실패했어요. (${r.error || '알 수 없는 오류'})` };
  }
  if (r.fallback) console.warn('[media] 볼륨 API(COM)를 쓰지 못해 볼륨 키로 대신 조절했어요:', r.comError);
  return { ok: true, message: messageOf(action, r), data: r };
}

module.exports = { run, messageOf };
