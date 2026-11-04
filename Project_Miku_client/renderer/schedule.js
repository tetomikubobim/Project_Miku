// 일정 관리 창. 일정은 메인 프로세스의 schedules.json 에 저장돼서 앱을 껐다 켜도 남는다.
const $ = (id) => document.getElementById(id);
const WEEK = ['일', '월', '화', '수', '목', '금', '토'];
const REPEAT = { daily: '매일', weekdays: '평일마다', weekly: '매주' };
let editing = null;   // 수정 중인 일정 id

const pad = (n) => String(n).padStart(2, '0');
const toInput = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };

function when(ms) {
  const d = new Date(ms);
  const h = d.getHours();
  return `${d.getMonth() + 1}월 ${d.getDate()}일(${WEEK[d.getDay()]}) ${h < 12 ? '오전' : '오후'} ${h % 12 || 12}:${pad(d.getMinutes())}`;
}
function left(ms) {
  const min = Math.round((ms - Date.now()) / 60e3);
  if (min < 1) return '곧';
  if (min < 60) return `${min}분 뒤`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}시간${min % 60 ? ` ${min % 60}분` : ''} 뒤`;
  return `${Math.floor(h / 24)}일 뒤`;
}

function row(s, isMedia) {
  const li = document.createElement('li');
  if (s.id === editing) li.className = 'editing';
  const w = document.createElement('div'); w.className = 'when';
  w.textContent = `${when(s.at)} · ${left(s.at)}`;
  if (s.repeat) { const b = document.createElement('span'); b.className = 'badge'; b.textContent = REPEAT[s.repeat] || s.repeat; w.append(b); }
  const t = document.createElement('div'); t.className = 'text'; t.textContent = s.text;
  const btns = document.createElement('div'); btns.className = 'btns';
  const ed = document.createElement('button'); ed.textContent = '수정'; ed.onclick = () => startEdit(s);
  const del = document.createElement('button'); del.textContent = '삭제'; del.className = 'danger';
  del.onclick = async () => { await window.scheduleApi.remove(s.id); if (editing === s.id) resetForm(); };
  btns.append(ed, del);
  li.append(w, t, btns);
  return li;
}

async function render() {
  const all = await window.scheduleApi.list();
  const norm = all.filter((s) => !s.media), media = all.filter((s) => s.media);
  $('list').replaceChildren(...(norm.length ? norm.map((s) => row(s)) : [Object.assign(document.createElement('li'), { className: 'empty', textContent: '등록된 일정이 없어요.' })]));
  $('media').replaceChildren(...media.map((s) => row(s, true)));
  $('mediaHead').hidden = !media.length;
}

function resetForm() {
  editing = null;
  $('form').reset();
  $('submit').textContent = '추가'; $('cancel').hidden = true; $('err').textContent = '';
  $('text').disabled = false; $('text').required = true;
  defaultTime();
  render();
}

function startEdit(s) {
  editing = s.id;
  $('text').value = s.text; $('text').disabled = !!s.media;   // 음악 예약은 내용을 못 바꾼다 (시각/반복만)
  $('text').required = !s.media;
  $('at').value = toInput(s.at); $('repeat').value = s.repeat || '';
  $('submit').textContent = '저장'; $('cancel').hidden = false; $('err').textContent = '';
  $('text').focus();
  render();
}

$('cancel').onclick = resetForm;
$('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const at = new Date($('at').value).getTime();
  const repeat = $('repeat').value || null;
  const text = $('text').value.trim();
  if (!Number.isFinite(at)) { $('err').textContent = '날짜와 시간을 정해 주세요.'; return; }
  if (!repeat && at <= Date.now()) { $('err').textContent = '지난 시각이에요. 앞으로의 시각을 정해 주세요.'; return; }
  try {
    if (editing) await window.scheduleApi.update(editing, { at, text: $('text').disabled ? undefined : text, repeat });
    else await window.scheduleApi.add({ at, text, repeat, weekday: repeat === 'weekly' ? new Date(at).getDay() : null });
    resetForm();
  } catch (err) { $('err').textContent = err.message || String(err); }
});

// 기본값: 한 시간 뒤 정각
function defaultTime() { const d = new Date(Date.now() + 3600e3); d.setMinutes(0, 0, 0); $('at').value = toInput(d.getTime()); }
defaultTime();
window.scheduleApi.onChanged(() => { if (editing == null) render(); });
setInterval(() => { if (editing == null) render(); }, 30e3);   // '몇 분 뒤' 갱신
render();
