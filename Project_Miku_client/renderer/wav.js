// WAV 헤더의 data 길이가 실제 파일과 안 맞을 때 바로잡는다.
//
// 스트리밍 TTS(GPT-SoVITS 등)가 만든 WAV 는 헤더에 길이를 0, 0xFFFFFFFF, 또는 첫 조각 길이로 적어 두는 경우가 있다.
// 그러면 브라우저의 decodeAudioData 가 헤더에 적힌 길이까지만 디코딩하고 나머지를 버려서 "말하다 마는" 현상이 생긴다.
// 정상 파일은 그대로 두고(같은 버퍼를 돌려줌), 어긋난 경우에만 복사본의 길이 필드를 고친다.
export function repairWav(buf) {
  const u8 = new Uint8Array(buf);
  if (u8.length < 44) return { buffer: buf, note: '' };
  const str = (o, n) => String.fromCharCode(...u8.subarray(o, o + n));
  if (str(0, 4) !== 'RIFF' || str(8, 4) !== 'WAVE') return { buffer: buf, note: '' };

  const dv = new DataView(buf);
  let off = 12;
  while (off + 8 <= u8.length) {
    const id = str(off, 4);
    const size = dv.getUint32(off + 4, true);
    if (id === 'data') {
      const start = off + 8;
      const actual = u8.length - start;
      let fixed = size;
      if (size === 0 || size === 0xffffffff || size > actual) {
        fixed = actual; // 길이 미정/과대: 실제 파일 끝까지
      } else if (size < actual) {
        // 선언 길이 뒤에 정상적인 다른 청크(LIST 등)가 이어지면 그대로 두고, 아니면 오디오가 더 있는 것
        const next = start + size;
        const nextId = next + 8 <= u8.length ? str(next, 4) : '';
        if (!/^[\x20-\x7e]{4}$/.test(nextId)) fixed = actual;
      }
      if (fixed === size) return { buffer: buf, note: '' };
      const copy = buf.slice(0);
      const cdv = new DataView(copy);
      cdv.setUint32(off + 4, fixed, true);
      cdv.setUint32(4, copy.byteLength - 8, true);
      return { buffer: copy, note: `WAV 헤더 data 길이 ${size} → ${fixed} 보정` };
    }
    if (size === 0xffffffff) break;
    off += 8 + size + (size & 1);
  }
  return { buffer: buf, note: '' };
}
