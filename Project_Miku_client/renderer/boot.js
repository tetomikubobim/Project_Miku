// 라이브러리를 순서대로 읽고, 실패하면 이유를 화면과 miku.log 에 남긴 뒤 app.js 를 시작하지 않는다.
(function () {
  const load = (src) => new Promise((ok, fail) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = ok;
    s.onerror = () => fail(new Error('파일을 읽지 못했어요: ' + src));
    document.head.appendChild(s);
  });

  function fatal(msg) {
    console.error('[boot] ' + msg);
    const d = document.createElement('div');
    d.style.cssText = 'position:fixed;left:12px;top:12px;right:12px;padding:12px;background:#400;color:#fff;font:13px sans-serif;z-index:99999;white-space:pre-wrap;border-radius:8px';
    d.textContent = msg;
    document.body.appendChild(d);
  }

  (async () => {
    try {
      await load('../node_modules/pixi.js/dist/pixi.min.js');
      const ver = window.PIXI ? String(window.PIXI.VERSION) : '';
      if (!/^8\./.test(ver)) throw new Error('pixi.js 8 이 아니에요 (읽힌 버전: ' + (ver || '없음') + ')');
      // index.min.js 는 Cubism 2 까지 묶여 있어서 live2d.min.js 가 없으면 죽는다. Cubism 3/4/5 전용 번들을 쓴다.
      const before = new Set(Object.keys(window));
      await load('../node_modules/untitled-pixi-live2d-engine/dist/cubism.min.js');
      const added = Object.keys(window).filter((k) => !before.has(k));
      console.info('[boot] 엔진이 추가한 전역: ' + (added.join(', ') || '없음') + ' / PIXI.live2d: ' + (window.PIXI && window.PIXI.live2d ? '있음' : '없음'));
    } catch (e) {
      fatal('Live2D 라이브러리를 불러오지 못했어요.\n' + (e.message || e) +
        '\nProject_Miku_client 폴더에서 npm install 을 실행한 뒤 다시 켜 보세요.');
      return;
    }
    const m = document.createElement('script');
    m.type = 'module';
    m.src = './app.js';
    document.body.appendChild(m);
  })();
})();
