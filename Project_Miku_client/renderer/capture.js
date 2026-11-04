// 화면 캡처 보조: 영역 선택 + 자르기/축소 (JPEG)

// 캡처 원본에서 (css 좌표 rect | 전체) 를 잘라 긴 변 maxEdge 이하의 JPEG Blob 으로 만든다.
// shot: { jpg, pxW, pxH, dipW, dipH, offX, offY }  (main 의 capture:screen 결과)
//   창 좌표(css px) -> 이미지 픽셀: (x + offX) * (pxW / dipW)
export async function cropToBlob(shot, rect, maxEdge = 1600, quality = 0.85) {
  const bmp = await createImageBitmap(new Blob([shot.jpg], { type: 'image/jpeg' }));
  const k = shot.pxW / shot.dipW;
  let sx = 0, sy = 0, sw = bmp.width, sh = bmp.height;
  if (rect) {
    sx = Math.round((rect.x + shot.offX) * k);
    sy = Math.round((rect.y + shot.offY) * k);
    sw = Math.round(rect.w * k);
    sh = Math.round(rect.h * k);
    sx = Math.max(0, Math.min(sx, bmp.width - 1));
    sy = Math.max(0, Math.min(sy, bmp.height - 1));
    sw = Math.max(1, Math.min(sw, bmp.width - sx));
    sh = Math.max(1, Math.min(sh, bmp.height - sy));
  }
  const r = Math.min(1, maxEdge / Math.max(sw, sh));
  const cw = Math.max(1, Math.round(sw * r));
  const ch = Math.max(1, Math.round(sh * r));
  const canvas = document.createElement('canvas');
  canvas.width = cw; canvas.height = ch;
  canvas.getContext('2d').drawImage(bmp, sx, sy, sw, sh, 0, 0, cw, ch);
  if (bmp.close) bmp.close();
  const blob = await new Promise((res) => canvas.toBlob(res, 'image/jpeg', quality));
  return { blob, width: cw, height: ch };
}

// 화면 위에서 드래그로 영역 고르기. 취소(Esc/우클릭/너무 작음)면 null.
export function selectRegion(layer, box, hint) {
  return new Promise((resolve) => {
    let start = null;
    let rect = null;
    layer.style.display = 'block';
    layer.classList.remove('dragging');
    box.style.display = 'none';
    if (hint) hint.style.display = 'block';

    const done = (val) => {
      layer.removeEventListener('mousedown', down);
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      window.removeEventListener('keydown', key, true);
      layer.removeEventListener('contextmenu', cancel);
      layer.style.display = 'none';
      box.style.display = 'none';
      if (hint) hint.style.display = 'none';
      resolve(val);
    };
    const cancel = (e) => { e && e.preventDefault && e.preventDefault(); done(null); };
    const down = (e) => {
      if (e.button !== 0) return;
      start = { x: e.clientX, y: e.clientY };
      layer.classList.add('dragging');
      if (hint) hint.style.display = 'none';
    };
    const move = (e) => {
      if (!start) return;
      rect = {
        x: Math.min(start.x, e.clientX), y: Math.min(start.y, e.clientY),
        w: Math.abs(e.clientX - start.x), h: Math.abs(e.clientY - start.y),
      };
      Object.assign(box.style, { display: 'block', left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.w}px`, height: `${rect.h}px` });
    };
    const up = (e) => {
      if (!start) return;
      move(e);
      done(rect && rect.w >= 8 && rect.h >= 8 ? rect : null);
    };
    const key = (e) => { if (e.key === 'Escape') cancel(e); };

    layer.addEventListener('mousedown', down);
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    window.addEventListener('keydown', key, true);
    layer.addEventListener('contextmenu', cancel);
  });
}
