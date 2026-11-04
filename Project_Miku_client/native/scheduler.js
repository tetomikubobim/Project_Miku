// 일정 저장/발화. schedules.json 에 저장되어 앱을 껐다 켜도 남는다.
//   항목: { id, at(ms), text, repeat: null|'daily'|'weekdays'|'weekly', weekday: 0~6|null }
const fs = require('fs');

class Scheduler {
  constructor(file, onFire) {
    this.file = file;
    this.onFire = onFire;
    this.items = this._load();
    this.timer = null;
  }

  _load() {
    try {
      const arr = JSON.parse(fs.readFileSync(this.file, 'utf8').replace(/^\uFEFF/, ''));
      return Array.isArray(arr) ? arr : [];
    } catch { return []; }
  }

  _save() {
    try { fs.writeFileSync(this.file, JSON.stringify(this.items, null, 2), 'utf8'); }
    catch (e) { console.warn('[schedule] 저장 실패', e.message); }
  }

  list() {
    return this.items.slice().sort((a, b) => a.at - b.at);
  }

  add({ at, text, repeat = null, weekday = null, media = null }) {
    const item = {
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      at: Number(at),
      text: String(text || '알림').slice(0, 200),
      repeat: ['daily', 'weekdays', 'weekly'].includes(repeat) ? repeat : null,
      weekday: Number.isInteger(weekday) ? weekday : null,
    };
    // 음악 예약("30분 뒤 음악 꺼줘") 같은 미디어 명령: 예전에는 여기서 버려져서 실행되지 않았다
    if (media && typeof media === 'object' && typeof media.action === 'string') {
      item.media = { action: String(media.action).slice(0, 40), value: Number.isFinite(Number(media.value)) ? Number(media.value) : -1 };
    }
    if (!Number.isFinite(item.at)) throw new Error('잘못된 시각');
    this.items.push(item);
    this._save();
    return item;
  }

  // 일정 관리 창에서 고친다. 바꿀 수 있는 것: 시각, 내용, 반복 (미디어 예약은 시각/반복만)
  update(id, { at, text, repeat } = {}) {
    const item = this.items.find((i) => i.id === id);
    if (!item) throw new Error('없는 일정이에요');
    if (at !== undefined) {
      if (!Number.isFinite(Number(at))) throw new Error('잘못된 시각');
      item.at = Number(at);
    }
    if (text !== undefined && !item.media) item.text = String(text || '알림').slice(0, 200);
    if (repeat !== undefined) {
      item.repeat = ['daily', 'weekdays', 'weekly'].includes(repeat) ? repeat : null;
      item.weekday = item.repeat === 'weekly' ? new Date(item.at).getDay() : null;
    }
    this._save();
    return item;
  }

  // id 문자열, 또는 'all'
  remove(idOrAll) {
    const before = this.items.length;
    this.items = idOrAll === 'all' ? [] : this.items.filter((i) => i.id !== idOrAll);
    if (this.items.length !== before) this._save();
    return before - this.items.length;
  }

  static nextAfter(item, now) {
    const d = new Date(item.at);
    const step = item.repeat === 'weekly' ? 7 : 1;
    do {
      d.setDate(d.getDate() + step);
      if (item.repeat === 'weekdays') while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
    } while (d.getTime() <= now);
    return d.getTime();
  }

  start() {
    this._tick(true);
    this.timer = setInterval(() => this._tick(false), 1000);
  }

  stop() { clearInterval(this.timer); }

  // startup=true: 앱이 꺼져 있는 동안 지나간 알림을 한 번 처리한다
  _tick(startup) {
    const now = Date.now();
    const due = this.items.filter((i) => i.at <= now);
    if (!due.length) return;
    for (const item of due) {
      const lateMs = now - item.at;
      // 앱이 꺼져 있는 동안 지나간 것은 알리지 않고 넘어간다 (반복이면 다음 차례로)
      if (startup && lateMs > 60e3) {
        if (item.repeat) item.at = Scheduler.nextAfter(item, now);
        else this.items = this.items.filter((x) => x.id !== item.id);
        continue;
      }
      // 반복 알림이 1시간 넘게 늦었으면 (오래 꺼져 있었음) 울리지 않고 다음 차례로만 넘긴다
      const stale = item.repeat && lateMs > 3600e3;
      if (!stale) {
        try { this.onFire({ id: item.id, text: item.text, repeat: item.repeat, at: item.at, missed: startup && lateMs > 60e3 }); }
        catch (e) { console.warn('[schedule] 발화 실패', e); }
      }
      if (item.repeat) item.at = Scheduler.nextAfter(item, now);
      else this.items = this.items.filter((x) => x.id !== item.id);
    }
    this._save();
  }
}

module.exports = { Scheduler };
