# Project Miku 통합 런처

`server_run.bat` / `client_run.bat` 대신 쓰는 런처입니다. cmd 창 없이 서버와 클라이언트를 **각각** 켜고 끄고, 상태·로그·리소스를 한 화면에서 봅니다.
서버의 `config.yaml` 도 여기서 고칩니다.

## 설치/실행 (Windows)

```
cd Project_Miku_launcher
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pythonw start_launcher.pyw        <- cmd 창 없이 실행 (또는 start_launcher.pyw 를 더블클릭)
```

- 폴더는 이렇게 나란히 두면 자동으로 찾습니다: `Project_Miku_server/`, `Project_Miku/`(클라), `Project_Miku_launcher/`. 다르면 "런처" 탭에서 지정하세요.
- 서버는 서버 폴더의 `venv`(있으면)로 돌립니다. 없으면 런처의 파이썬을 씁니다. 클라는 `Project_Miku/node_modules` 의 electron 을 직접 실행합니다 (`npm install` 은 한 번 해 두세요).
- 처음 켜면 서버 → 서버가 준비되면 클라 순서로 자동으로 켭니다 ("런처" 탭에서 끌 수 있어요).
- 한 번에 하나만 실행됩니다 (이미 떠 있으면 알려 줘요).

## 화면

- **상태 카드**: 서버 / 클라 / 음성 인식(STT) / 음성 합성(TTS) / LLM.
  - 🟢 정상 · 🟡 켜는 중·모델 불러오는 중 · 🔴 꺼짐·오류 · ⚪ 꺼 둠 · 🔵 쓸 때 불러옴(STT)
  - 서버/STT/TTS 는 서버의 `GET /health` 를 2초마다 확인합니다. TTS 는 GPT-SoVITS(또는 RVC)가 실제로 응답하는지까지 봅니다.
  - **LLM 은 실시간으로 검사하지 않습니다.** 토큰을 쓰고 한도에 걸릴 수 있어서, 대신 서버가 모든 호출 결과를 `[llm] 성공/실패 …` 한 줄로 남기고 로그 탭의 **"LLM (호출 결과)"** 보기로 모아 봅니다. 카드에는 쓰는 제공자/모델만 표시합니다.
- **서버/클라 버튼**: 각각 시작/중지, 서버는 재시작도 있습니다. 서버만 재시작해도 클라는 그대로고 알아서 다시 접속합니다 (클라가 10초 간격까지 재시도).
- **로그 탭**: 서버, 클라, GPT-SoVITS, RVC, 런처 로그를 색으로 구분해서 한 곳에. 보기 필터 / 자동 스크롤 / 로그 폴더 열기. 로그는 `logs/` 에도 파일로 남습니다 (5MB × 4개 회전).
- **서버 설정 탭**: 서버 `config.yaml` 편집 (LLM, 음성, GPT-SoVITS, 음성 인식, 검색, 기억, 서버 주소/포트, 자동 실행 경로).
  - 저장하면 **`config.yaml` 에 직접** 쓰고 (주석/순서 그대로, 값만 바뀜, 직전 내용은 `config.yaml.bak`), 서버가 켜져 있으면 바로 다시 읽게 합니다.
  - ↻ 표시 항목은 서버를 다시 켜야 적용됩니다. [저장하고 서버 재시작] 버튼이 있어요.
  - 저장 전에 입력값을 검사하고, 저장 뒤 다시 읽어서 "요청한 값만 바뀌었는지" 확인합니다. 어긋나면 아무것도 쓰지 않습니다.
  - 예전 **`config.local.json`** 이 남아 있으면 [설정 병합] 버튼이 나옵니다. 지금 서버가 쓰던 값 그대로 `config.yaml` 에 합치고 local 파일은 `.migrated.bak` 으로 치웁니다. (서버는 이제 local 파일을 읽지 않습니다.)
  - API 키는 화면에 표시되지 않습니다 (설정됨/안 됨만). 비워 두면 그대로 둡니다.
- **런처 탭**: 서버/클라 폴더, 서버 파이썬, electron 경로, 자동 시작, 트레이로 시작.
- **리소스 패널**: CPU / 메모리 / GPU / VRAM 그래프와 서버(+STT), 클라, GPT-SoVITS, RVC 별 CPU·메모리. GPU 는 NVIDIA 만 (`nvidia-ml-py`).
- 클라의 서버 주소(`config.json` 의 `serverUrl`)가 서버 포트와 다르면 위쪽에 경고가 뜹니다 (클라 설정은 클라 설정창에서 그대로 고칩니다).

## config.yaml 만들기 / 설정 프리셋

**서버 설정** 탭에서 합니다.

- **config.yaml 만들기**: 서버 폴더에 `config.yaml` 이 없을 때만 [config.example.yaml 로 config.yaml 만들기] 버튼이 보입니다. 누르면 예시 파일을 복사해서 만들어 줍니다. **이미 `config.yaml` 이 있으면 버튼이 사라지고, 어떤 경우에도 덮어쓰지 않습니다.**
- **프리셋 저장**: [현재 설정 저장…] 으로 지금 `config.yaml` 의 값을 이름을 붙여 `presets/` 폴더(런처 옆, 저장소에는 올라가지 않음)에 JSON 으로 저장합니다. API 키는 저장할 때 포함 여부를 고릅니다 (기본은 빼기. 빼고 저장해도 불러올 때 지금 키는 그대로 남습니다).
- **프리셋 불러오기**: 고른 프리셋을 지금 설정에 적용합니다. 적용 전에 바뀔 항목과 건너뛸 항목을 보여 주고, 적용하면 직전 내용은 `config.yaml.bak` 으로 남습니다. 주석과 순서는 그대로입니다.
- **형식이 바뀌어도 불러올 수 있는 이유**: 프리셋은 줄 모양이 아니라 `llm.groq.model` 같은 **경로: 값** 으로 저장됩니다. 불러올 때 항목마다 지금 형식과 검사 규칙(범위, 선택지)에 맞는지 따로 확인해서, 맞는 것만 적용하고 맞지 않는 항목은 이유를 알려 주며 건너뜁니다. 하나가 틀려도 나머지는 적용됩니다. 새 버전에서 항목 이름이 바뀌면 `miku_launcher/presets.py` 의 `RENAMES` 에 `"예전.경로": "새.경로"` 를 한 줄 적으면 예전 프리셋도 새 이름으로 적용됩니다.
- **자동 백업**: [기본값 초기화] 를 하기 전에 지금 설정이 `자동백업-날짜시각` 프리셋으로 자동 저장됩니다 (API 키 제외, 최근 5개 유지). 초기화한 뒤 이 프리셋을 불러오면 값을 되살릴 수 있습니다.
- 새 config.example.yaml 로 새로 시작하는 방법: 프리셋 저장 → 기존 `config.yaml` 을 다른 이름으로 옮기기 → [config.yaml 만들기] → 프리셋 불러오기.

## 업데이트 (GitHub 연동)

**런처** 탭의 '업데이트 (GitHub)' 묶음에서 합니다. 저장소 주소(`owner/name`)를 적으면 GitHub 의 **최신 정식 릴리스**를 확인해서 지금 버전(`프로젝트 폴더/VERSION`)보다 높으면 알려 줍니다.

- **끄고 켜기**: '새 버전 확인'을 끄면 GitHub 에 아무것도 요청하지 않습니다 (버튼으로 직접 확인은 가능). '자동으로 설치'는 기본이 꺼져 있고, 켜면 **런처를 켤 때** 새 버전을 설치한 뒤 서버/클라를 켭니다. 켜 둔 동안 새 버전이 나오면 알려 주기만 하고(12시간마다 확인, `launcher.json` 의 `update_interval_hours`), 설치는 다음 시작이나 버튼으로 합니다.
- **설치 과정**: 서버/클라 끄기 → 설정을 `자동백업-…` 프리셋으로 저장 → 릴리스 소스 zip 받기 → 바뀔 파일만 백업(`updates/backups/`)하고 덮어쓰기 → 끝나면 다시 켜기. 하나라도 실패하면 이미 바꾼 파일을 되돌리고 아무것도 바뀌지 않은 상태로 끝냅니다.
- **건드리지 않는 것**: 서버 `config.yaml`, `persona.txt`, `chat_log/`, DB(`*.db`), `uploads/`, `audio/` / 클라 `config.json`, `models/`, `voice-models/`, `schedules.json`, `lyrics-cache/` / 런처 `launcher.json`, `presets/`, `tools/`, 그리고 `venv`, `node_modules`, 로그. 이미 있는 이 파일들은 릴리스에 들어 있어도 덮어쓰지 않습니다. 저장소에서 지워진 파일은 지우지 않습니다.
- **패키지**: `requirements.txt` 나 `package.json`/`package-lock.json` 이 바뀌었고 설치 탭에서 이미 설치된 항목이면 자동으로 다시 설치합니다.
- **되돌리기**: [마지막 업데이트 되돌리기]로 바뀐 파일을 백업에서 복원하고 새로 생긴 파일은 지웁니다 (백업은 최근 3개 유지).
- **런처 자신**: 소스로 실행 중이면 런처 `.py` 도 같이 바뀌고 런처를 다시 켜면 적용됩니다. **exe 로 실행 중이면** 릴리스에 `MikuLauncher*.zip` (MikuLauncher.exe 와 `_internal` 이 들어 있는 압축 파일)이 있을 때만 교체합니다. 런처를 끌 때 교체하고, [런처 다시 시작]을 누르면 교체 뒤 바로 다시 켭니다. 그 zip 이 없으면 exe 는 그대로입니다.
- **git 으로 받은 폴더**(프로젝트 폴더에 `.git` 이 있음)는 파일을 바꾸지 않습니다. `git pull` 로 업데이트하세요.
- 공개 저장소만 지원합니다 (토큰 없이 GitHub API 를 씁니다). 확인할 때 GitHub(api.github.com)에 접속합니다.

**릴리스 올리는 법 (배포하는 사람)**: 코드를 올린 뒤 GitHub 의 Releases 에서 태그를 `v1.0.1` 처럼 붙여 새 릴리스를 만들고(초안/사전 배포는 무시됩니다), 설명에 변경 내용을 적으면 런처 화면에 그대로 보입니다. 루트의 `VERSION` 도 같은 숫자로 맞춰 두세요. exe 를 같이 배포하려면 `MikuLauncher_win.zip` 처럼 `MikuLauncher` 로 시작하는 zip 을 릴리스 파일로 올립니다.

## 끄기

- 창 X 는 **트레이로 숨기기**입니다. 트레이 메뉴의 **종료**(또는 트레이가 없을 때의 창 X)를 누르면 런처가 켠 클라와 서버(+GPT-SoVITS/RVC)를 **정상 종료를 먼저 시도하고, 안 되면 하위 프로세스까지 강제로** 끈 뒤 종료합니다.
- 런처가 작업 관리자로 죽거나 크래시해도, Windows Job Object 로 묶어 둬서 OS 가 서버/클라를 같이 끕니다.
- 런처 밖에서 직접 켠 서버는 "외부 실행"으로 표시되고, 런처를 꺼도 그대로 둡니다 (중지 버튼은 종료 요청을 보냅니다).
- 런처로 켜면 서버의 "클라이언트가 꺼지면 서버도 종료" 동작은 자동으로 꺼집니다. 클라만 껐다 켜도 서버는 그대로예요.

## 터미널판 (점검용)

```
python -m miku_launcher.cli --base .            # 창 없이 서버/클라를 켜고 상태와 로그를 터미널에 찍기 (Ctrl+C 로 종료)
python -m miku_launcher.cli --no-client --seconds 60
```

## 테스트

```
pip install -r requirements.txt
python -m unittest discover -s tests -t .
```

- `test_yamlpatch.py`: config.yaml 주석 보존 편집 / local 병합 (실제 설정 파일 모양의 픽스처, API 키는 가려 둠)
- `test_core.py`: 로그, 프로세스 종료(말 안 듣는 하위 프로세스 포함), 설정 서비스, 리소스, 경고, 상태 계산
- `test_presets.py`: config.yaml 만들기(덮어쓰기 금지), 프리셋 저장/불러오기, 형식이 바뀐 경우(없어진 항목·바뀐 이름·잘못된 값) 처리
- `test_updater.py`: 버전 비교, 릴리스 확인 오류, 압축 풀기 안전 검사, 내 파일 보호, 백업/되돌리기, 실패 시 원상복구, 설치 전체 흐름 (네트워크 없이 가짜 응답으로)
- `test_integration.py`: 진짜 `server.py` 와 가짜 electron 으로 시작 → 설정 저장/즉시 반영 → 서버만 재시작 → 클라 개별 종료 → 정상 종료 → 런처 종료 전 과정 (서버 의존성이 있고 Linux/Mac 일 때만 돌아요. `MIKU_SERVER_SRC` 로 서버 폴더 지정)

## 구조

```
start_launcher.pyw          창 실행 진입점 (pythonw)
miku_launcher/
  core.py                   두뇌: 프로세스/상태/로그/리소스/설정 (Qt 와 무관)
  procs.py                  프로세스 켜기/끄기 (창 없이, 출력 수집, 정상→강제 종료)
  jobobject.py              Windows Job Object (런처가 죽으면 자식도 같이)
  poller.py                 /health 폴링
  resources.py              CPU/RAM/GPU 측정
  logs.py                   로그 허브 + 파일 + GPT-SoVITS/RVC 로그 따라 읽기
  server_config.py          서버 config.yaml 읽기/검증/저장 (서버의 settings.py 를 불러다 씀)
  yamlpatch.py              주석을 지키는 yaml 값 편집 + local 병합
  extra_fields.py           서버 FIELDS 에 없는 항목(주소/포트/경로 등)
  settings_store.py         launcher.json
  updater.py                GitHub 릴리스 확인 / 설치 / 되돌리기 (Qt 와 무관)
  ui/                       PySide6 화면 (main_window, log_page, settings_page, launcher_page, update_box, resource_panel, widgets, app)
```

나중에 exe 로 묶을 때는 `pyinstaller --noconsole --name MikuLauncher start_launcher.pyw` 정도로 시작하면 됩니다 (이 폴더 구조를 유지).

## 서버 파이썬 선택
비워 두면 아래 순서로 찾고, `uvicorn`/`fastapi`/`yaml`/`requests` 를 실제로 불러올 수 있는 첫 번째를 씁니다.
1. 서버 폴더의 `venv` / `.venv`
2. PATH 의 `python` (예전 `server_run.bat` 이 쓰던 것)
3. 런처 자신의 파이썬

모두 안 되면 어떤 파이썬에 어떤 모듈이 없는지 로그에 보여 줍니다. '런처' 탭의 '서버 파이썬'에 직접 지정하면 그 파이썬만 씁니다.


## 설치 탭 (모듈 설치)
`설치` 탭에서 필요한 모듈을 불빛(초록=설치됨, 회색=안 됨, 노랑=일부)으로 확인하고 버튼으로 바로 설치합니다.
- **필수**: 서버 패키지 (서버 폴더의 `venv` + `requirements.txt` + ddgs), 클라이언트 (`npm install`)
- **선택 · 마이크**: 음성 인식(faster-whisper), VAD/화자 인증 모델, NVIDIA GPU 라이브러리
- **선택 · RVC**: `tools/rvc/venv` 에 rvc-python + torch (NVIDIA GPU 가 있으면 CUDA 용, 없으면 CPU 용). 설치가 끝나면 서버 설정의 `tts.rvc.launch.python` / `tts.rvc.launch.dir`(= `tools/rvc`)이 비었거나 가리키는 곳이 없을 때 자동으로 채우고, `tools/rvc/rvc_models/` 폴더를 만들어 둡니다. 모델(.pth, 있으면 .index)은 그 안에 모델 폴더를 만들어 넣고 서버 설정 탭에서 목록으로 고릅니다
  - **└ 츠쿠요미짱 공식 RVC 모델 (옵션)**: RVC 를 설치해도 자동으로 받지 않습니다 (다른 모델을 쓸 수 있게). 이 항목의 버튼을 눌렀을 때만 공식 배포처(`tyc.rei-yumesaki.net`)에서 zip 을 받아 **모델의 `.pth` 와 `.index` 만** `rvc_models/tsukuyomi/` 에 넣습니다 (파일 이름만 영문으로 바뀌고 내용은 그대로, 받은 zip 은 지움). 이미 있으면 건드리지 않고, 서버 설정은 바꾸지 않습니다 — 쓰려면 서버 설정 탭에서 RVC 모델을 `tsukuyomi` 로 고르세요. [이용 약관](https://tyc.rei-yumesaki.net/work/software/rvc/terms/)(크레딧 표기 등)을 꼭 읽어 주세요.
- **선택 · GPT-SoVITS**: 공식 윈도우 통합 패키지(약 8GB)를 `tools/gptsovits` 에 풉니다. 설치가 끝나면(이미 설치돼 있어도 '이어서 설치'로) 한국어 TTS 에 필요한 `eunjeon` 을 GPT-SoVITS 자체 파이썬(`runtime`)에 설치합니다 — GPT-SoVITS 가 스스로 하는 `pip install` 은 PATH 의 다른 파이썬에 깔려서 소용이 없고, 없으면 `/tts` 가 400 으로 실패합니다. 빌드 오류가 나면 Visual Studio Build Tools 의 'C++를 사용한 데스크톱 개발'을 설치한 뒤 다시 시도하세요.
- 파이썬은 `tools/python` (동봉) → 이 컴퓨터의 3.10~3.11 → 없으면 포터블 파이썬(3.11)을 받아 `tools/python` 에 풉니다. Node.js 도 PATH 에 없으면 `tools/node` 에 받습니다
- 각 항목의 **지우기** 버튼: 설치된(또는 일부만 설치된) 항목을 확인 창을 거쳐 지웁니다. 서버 venv / node_modules / 마이크 모델 / pip 패키지 / RVC venv(`rvc_models` 의 모델은 유지) / 츠쿠요미짱 모델 폴더 / `tools/gptsovits` 를 지우며, 서버 설정(config)은 건드리지 않습니다. 직접 지정한 파이썬은 지우지 않습니다. 서버/클라가 켜져 있으면 시작되지 않습니다
- 서버/클라가 켜져 있으면 그 환경에 설치하는 항목은 시작되지 않습니다 (파일이 잠겨서 실패하므로). 설치 중에도 `취소`로 멈출 수 있습니다
