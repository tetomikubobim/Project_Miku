// 스트리밍 응답(답이 만들어지는 동안 조금씩 도착)을 말풍선에 이어 붙이는 상태.
// 서버가 stream_start -> stream_meta -> stream_text... -> (chat_result{streamed:true}) 순서로 보낸다.
// 화면과 무관해서 시험할 수 있다 (node test/stream.test.mjs).

export class StreamState {
  constructor() { this.reset(); }

  reset() {
    this.id = null;       // 지금 받는 스트림 (이전 스트림의 늦게 온 조각은 버린다)
    this.spoken = '';     // 지금까지 도착한 대사
    this.tag = '';        // 말풍선 위에 붙는 작은 글씨 (💭 속마음 / 감정)
    this.active = false;
  }

  start(id) { this.reset(); this.id = id ?? null; this.active = true; }

  // 이 이벤트가 지금 스트림 것인가 (id 가 없는 서버 응답은 그대로 받아 준다)
  accepts(id) { return this.active && (id == null || this.id == null || id === this.id); }

  text(delta) { this.spoken += String(delta || ''); return this.spoken; }

  end() { this.active = false; }
}
