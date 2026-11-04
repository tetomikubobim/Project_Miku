// '먼저 말 거는 간격' 직접 입력.
//  - parseInterval(): 입력 글자를 검사해서 { ok, minMinutes, maxMinutes } 또는 { ok:false, field:'min'|'max', error } 로 돌려준다 (화면과 무관, 시험 가능)
//  - IntervalDialog : 트레이 메뉴의 "간격 직접 입력…" 으로 여는 작은 입력 상자

export const LIMITS = { minMin: 1, minMax: 720, maxMax: 1440, defaultFactor: 1.5 };

// "30", "30분", "90m", "1.5시간", "2h" -> 분 (소수 허용, 반올림). 못 읽으면 null
export function parseMinutes(raw) {
  const m = /^\s*(\d+(?:\.\d+)?)\s*(분|m|min|mins|minutes?|시간|h|hr|hrs|hours?)?\s*$/i.exec(String(raw ?? ''));
  if (!m) return null;
  const n = Number(m[1]);
  const hour = m[2] && /^(시간|h|hr|hrs|hours?)$/i.test(m[2]);
  return Math.round(hour ? n * 60 : n);
}

export function parseInterval(minRaw, maxRaw) {
  const minText = String(minRaw ?? '').trim();
  if (!minText) return { ok: false, field: 'min', error: '최소 시간을 입력해 주세요. (예: 30)' };
  const min = parseMinutes(minText);
  if (min == null) return { ok: false, field: 'min', error: '최소 시간은 숫자로 적어 주세요. (예: 30, 1시간)' };
  if (min < LIMITS.minMin) return { ok: false, field: 'min', error: `최소 시간은 ${LIMITS.minMin}분 이상이어야 해요.` };
  if (min > LIMITS.minMax) return { ok: false, field: 'min', error: `최소 시간은 ${LIMITS.minMax}분(12시간) 이하로 해 주세요.` };

  const maxText = String(maxRaw ?? '').trim();
  let max;
  if (!maxText) {
    max = Math.ceil(min * LIMITS.defaultFactor);   // 비우면 최소의 1.5배 ("N분마다 먼저 말 걸어줘" 와 같은 규칙)
  } else {
    max = parseMinutes(maxText);
    if (max == null) return { ok: false, field: 'max', error: '최대 시간은 숫자로 적어 주세요. (예: 45) 비워도 돼요.' };
    if (max < min) return { ok: false, field: 'max', error: '최대 시간은 최소 시간보다 작을 수 없어요.' };
  }
  if (max > LIMITS.maxMax) return { ok: false, field: 'max', error: `최대 시간은 ${LIMITS.maxMax}분(24시간) 이하로 해 주세요.` };
  return { ok: true, minMinutes: min, maxMinutes: max };
}

export class IntervalDialog {
  // root: 대화상자 요소(#intervalDlg). deps: onApply({minMinutes,maxMinutes}), onOpen(), onClose()
  constructor(root, deps = {}) {
    this.root = root;
    this.d = deps;
    this.isOpen = false;
    root.innerHTML = `
      <div class="id-title">먼저 말 거는 간격</div>
      <div class="id-hint">말이 없을 때 <b>최소~최대</b> 사이의 무작위 시간이 지나면 먼저 말을 걸어요.<br>숫자는 분 단위예요. (1시간은 <code>60</code> 또는 <code>1시간</code>)</div>
      <label>최소 (분)<input class="id-min" inputmode="numeric" autocomplete="off" spellcheck="false" /></label>
      <label>최대 (분) <small>비우면 최소의 1.5배</small><input class="id-max" inputmode="numeric" autocomplete="off" spellcheck="false" /></label>
      <div class="id-err" role="alert"></div>
      <div class="id-row"><button class="id-cancel" type="button">취소</button><button class="id-ok primary" type="button">적용</button></div>`;
    this.minEl = root.querySelector('.id-min');
    this.maxEl = root.querySelector('.id-max');
    this.errEl = root.querySelector('.id-err');
    root.querySelector('.id-ok').addEventListener('click', () => this.submit());
    root.querySelector('.id-cancel').addEventListener('click', () => this.close());
    root.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); this.close(); }
      else if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); this.submit(); }
    });
    for (const el of [this.minEl, this.maxEl]) el.addEventListener('input', () => { this.errEl.textContent = ''; });
  }

  // current: 지금 적용 중인 { minMinutes, maxMinutes }
  open(current = {}) {
    this.minEl.value = current.minMinutes ?? '';
    this.maxEl.value = current.maxMinutes ?? '';
    this.errEl.textContent = '';
    this.root.style.display = 'flex';
    const was = this.isOpen;
    this.isOpen = true;
    if (!was && this.d.onOpen) this.d.onOpen();
    this.minEl.focus();
    this.minEl.select();
  }

  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    this.root.style.display = 'none';
    if (this.d.onClose) this.d.onClose();
  }

  submit() {
    const r = parseInterval(this.minEl.value, this.maxEl.value);
    if (!r.ok) { this.errEl.textContent = r.error; (r.field === 'max' ? this.maxEl : this.minEl).focus(); return false; }
    this.close();
    if (this.d.onApply) this.d.onApply({ minMinutes: r.minMinutes, maxMinutes: r.maxMinutes });
    return true;
  }

  contains(x, y) {
    if (!this.isOpen) return false;
    const r = this.root.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }
}
