// 안내(명령 결과, 미디어 제어, 오류, 설정 바꿈 알림 등)가 떠 있는 시간(초). 글이 길수록 읽을 시간을 더 준다.
export const noticeSeconds = (text) => Math.min(12, Math.max(2.5, 1.5 + String(text).length * 0.07));

export class Bubble {
  constructor(el, seconds = 6) {
    this.noticeSeconds = noticeSeconds;   // 시험에서 바꿔 끼울 수 있게 속성으로 둔다
    this.el = el;
    this.seconds = seconds;
    this.timer = null;
    this.enabled = true; // false 면 force 가 아닌 말풍선은 띄우지 않는다 (config.showBubble)
    this.holds = new Set(); // 이유별로 "사라지지 않게 붙잡기" (음성 재생 중, 편집 모드 등)
    this.last = null;       // 마지막 대사 { text, tag }: 사라진 뒤 마우스를 올리면 다시 보여 준다
    this.hovering = false;  // 마우스가 모델/입력창 위에 있는 동안 true
    this.recalled = false;  // 지금 보이는 게 사라졌던 대사를 다시 띄운 것이면 true
    this.canRecall = () => true; // false 를 돌려주면 마우스를 올려도 지난 대사를 다시 띄우지 않는다 (가사가 떠 있을 때)
  }

  _arm(seconds = this.seconds) {
    clearTimeout(this.timer);
    if (this.holds.size) return; // 붙잡혀 있으면 사라지는 타이머를 걸지 않는다
    this.timer = setTimeout(() => this.hide(), seconds * 1000);
  }

  // tag: 감정 매칭 확인용 작은 글씨 (여러 줄 가능). 예: "💭 *blushes*\n감정: red → 표정:red (think)"
  // dialogue: true 면 캐릭터의 대사 (마우스를 올릴 때 다시 보여 줄 대상). 안내/오류/'…' 는 넣지 않는다.
  show(text, tag = '', force = false, dialogue = false) {
    if (!this.enabled && !force) { this.hide(); return; }
    this.recalled = false;
    if (dialogue && String(text).trim() && String(text).trim() !== '…') this.last = { text, tag };
    this._render(text, tag);
    // 캐릭터의 대사가 아닌 안내는 저절로 사라진다 (마우스를 모델 위에 올려 뒀어도, 대사가 나오지 않아도).
    // '…' 로 끝나는 건 처리 중 표시(생각 중, 화면 읽는 중, 검색 중)라 결과가 오기 전까지 둔다.
    const t = String(text).trim();
    if (!dialogue && t && !t.endsWith('…')) this.expireIn(this.noticeSeconds(t), true);
  }

  _render(text, tag) {
    this.el.replaceChildren();
    if (tag) {
      const t = document.createElement('span');
      t.className = 'tag';
      t.textContent = tag;
      this.el.appendChild(t);
    }
    this.el.appendChild(document.createTextNode(text));
    this.el.style.display = 'block';
    this._arm();
  }

  // 이어서 오는 문장을 붙일 때 사용
  append(text) {
    const cur = this.el.style.display === 'block' ? this.el.textContent + '\n' : '';
    this.show(cur + text);
  }

  // 지금 떠 있는 말풍선을 seconds 초 뒤에 지운다 (붙잡혀 있으면 그대로 둔다). 가사가 떠 있을 때 짧은 결과 안내를 금방 치우는 데 쓴다
  // force: 마우스를 올려 둔 것(hover) 때문에 붙잡혀 있어도 풀고 지운다 (입력창에서 명령을 친 직후엔 마우스가 모델 위에 있어서 안내가 안 사라지던 문제)
  expireIn(seconds, force = false) {
    if (!this.visible) return;
    if (force) this.holds.delete('hover');
    if (!this.holds.size) this._arm(seconds);
  }

  // 음성이 재생되는 동안 등, 말풍선을 계속 보여줘야 할 때
  hold(reason) {
    this.holds.add(reason);
    clearTimeout(this.timer);
  }

  // 붙잡기를 풀면 그때부터 seconds 초 뒤에 사라진다. seconds 를 주면 이번만 그 시간 뒤에 (예: 가사가 기다리고 있을 때 음성이 끝나고 1초 뒤)
  // force: 마우스를 올려 둔 것(hover) 때문에 붙잡혀 있어도 풀고 그 시간 뒤에 사라지게 한다 (가사로 돌아가야 할 때)
  release(reason, seconds, force = false) {
    this.holds.delete(reason);
    if (force && seconds != null) this.holds.delete('hover');
    if (this.visible) this._arm(seconds == null ? this.seconds : seconds);
  }

  // 마우스가 모델/입력창 위에 올라오면: 사라졌던 마지막 대사를 다시 띄우고, 올라와 있는 동안은 사라지지 않게 붙잡는다 (지나가면 못 읽으니까)
  hoverOn() {
    if (this.hovering) return;
    this.hovering = true;
    this.holds.add('hover');
    clearTimeout(this.timer);
    if (!this.visible && this.enabled && this.last && this.canRecall()) {
      this._render(this.last.text, this.last.tag);
      this.recalled = true;
    }
  }

  // 마우스가 벗어나면: 다시 띄운 대사는 바로 숨기고, 원래 떠 있던 말풍선은 평소처럼 seconds 초 뒤에 사라진다
  hoverOff() {
    if (!this.hovering) return;
    this.hovering = false;
    this.holds.delete('hover');
    if (this.recalled) this.hide();
    else if (this.visible) this._arm();
  }

  setEnabled(on) {
    this.enabled = !!on;
    if (!this.enabled) this.hide();
  }

  hide() {
    clearTimeout(this.timer);
    this.recalled = false;
    this.el.style.display = 'none';
  }

  get visible() {
    return this.el.style.display === 'block';
  }
}
