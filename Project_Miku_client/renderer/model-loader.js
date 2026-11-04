// vtube.json + model3.json 을 병합해서 pixi-live2d-display 가 읽을 수 있는 설정과 감정 맵을 만든다.
//
// 핵심: VTube Studio 핫키가 가리키는 exp3/motion3 파일은 model3.json 에 등록돼 있지 않은 경우가 많다.
// 그래서 로드 전에 설정 객체를 복제해서 누락된 표정/모션을 주입한다.

const lower = (s) => String(s || '').trim().toLowerCase();
const baseName = (f) => String(f).split(/[\\/]/).pop().replace(/\.(exp3|motion3)\.json$/i, '');
// 경로 비교용 정규화 (대소문자, 역슬래시, ./ 무시)
const norm = (f) => String(f).replace(/\\/g, '/').replace(/^\.\//, '').toLowerCase();

export function buildModelSettings(resolved) {
  const { model3, model3Url, vts, hotkeyBase } = resolved;
  // 디스크에 실제로 있는 표정/모션 파일 목록 (main.js 가 스캔). 없으면 존재 검사를 건너뛴다.
  const files = Array.isArray(resolved.files) ? resolved.files : null;
  const fileSet = files ? new Set(files.map(norm)) : null;
  const fileExists = (rel) => !fileSet || fileSet.has(norm(rel));

  const settings = JSON.parse(JSON.stringify(model3));
  settings.url = model3Url; // pixi-live2d-display 가 상대경로를 해석할 기준 URL
  const fr = (settings.FileReferences ||= {});
  fr.Expressions ||= [];
  fr.Motions ||= {};

  // 감정 키워드(소문자) -> { type:'expression', name } | { type:'motion', group, index }
  const emotionMap = new Map();

  // 1) model3.json 자체에 등록된 표정/모션 (폴백용)
  for (const ex of fr.Expressions) {
    if (ex && ex.Name) emotionMap.set(lower(ex.Name), { type: 'expression', name: ex.Name });
  }

  const findExprByFile = (file) => fr.Expressions.find((e) => norm(e.File) === norm(file));
  const findMotion = (file) => {
    for (const [group, list] of Object.entries(fr.Motions)) {
      const index = list.findIndex((m) => norm(m.File) === norm(file));
      if (index >= 0) return { group, index };
    }
    return null;
  };
  const usedNames = new Set(fr.Expressions.map((e) => e.Name));
  const uniqueName = (n) => {
    let name = n, i = 2;
    while (usedNames.has(name)) name = `${n}_${i++}`;
    usedNames.add(name);
    return name;
  };

  const report = { hotkeys: 0, injectedExpressions: 0, injectedMotions: 0, skipped: [], fileKeys: [] };

  // 2) vtube.json 핫키 (우선순위가 더 높으므로 덮어쓴다)
  const hotkeys = (vts && Array.isArray(vts.Hotkeys)) ? vts.Hotkeys : [];
  for (const hk of hotkeys) {
    const file = hk && hk.File;
    if (!file || typeof file !== 'string') continue;
    const rel = hotkeyBase + file.replace(/\\/g, '/');
    if (!fileExists(rel)) { report.skipped.push(`${hk.Name || '?'}(파일 없음: ${file})`); continue; }
    const key = lower(hk.Name) || lower(baseName(file));
    if (!key) continue;

    if (/\.exp3\.json$/i.test(file)) {
      let ex = findExprByFile(rel);
      if (!ex) {
        ex = { Name: uniqueName(hk.Name || baseName(file)), File: rel };
        fr.Expressions.push(ex);
        report.injectedExpressions++;
      }
      emotionMap.set(key, { type: 'expression', name: ex.Name });
      report.hotkeys++;
    } else if (/\.motion3\.json$/i.test(file)) {
      let found = findMotion(rel);
      if (!found) {
        (fr.Motions.VTS ||= []).push({ File: rel });
        found = { group: 'VTS', index: fr.Motions.VTS.length - 1 };
        report.injectedMotions++;
      }
      emotionMap.set(key, { type: 'motion', group: found.group, index: found.index });
      report.hotkeys++;
    } else {
      report.skipped.push(`${hk.Name || '?'}(${hk.Action || file})`);
    }
  }

  // 3) 파일 이름 -> 감정 키워드 (가장 높은 우선순위)
  //    happy.exp3.json -> "happy", 기쁨.exp3.json -> "기쁨". 이름이 곧 감정 태그다.
  //    같은 이름이면 모션보다 표정을 우선한다 (모션을 먼저 등록하고 표정이 덮어쓴다).
  if (files) {
    for (const f of files.filter((x) => /\.motion3\.json$/i.test(x))) {
      const key = lower(baseName(f));
      if (!key) continue;
      let found = findMotion(f);
      if (!found) {
        (fr.Motions.VTS ||= []).push({ File: f });
        found = { group: 'VTS', index: fr.Motions.VTS.length - 1 };
        report.injectedMotions++;
      }
      emotionMap.set(key, { type: 'motion', group: found.group, index: found.index });
      report.fileKeys.push(key);
    }
    for (const f of files.filter((x) => /\.exp3\.json$/i.test(x))) {
      const key = lower(baseName(f));
      if (!key) continue;
      let ex = findExprByFile(f);
      if (!ex) {
        ex = { Name: uniqueName(baseName(f)), File: f };
        fr.Expressions.push(ex);
        report.injectedExpressions++;
      }
      emotionMap.set(key, { type: 'expression', name: ex.Name });
      report.fileKeys.push(key);
    }
  }

  // 4) 대기 모션: model3.json 에 Idle 그룹이 없을 때만 vtube.json 의 IdleAnimation 사용
  const idle = vts && vts.FileReferences && vts.FileReferences.IdleAnimation;
  if (idle && !fr.Motions.Idle) {
    fr.Motions.Idle = [{ File: hotkeyBase + idle }];
  }

  return { settings, emotionMap, report };
}
