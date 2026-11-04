# Project Miku Server (경량판)

FastAPI + SQLite 로 만든 가벼운 서버입니다. MySQL, 로컬 LLM, 감정 분석 모델이 필요 없습니다.
Project Miku Client 와 같은 프로토콜(`/ws`, `POST /upload/image`)이라 클라이언트는 그대로 씁니다.

## 설치/실행 (Windows)

```
cd <서버 폴더>
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy config.example.yaml config.yaml     <- 열어서 api_key 등 수정
python server.py
```

- 서버 주소: `ws://127.0.0.1:3545/ws` (클라이언트 `config.json` 의 `serverUrl` 과 같게)
- 클라이언트의 `audioBaseUrl` 은 **지우세요**. 음성도 같은 포트(`/audio/…`)에서 내려줍니다.
- 음성 엔진은 `config.yaml` 의 `tts.provider` 로 고릅니다 (아래 "음성 엔진 고르기").
  `gptsovits` 면 서버가 GPT-SoVITS(api_v2, 기본 9880)를 **자동으로 켜 줍니다** (아래 "GPT-SoVITS 자동 실행"). `edge` 면 따로 켤 것이 없습니다.

## 설정 (config.yaml)

설정 파일은 `config.yaml` 입니다. (예전 `config.json` 은 더 이상 읽지 않으니 값을 `config.yaml` 로 옮기세요.)
`#` 뒤는 주석입니다. 들여쓰기는 스페이스만 쓰고(탭 금지), `:` 뒤에는 공백을 두세요.
`no`/`on`/`yes` 같은 값은 YAML 이 true/false 로 오해하니 `"no"` 처럼 따옴표로 감싸고, `"+8%"` 처럼 부호가 붙은 값도 따옴표로 감싸세요.
문법이 틀리면 서버가 시작할 때 몇 번째 줄이 문제인지 알려줍니다.

| 키 | 설명 |
|---|---|
| `llm.provider` | `gemini` / `openai` (OpenAI 호환: OpenAI, OpenRouter, 로컬 서버 등. `base_url` 지정) / `groq` (`llm.groq.api_key`, `llm.groq.model`) / `ollama` (`llm.ollama.base_url` 기본 `http://127.0.0.1:11434/v1`, `llm.ollama.model`. 로컬이면 API 키 불필요) |
| `llm.reasoning_level` | 사고 수준: `auto`(기본, 모델 기본값 그대로) / `off` / `low` / `medium` / `high` (아래 "사고 수준") |
| `persona_file` | 캐릭터 설정 파일 (`persona.txt`). 기존 서버의 `LLM_CHAT_FORMAT` 내용을 붙여 넣으세요. 사용자 호칭과 캐릭터 이름은 설정이 아니라 여기에 직접 적습니다. 서버를 끄지 않아도 저장하면 다음 대화부터 반영. **클라이언트의 모델에 `persona.txt` 가 있으면 그게 우선**하고, 이 파일은 모델에 persona 가 없을 때의 기본값 (아래 "모델별 persona / 감정 태그") |
| `history_turns` | 프롬프트에 넣는 최근 대화 턴 수 (기본 40) |
| `use_think` | 모델이 `[감정]*표정/동작 묘사*대사` 형식으로 행동 묘사를 내게 함 → 클라이언트 표정 매칭과 위쪽 작은 글씨(💭)에 사용. 속마음/생각은 요청하지 않음. 끄면 행동 묘사를 요청하지 않고, 모델이 써도 클라이언트로 보내지 않음 |
| `tts.provider` | 음성 엔진: `gptsovits`(기본, 생략 가능) 또는 `edge` |
| `tts.ref_audio_path` 등 | 기본 참조 음성. 감정별로 다르게 하려면 `emotion_refs` 에 `{"sad": {"path":"…","text":"…","lang":"ko"}}` |
| `tts.enabled` | false 면 음성 없이 글자만 |
| `stt.model` / `stt.device` / `stt.language` | 음성 인식(faster-whisper) 모델·장치·언어 (아래 "음성 인식") |
| `stream` | 답을 만드는 대로 먼저 보내기(스트리밍). 기본 true (아래 "스트리밍 응답") |
| `memory.*` | 장기 기억/대화 요약 (아래) |

**설정 편집**: 통합 런처(`Project_Miku_launcher`)의 "서버 설정" 탭에서 위 값들을 고칩니다. 런처가 **`config.yaml` 을 직접 고칩니다** (주석과 순서는 그대로, 값만 바뀝니다. 직전 내용은 `config.yaml.bak` 에 남습니다). 서버가 켜져 있으면 런처가 `POST /admin/reload` 로 바로 다시 읽게 하고, 서버를 다시 켜야 하는 항목(↻)은 화면에서 알려 줍니다. 서버는 설정 파일을 **읽기만** 합니다. 예전의 `config.local.json` 은 더 이상 읽지 않습니다 — 남아 있으면 런처의 "설정 병합"으로 `config.yaml` 에 합치세요 (지금 쓰던 값 그대로 들어가고 원본은 `.migrated.bak` 으로 보관됩니다). API 키는 화면으로 나오지 않습니다(설정 여부만 표시). 런처 없이 쓸 때는 `config.yaml` 을 직접 고치세요.

## 음성 인식 (STT, faster-whisper)

마이크 음성을 글자로 바꾸는 일을 **서버가 직접** 합니다. 따로 STT 서버를 켜거나 클라이언트에 주소를 적을 필요가 없습니다.

```
pip install -r requirements.txt        <- faster-whisper 포함 (이미 설치했다면 pip install faster-whisper 만)
```

- 클라이언트의 `serverUrl` 에서 `http://127.0.0.1:3545/v1/audio/transcriptions` 를 자동으로 만들어 음성을 보냅니다 (OpenAI 호환 경로라서 다른 도구에서도 쓸 수 있어요. 상태 확인: `GET /stt/status`).
- `config.yaml` 에 `stt:` 항목이 없어도 기본값(`small` 모델, `ko`, GPU 가 있으면 GPU)으로 동작합니다. 바꾸려면 `config.example.yaml` 의 `stt:` 를 복사해 고치세요. 모델/장치/언어는 클라이언트 설정 창의 "음성 인식" 항목에서도 바꿀 수 있고 다음 인식부터 적용됩니다.
- **처음 서버를 켤 때 모델을 내려받습니다** (small 약 460MB, 인터넷 필요). 서버가 켜질 때 백그라운드로 미리 불러오니 콘솔에 `[stt] 준비 완료` 가 뜬 뒤부터 빨라집니다 (`preload: false` 면 처음 말했을 때 불러옴).
- 모델 크기: `tiny` < `base` < `small` < `medium` < `large-v3-turbo` < `large-v3` (오른쪽일수록 정확하고 느리고 메모리를 더 씁니다). CPU 만 있다면 `small` 이하를 권합니다.
- **GPU**: `device: auto` 는 NVIDIA GPU 가 있으면 GPU 를 씁니다. cuBLAS/cuDNN 라이브러리가 없어서 실패하면 **자동으로 CPU(int8)로 바꿔서 이어가고**, 콘솔에 이유가 남습니다. GPU 를 쓰고 싶은데 이 메시지가 뜨면 `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` 로 라이브러리를 설치해 보세요 (서버가 그 DLL 폴더를 알아서 찾습니다). GPT-SoVITS 도 같은 GPU 를 쓰면 메모리(VRAM)를 나눠 쓰니, 부족하면 `device: cpu` 로 두세요.
- 말 구간은 클라이언트(Silero VAD)가 이미 잘라 보내므로 서버는 VAD 를 다시 돌리지 않습니다. 조용한 소리에서 지어낸 문장("시청해 주셔서 감사합니다" 등)과 반복 루프는 서버와 클라이언트에서 걸러냅니다.
- 서버에 `faster-whisper` 가 없거나 `stt.enabled: false` 면 클라이언트에 이유가 말풍선으로 나옵니다.

## 사고 수준 (reasoning)

`llm.reasoning_level` 은 LLM 이 답하기 전에 얼마나 생각할지를 정합니다 (런처 > 서버 설정 > LLM > 사고 수준). **업체마다 방식이 달라서** 서버가 알맞은 파라미터로 바꿔 보냅니다.

| 제공자 | 보내는 값 |
|---|---|
| Gemini 3 이상 | `thinkingConfig.thinkingLevel` (off=minimal→low, low/medium/high) |
| Gemini 2.5 | `thinkingConfig.thinkingBudget` (off=0, low=1024, medium=8192, high=24576 토큰) |
| OpenAI 호환 / Ollama | `reasoning_effort` (off=none, 추론 모델만 받음) |
| Groq | `reasoning_effort` (gpt-oss 는 off 가 없어 low) + 생각 과정이 본문에 섞이지 않게 `reasoning_format: hidden` (gpt-oss 는 `include_reasoning: false`) |

- `auto` 는 아무것도 보내지 않는 예전 동작입니다 (Groq 만 생각 과정을 숨기는 값을 보냄).
- 모델이 지원하지 않는 값이면 서버가 **400 으로 거부하는 것을 감지해 다음 후보 → 마지막엔 파라미터 없이** 다시 보냅니다. 통한 조합은 기억해서 다음부터는 바로 씁니다. 그래서 모델마다 되는 값이 달라도 대화가 막히지 않지만, 거부된 경우 그 설정은 조용히 무시된다는 뜻이기도 합니다. 서버 콘솔에는 따로 안내하지 않습니다.
- 생각에 쓰는 토큰도 `max_output_tokens` 에서 깎이므로 low/medium/high 에서는 **여유(+1024/+3072/+6144 토큰)를 자동으로 더합니다**.
- 검색 판단, 대화 요약, 기억 추출 같은 짧은 보조 호출은 항상 `off` 로 보냅니다 (빠르게, 토큰이 모자라 잘리지 않게).
- 생각이 높을수록 답이 느려집니다. 음성 대화에는 `off`/`low` 를 권장합니다.

## 모델별 persona / 감정 태그

Live2D 모델마다 캐릭터 설정과 감정 태그를 따로 쓸 수 있습니다. 파일은 **클라이언트의 모델 폴더**(`models/<모델>/persona.txt`, `emotionAliases.json`)에 있고, 클라이언트가 접속 직후/모델을 바꿀 때/설정 창에서 저장할 때 `profile_set {model, persona, emotions:{태그:설명}}` 으로 서버에 알립니다 (응답 없음). 서버는 가장 최근에 받은 프로필을 씁니다.

- **persona**: 보낸 persona 가 있으면 `persona.txt`(서버) 대신 그걸 프롬프트에 씁니다. 비어 있으면 서버의 `persona.txt`. 호칭과 이름도 persona 에 직접 적습니다 (별도 설정 없음).
- **감정 태그**: 보낸 태그가 AI 가 고를 수 있는 감정 목록이 됩니다 (`neutral` 은 항상 포함). 없으면 `config.yaml` 의 `emotions`/기본 감정. 설명이 비어 있으면 기본 감정의 설명을 빌려 옵니다 (`anger` → `angry` 처럼 같은 뜻이면). AI 가 `[angry]` 처럼 같은 뜻의 다른 표기를 써도 모델의 태그(`anger`)로 받아 줍니다. 태그는 영어/한글 글자만(최대 20자, 24개까지).
- **음성(GPT-SoVITS)**: 보낸 `voice`(`gpt_weights_path`, `sovits_weights_path`, `ref_audio_path`, `prompt_text`, `prompt_lang`)가 있으면 `config.yaml` 의 `tts` 설정 위에 덮어씁니다. 적지 않은 항목은 `tts` 설정 그대로입니다. 모델이 자기 참조 음성을 쓰면 서버의 `emotion_refs`(다른 목소리의 것)는 쓰지 않습니다. 가중치는 아래 "GPT-SoVITS 가중치"대로 올립니다.
- 음성 감정(`edge.emotions`, `emotion_refs`)은 태그가 `anger` 여도 기본 이름(`angry`)으로 찾습니다.
- 서버를 다시 켜면 프로필이 비지만, 클라이언트가 다시 접속할 때 자동으로 다시 보냅니다. 대화 기록도 모델별로 나뉩니다 (위의 "대화 기록" 참고).

## GPT-SoVITS 가중치

`tts.gpt_weights_path`(`.ckpt`), `tts.sovits_weights_path`(`.pth`)를 적으면 서버가 합성하기 전에 GPT-SoVITS api_v2 의 `/set_gpt_weights`, `/set_sovits_weights` 로 가중치를 올립니다 (설정 창 > 서버 탭 > GPT-SoVITS 에서도 고칠 수 있음). 상대 경로는 GPT-SoVITS 폴더 기준, 서버 PC 기준 경로입니다.

- 이미 올라간 것과 같으면 다시 올리지 않습니다. 모델(캐릭터)을 바꿔 가중치가 달라지면 바뀐 것만 올립니다 (처음 말할 때 몇 초 걸릴 수 있음). 모델별 값은 클라이언트의 모델 폴더 `voice.json` 에 있고 설정 창 > 모델 탭에서 고칩니다.
- **비워 두면 건드리지 않습니다**: GPT-SoVITS 가 켜질 때 `tts_infer.yaml` 에서 읽은 가중치를 그대로 씁니다. 그래서 모델 A 에만 가중치를 적고 모델 B 에는 안 적으면, B 에서도 A 의 가중치가 남아 있습니다. 모델마다 다르게 쓸 때는 **기본 가중치를 서버 설정에 적어 두세요** (적지 않은 모델은 이 기본값으로 돌아옵니다).
- 경로가 틀려 GPT-SoVITS 가 거절하면 서버 콘솔에 로그를 남기고 현재 가중치로 계속 합성합니다 (같은 값으로는 다시 시도하지 않음, 설정을 고치면 다시 시도). GPT-SoVITS 가 꺼졌다 켜졌다면 다음 합성 때 다시 올립니다.

## 응답 형식과 파싱

모델은 `[감정]*행동 묘사*대사` 로 답합니다 (예: `[sad]*고개를 숙이며 눈물이 고인다*진심이야......?`).

- **행동 묘사(`*별표*`)는 대사에서 빼서** 클라이언트에는 `<think>*행동*</think>대사` 로 보냅니다. 그래서 대사창과 음성에는 대사만 남고, 행동 묘사는 위쪽 작은 글씨와 표정 선택에만 쓰입니다. 대사 중간이나 끝에 `*웃음*` 이 섞여 나와도 같은 방식으로 빠집니다 (`**굵게**` 는 그대로).
- 예전 형식 `<think>*…*</think>` 도 받습니다. 모델이 `<thought>`, `</thought>`, `<thinking>` 처럼 태그를 틀리게 써도 같은 블록으로 봅니다.
- 닫는 태그가 아예 없으면 앞쪽의 `*행동 묘사*` 만 think 로, 나머지는 대사로 살립니다 (예전에는 통째로 속마음이 되어 대사와 음성이 사라졌습니다).
- 스트리밍 중에 대사가 하나도 나오지 않은 채 끝나면, 최종 분해로 건진 대사로 음성을 만듭니다 (음성이 통째로 빠지는 것을 막는 안전장치).

## 클라이언트가 꺼지면 서버도 종료

클라이언트(앱)가 **모두 끊기면 서버도 스스로 종료**하고, GPT-SoVITS 도 같이 꺼집니다. 앱을 끄거나 작업 관리자로 강제 종료해도 연결이 끊기므로 똑같이 동작합니다.

```yaml
shutdown_with_client: true      # 생략하면 true. 끄려면 false
shutdown_grace_seconds: 15      # 끊긴 뒤 이 시간 안에 다시 접속하면 종료를 취소 (기본 15초)
```

- 서버를 먼저 켜 두고 클라이언트를 나중에 켜도 됩니다. **한 번도 접속한 적이 없으면 종료하지 않습니다.**
- 앱을 껐다 바로 다시 켜거나 절전에서 깨어나 재접속하는 경우를 위해 유예 시간이 있습니다. 재접속이 늦어 서버가 먼저 꺼지는 일이 있으면 `shutdown_grace_seconds` 를 늘리세요.
- 클라이언트 여러 개가 붙어 있으면 **전부** 끊겼을 때만 셉니다.
- 서버를 계속 켜 두고 싶으면 `shutdown_with_client: false`.
- **통합 런처로 켜면** 이 동작은 자동으로 꺼집니다 (런처가 `MIKU_LAUNCHER=1` 을 넘겨서 클라이언트가 끊겨도 서버가 꺼지지 않아요). 서버를 끄는 건 런처의 [중지] 버튼이나 런처 종료입니다.

## GPT-SoVITS 자동 실행

`tts.provider` 가 `gptsovits` 일 때만, 서버를 켤 때 GPT-SoVITS 도 같이 켜고 서버가 꺼지면 같이 끕니다. `edge` 이거나 `tts.enabled: false` 면 아무것도 켜지 않습니다.

```yaml
tts:
  provider: gptsovits
  url: "http://127.0.0.1:9880"
  launch:
    enabled: true
    dir: ""   # GPT-SoVITS 폴더 (api_v2.py 가 있는 곳)
    wait_seconds: 180
    log_file: gptsovits.log
```

- 기본 명령은 `<dir>/runtime/python.exe api_v2.py -a <url 호스트> -p <url 포트> -c GPT_SoVITS/configs/tts_infer.yaml` 입니다 (`runtime` 이 없으면 서버가 쓰는 파이썬). 다르게 켜야 하면 `launch.command` 에 목록으로 적으세요. 예: `["runtime/python.exe", "api_v2.py", "-a", "127.0.0.1", "-p", "9880", "-c", "내설정.yaml"]`
- `tts.url` 에서 이미 응답하는 서버가 있으면 새로 켜지 않고 그대로 씁니다. 이 경우 서버를 꺼도 그 GPT-SoVITS 는 끄지 않습니다. 직접 켜서 쓰고 싶으면 `launch.enabled: false`.
- 모델을 불러오는 동안(보통 수십 초)은 대화가 글자로만 나가고, 서버 콘솔에 `[sovits] GPT-SoVITS 준비 완료` 가 뜨면 음성이 나옵니다.
- `dir` 이 틀렸거나 실행에 실패해도 서버는 그대로 켜지고(글자로만 대화), 이유가 콘솔에 나옵니다. GPT-SoVITS 의 출력은 `log_file` 에 쌓입니다.
- 서버 프로세스가 강제로 죽으면(작업 관리자 등) GPT-SoVITS 가 남을 수 있습니다. 다음에 서버를 켜면 "이미 켜져 있다"고 보고 그대로 이어서 씁니다.

## 음성 엔진 고르기

`config.yaml` 의 `tts.provider` 한 줄로 바꿉니다. 바꾼 뒤 서버를 재시작하세요.

```yaml
tts:
  enabled: true
  provider: edge        # edge | gptsovits
```

| provider | 특징 |
|---|---|
| `gptsovits` | 참조 음성으로 목소리를 복제. GPT-SoVITS API 서버를 먼저 켜 둬야 함. `url`, `ref_audio_path`, `params` 등을 사용 |
| `edge` | Microsoft Edge TTS. 설치만 하면 바로 동작, GPU 불필요. 목소리 복제는 없고 기본 음성만 사용. 인터넷 필요 |

Edge 를 쓰려면 한 번만 설치합니다: `pip install edge-tts miniaudio` (requirements.txt 에 포함)

`edge` 설정 (모두 생략 가능):

| 키 | 설명 |
|---|---|
| `edge.voice` | 음성 이름. 기본 `ko-KR-SunHiNeural`. 목록: `edge-tts --list-voices` (한국어는 `ko-KR-` 로 시작) |
| `edge.rate` / `edge.pitch` / `edge.volume` | 속도 `+10%`, 음높이 `+20Hz`, 볼륨 `-10%` 처럼 부호 포함 문자열 |
| `edge.emotions` | 감정(`happy` `sad` `angry` `surprise`)별로 기본 `rate`/`pitch`/`volume` 에 **더하는** 값. 예: 기본 `pitch: "+12Hz"` 에 `happy: { pitch: "+2Hz" }` 이면 +14Hz. 목소리가 달라지지 않게 작은 값(±10 이내)을 권장. 예전처럼 통째로 덮어쓰려면 `edge.emotion_mode: absolute` |
| `edge.timeout` | 합성 제한 시간(초). 기본 30 |

음성이 안 나오면 서버 콘솔의 `[tts]` 로 시작하는 줄을 확인하세요. 엔진이 실패해도 대화는 글자로 계속됩니다.
설정이 맞는지 서버 없이 확인하려면 `python tts.py "안녕하세요"` 를 실행하세요 (audio 폴더에 wav 가 생성됩니다).

## RVC 로 목소리 바꾸기 (Edge TTS → 츠쿠요미짱 음색)

Edge TTS 가 만든 음성을 **RVC(목소리 변환)** 로 다른 음색으로 바꿉니다. 말투/억양/속도는 Edge 음성 그대로(`edge.emotions` 의 감정 표현도 유지)이고 음색만 바뀝니다. `tts.provider` 가 **edge** 이고 `tts.rvc.enabled: true` 일 때만 동작하며, 서버가 RVC 도 같이 켜고 같이 끕니다. 변환에 실패하면 원래 Edge 음성으로 대화를 이어갑니다 (`fallback: false` 면 글자만).

### 1) RVC 설치 (서버와 **별도** 파이썬 환경)
RVC(torch/fairseq)는 서버 파이썬과 한 환경에 두기 어려워서 따로 설치합니다. [rvc-python](https://github.com/daswer123/rvc-python) 의 GPU(Windows) 설치법 그대로입니다. 파이썬 **3.10** 권장.

```
cd C:\rvc
py -3.10 -m venv venv
venv\Scripts\activate
pip install rvc-python
pip install torch==2.1.1+cu118 torchaudio==2.1.1+cu118 --index-url https://download.pytorch.org/whl/cu118
```

### 2) 모델 받기
음색은 **츠쿠요미짱 공식 RVC 모델**(CV.夢前黎)입니다. 모델 파일은 이 저장소에 **들어 있지 않고**, 공식 배포처에서 직접 받아 옵니다. 서버 폴더에서 한 번 실행하세요.

```
python get_tsukuyomi_rvc.py --dir C:\rvc
```
- 공식 zip(`tyc-rvc.zip`, 通常1~3·強·弱 포함)을 받아 **고른 모델의 `.pth` 와 `.index` 만** `C:\rvc\rvc_models\tsukuyomi\` 에 넣습니다. 목록에서 번호를 고르거나 `--pick 번호` / `--match 글자` 로 미리 정할 수 있고, `--dir` 를 생략하면 `config.yaml` 의 `tts.rvc.launch.dir` 를 씁니다. 이미 받아 둔 zip 은 `--zip 경로`. 통합 런처를 쓴다면 **설치 탭의 'RVC' 아래 옵션 항목**으로도 받을 수 있습니다 (RVC 를 설치해도 자동으로 받지는 않습니다). 파일 이름만 영문(`tsukuyomi.pth`)으로 바뀌고 내용은 그대로입니다. 폴더 이름이 `model` 설정값입니다.
- hubert / rmvpe 같은 RVC 공통 파일은 따로 넣을 필요가 없습니다 (이 프로젝트는 받지 않습니다). 처음 설치한 환경에서 변환이 에러로 실패하면 `rvc.log` 에서 이 파일 관련 메시지가 있는지 확인하세요.
- **사용 조건** ([공식 이용 약관](https://tyc.rei-yumesaki.net/work/software/rvc/terms/) 요약. 반드시 원문을 확인하세요)
  - 특정 정치·종교·사상에 찬성/반대를 호소하는 활동, 실존 인물·단체·상품을 비판·공격하는 활동에는 쓸 수 없고, 그런 활동을 하는 이름/계정으로도 쓸 수 없습니다.
  - 변환한 음성을 영상·작품 등으로 공개할 때는 **"변환 전 음성"과 "츠쿠요미짱 공식 RVC 모델"** 두 가지 크레딧을 적어야 합니다.
  - 이 프로젝트에서 변환 전 음성은 Edge TTS 가 만든 음성입니다. 약관은 자기 목소리가 아닌 음성은 그 음성의 이용 조건도 함께 확인하라고 하니, Edge TTS 쪽 조건도 확인하세요.
- 이 프로젝트에 모델을 **직접 동봉해서 배포**하려면 약관의 "자작 소프트웨어에 동봉" 조건(무개조 모델, 공식 배포 페이지 URL 명시, 모델 부분에만 약관 계승, 사용 전에 보이는 곳에 지정된 안내문 표시)을 따라야 합니다. 지금은 동봉하지 않고 받게 합니다.

### 3) 설정 (config.yaml 의 tts 아래)
```yaml
tts:
  provider: edge
  rvc:
    enabled: true
    model: tsukuyomi
    pitch: 0                    # 반음 단위. 어색하면 +2, +4, -2 처럼 바꿔 보기
    device: "cuda:0"            # GPU 없으면 "cpu:0"
    launch:
      python: "C:/rvc/venv/Scripts/python.exe"
      dir: "C:/rvc"             # 이 안에 rvc_models 폴더
```

- 서버를 켜면 `python -m rvc_python api -p 5050` 를 대신 실행하고, 준비되면 모델/장치/파라미터를 설정합니다. 모델을 불러오는 동안(수십 초)의 대답은 원래 Edge 음성으로 나가고, 콘솔에 `[rvc] RVC 준비 완료` 가 뜬 뒤부터 변환됩니다.
- 이미 RVC 서버를 직접 켜 두었다면 `launch.enabled: false` 로 하세요 (주소가 다르면 `url` 도).
- 처음 한 번은 RVC 만 따로 실행해서 되는지 확인하면 문제를 찾기 쉽습니다: `venv\Scripts\python -m rvc_python api -p 5050`
- 안 될 때는 서버 콘솔의 `[rvc]` 줄과 `rvc.log` (RVC 출력)를 보세요.
- `params` 로 고급 값을 바꿀 수 있습니다: `f0method`(rmvpe/harvest/crepe/pm), `index_rate`, `filter_radius`, `resample_sr`, `rms_mix_rate`, `protect`.
- RVC 는 GPU 메모리(VRAM)를 추가로 씁니다. 응답마다 변환 시간만큼 음성이 늦어지니, 너무 느리면 `pitch`/`f0method` 를 가볍게 하거나 `cpu` 대신 GPU 를 쓰세요.

## 동작

한 번의 채팅: 지금 대화(세션)의 최근 N턴을 SQLite 에서 불러옴 → 프롬프트(페르소나 + 시간 + 형식 규칙) → LLM → `[감정]*행동 묘사*대사` 파싱 → 저장 → 대사만 TTS(선택한 엔진, `*행동*`, `(지문)`, 이모지는 읽지 않음) → 결과 전송.

- 감정은 LLM 이 응답 앞에 `[happy]` 처럼 붙인 것을 사용 (별도 분석 모델 없음)
- 캡처 이미지는 `uploads/` 에 저장 → LLM 에 첨부 → 사용 후 삭제. 업로드 폴더 밖 경로는 거부
- 실패한 요청(LLM 오류 등)은 대화 기록에 남기지 않음
- 대화 기록은 **모델별로** `chat_log/<모델 폴더 이름>/miku.db` 에 저장됩니다 (`config.yaml` 의 `paths.chat_log` 폴더, 파일 이름은 `paths.db`). 세션, 메시지, 요약, 장기 기억이 모두 모델마다 따로입니다. 한 모델의 기록만 초기화하려면 그 폴더를 지우세요 (서버를 끈 뒤).
- 클라이언트가 모델을 알려 주면(`profile_set`) 그 모델의 기록으로 바뀌고 **새 대화 상태**로 돌아갑니다 (이전 대화는 대화 목록에서 열 수 있음). 같은 모델이면 대화가 이어집니다. 폴더 이름은 모델 이름에서 `\ / : * ? " < > |` 같은 글자를 `_` 로 바꾼 것입니다.
- **기존 기록 이전**: `chat_log` 폴더가 아직 없을 때 처음 접속한 모델이, 그동안 쓰던 `miku.db` 의 기록(대화, 기억)을 그대로 이어받습니다 (복사, 원본은 그대로 남음). 그 뒤 다른 모델은 빈 기록으로 시작합니다.
- 모델 이름 없이 접속하면(옛 클라이언트) 예전처럼 `miku.db` 를 씁니다.
- 예전 버전은 기본 파일 이름이 `lucia.db` 였습니다. `config.yaml` 의 `paths.db` 를 그대로 두면 계속 그 파일을 쓰므로 기록은 그대로입니다. 이름을 바꾸고 싶으면 서버를 끈 뒤 `lucia.db`(와 있다면 `-wal`, `-shm` 파일)를 `miku.db` 로 바꾸고 `paths.db` 를 고치세요.

## 대화(세션)

웹 LLM 처럼 대화가 세션으로 나뉘고, **세션마다 기록과 문맥이 따로**입니다 (다른 대화의 내용이 AI 에게 섞이지 않음).

- **서버를 켤 때마다 새 대화로 시작**합니다. 첫 메시지에 대한 응답이 성공하면 그때 세션이 만들어지고, 제목은 첫 메시지 앞부분(28자)입니다. 아무것도 보내지 않았거나 LLM 이 실패한 경우에는 빈 세션이 남지 않습니다.
- 클라이언트가 접속을 다시 맺어도(서버는 그대로) 같은 대화가 이어집니다. 서버를 재시작해야 새 대화가 됩니다.
- 알림(observe/notification)도 지금 대화에 기록됩니다. 대화가 없는 상태에서 알림이 먼저 오면 `알림 · 앱이름` 세션이 만들어집니다.
- 세션 삭제는 **그 대화의 메시지까지 완전히 지웁니다** (되돌릴 수 없음). 지운 대화가 지금 대화였다면 새 대화 상태로 돌아갑니다.
- 세션 도입 전의 기존 DB 파일은 처음 실행할 때 자동으로 옮겨집니다: 기존 메시지 전부가 `이전 대화` 세션 하나가 됩니다. 안전하게 하려면 처음 실행 전에 DB 파일을 복사해 두세요.

WebSocket 프로토콜 (`ws_handler.py` 상단 주석에도 있음):

| 클라이언트 → 서버 | 응답 |
|---|---|
| `session_list` | `sessions {sessions:[{id,title,created,updated,count}], active}` |
| `session_new` | `session_active {session_id:null, ...}` (실제 세션은 첫 메시지 때 생성) |
| `session_switch {session_id}` | `session_active {session_id, title, messages}` |
| `session_rename {session_id, title}` | `sessions` |
| `session_delete {session_id}` | `sessions` (+ 지운 게 현재 대화면 `session_active` 새 대화) |
| `session_messages {session_id}` | `session_messages {session_id, messages}` |
| `session_delete_all` | `session_active` (새 대화) + `sessions` (빈 목록). 모든 대화와 기록을 지움 |
| `proactive` | `proactive_result` (chat_result 와 같은 형태). 캐릭터가 먼저 거는 말 |

**먼저 말 걸기(`proactive`)**: 클라이언트의 타이머가 부릅니다. 서버는 지금 대화의 기록과 "마지막 대화로부터 N분 지났다"는 시스템 안내를 AI 에게 주고 먼저 한마디 하게 합니다. 사용자가 요청한 게 아니라서 **조용히** 처리합니다: 처리 중 표시(`status`)도 없고, LLM 이 실패해도(한도 초과 등) 클라이언트에 오류를 보내지 않고 서버 로그(`[server] proactive …`)에만 남깁니다. 시스템 안내문은 DB 에는 `kind='proactive'` 로 남아 AI 가 문맥으로 보지만, 대화 기록 화면과 목록의 메시지 수에서는 빠집니다. 대화가 없는 상태면 `먼저 말 걸기` 세션이 만들어집니다.

`sessions` 는 접속 직후, 그리고 대화 결과/세션 변경이 있을 때마다 서버가 먼저 보냅니다. 없는 대화 번호나 빈 이름이면 `error {code:"session_error"}` 를 보내고 목록을 다시 보냅니다.

## 장기 기억 / 대화 요약

같은 SQLite(모델별 `chat_log/<모델>/miku.db`)에 저장합니다. 그래서 기억도 모델(캐릭터)마다 따로 쌓입니다. 대화 응답을 돌려준 **뒤에 백그라운드**에서 LLM 을 한 번 더 불러 정리하므로 대화 속도에는 영향이 없고, 실패해도(한도 초과 등) 대화는 계속되며 다음 기회에 다시 시도합니다.

- **기억**: 내 메시지가 `extract_every` 개 쌓일 때마다 새 대화에서 "오래 기억할 사실"(이름, 취향, 약속…)을 뽑아 저장합니다. 이미 저장된 기억과 겹치면 합치거나 고칩니다. 저장된 기억은 매 대화의 시스템 프롬프트에 들어갑니다. 개수가 `max_items` 를 넘으면 오래된 것부터 지웁니다. 설정 창의 **기억** 탭에서 보고/고치고/지우고/직접 추가할 수 있습니다.
- **요약**: 한 대화가 길어져 원문으로 남긴 최근 `keep_turns` 턴을 넘긴 메시지가 `summary_batch` 개 이상 쌓이면, 앞부분을 요약으로 줄여 문맥에 넣습니다 (요약에 들어간 메시지는 문맥에서 빠지고 DB 에는 그대로 남아 대화 기록 화면에는 보입니다).
- **기억이 넘칠 때 (합치기)**: 기억이 `max_items` 를 넘으면 오래된 것부터 지우지 않고, LLM 이 비슷한 기억을 한 문장으로 **합치고** 겹치거나 의미 없어진 것만 지웁니다. 합칠 때 이름/날짜/숫자는 보존하도록 시키고, 그냥 지우는 건 한 번에 전체의 20% 까지만 허용합니다. 목표는 `max_items` 의 80% 입니다. 실패하거나 충분히 못 줄이면 기억은 그대로 두고 30분 뒤에 다시 시도합니다 (매 대화마다 호출하지 않음). 합치기가 계속 실패해서 `max_items` 의 1.5배를 넘으면 그제서야 오래된 것부터 지웁니다(안전장치). `consolidate: false` 로 끄면 예전처럼 오래된 순으로 지웁니다.
- **대화를 전체 삭제해도 기억은 남습니다.** 기억은 설정 창의 기억 탭에서 따로 지웁니다.

```yaml
memory:
  enabled: true        # 생략하면 켜짐
  extract_every: 6     # 내 메시지 N개마다 기억 추출 (0 이면 추출 안 함)
  max_items: 60
  summary: true
  keep_turns: 20
  summary_batch: 8
  consolidate: true  # 넘치면 비슷한 기억 합치기 (false 면 오래된 것부터 삭제)
```

## 웹 검색 (기본 구글, 실패하면 DuckDuckGo)

`pip install -U ddgs` 로 설치하면 켜집니다 (설치 전에는 검색 없이 동작하고, 서버 시작 때 안내가 나옵니다). **모델 기능이 아니라 서버가 직접 검색**하므로 Gemini 든 로컬 모델이든 똑같이 동작합니다.

1. 내 말이 오면 짧은 LLM 호출 한 번으로 "검색이 필요한가? 검색어는?"을 판단합니다 (날씨, 뉴스, 시세, 최신 정보, "검색해 줘" 등. 인사/잡담/짧은 말/이미지가 붙은 말은 판단 호출도 하지 않습니다).
2. 필요하면 `ddgs` 로 검색해서 제목/요약/날짜를 가져옵니다. 검색 엔진은 `search.engine`(런처 > 서버 설정 > 검색)으로 고릅니다: `google`(기본) / `duckduckgo` / `bing` / `brave` / `auto`. 구글/빙/브레이브는 스크래핑이라 막힐 수 있어서 **실패하면 자동으로 duckduckgo 로 넘어갑니다** (ddgs 의 `backend="google, duckduckgo"`). 뉴스 검색은 ddgs 가 구글을 지원하지 않아 항상 duckduckgo(`bing` 선택 시 bing→duckduckgo)를 씁니다.
3. 결과를 시스템 프롬프트에 넣고 캐릭터 말투로 답하게 합니다. 주소는 넣지 않고, 결과 안의 지시문은 따르지 않도록 안내하고, 못 찾거나 검색이 실패하면 지어내지 말고 솔직히 말하게 합니다.

검색 결과는 DB 에 남지 않습니다 (대화 기록에는 대화만 저장). 스트리밍 중에는 `stream_search {query}` 가 먼저 가서 클라이언트가 "🔍 검색 중…"을 보여 줍니다. 알림/먼저 말 걸기/화면 관찰에는 검색하지 않습니다. 판단/검색이 실패해도 검색 없이 그대로 답합니다. 검색할 때는 응답이 1~3초 정도 늦어지고 LLM 호출이 한 번 늘어납니다. DuckDuckGo 는 비공식 접근이라 가끔 제한(Ratelimit)에 걸릴 수 있습니다 — 그때는 검색 실패로 처리하고 대화는 계속됩니다.

```yaml
search:
  enabled: true      # 생략하면 켜짐 (ddgs 가 설치돼 있을 때만 동작)
  engine: google     # google | duckduckgo | bing | brave | auto
  max_results: 5
  region: kr-kr      # us-en, jp-jp, wt-wt(전체) 등
  timeout: 10
```

## 스트리밍 응답

클라이언트가 `chat` 에 `stream: true` 를 붙이면 답이 만들어지는 대로 보냅니다 (`stream` 설정이 true 이고 서버가 진짜 LLM 을 쓸 때). 순서: `stream_start` → (검색했다면 `stream_search {query}`) → `stream_meta {emotion, think}` (표정이 대사보다 먼저) → `stream_text {delta}` … → 문장이 끝날 때마다 `stream_audio {seq, audio_url, text}` (그 문장만 음성으로 만들어 먼저 재생) → 마지막에 `chat_result {streamed:true}` (전체 글/감정, 이미 보낸 음성은 다시 안 보냄). 도중에 LLM 이 실패하면 `error` 만 오고 **대화는 저장되지 않습니다**. `stream: false` 이거나 `stream` 을 안 붙이면 예전처럼 한 번에 `chat_result` 가 옵니다. `observe/notification/proactive` 는 항상 한 번에 옵니다.

## 검색 / 내보내기 / 기억 / 설정 프로토콜

| 클라이언트 → 서버 | 응답 |
|---|---|
| `session_search {query}` | `session_search_result {query, results:[{session_id,title,updated,role,snippet}]}` (제목 + 본문에서 찾고, 대화마다 가장 최근에 맞은 메시지 하나) |
| `session_export {session_id|null, format:"md"|"txt"}` | `session_export {filename, text, count}` (`session_id` 가 null 이면 모든 대화). 저장은 클라이언트가 합니다 |
| `memories_list` / `memory_add {text}` / `memory_update {id,text}` / `memory_delete {id}` / `memories_clear` | `memories {memories:[{id,text,created,updated}]}` (오류는 `error {code:"memory_error"}`) |
| `profile_set {model, persona, emotions}` | (응답 없음) 지금 모델의 persona / 감정 태그 |

## 기존 서버 대비 아직 없는 것

화면 관찰(observe), 피드백.

## 테스트

```
python -m unittest tests.test_all tests.test_features tests.test_stt
```

## 런처용 HTTP 경로

통합 런처가 상태를 보고 서버를 다루는 데 쓰는 경로입니다 (`/admin/*` 은 이 컴퓨터에서 온 요청만 받습니다).

| 경로 | 설명 |
|---|---|
| `GET /health` | 서버 상태: `stt`(ok/loading/idle/error/off), `tts`(ok/loading/down/off. GPT-SoVITS·RVC 포함), 접속한 클라이언트 수, 쓰는 LLM 이름. LLM 은 실시간으로 확인하지 않습니다 (아래 `[llm]` 로그로 확인) |
| `POST /admin/reload` | `config.yaml` 을 다시 읽어 실행 중인 설정에 반영 (문법이 틀리면 400 으로 거절하고 지금 설정을 그대로 둠) |
| `POST /admin/shutdown` | 서버를 정상 종료 (GPT-SoVITS/RVC 도 같이 끔) |

**LLM 로그**: 모든 LLM 호출 결과가 서버 콘솔에 `[llm] 성공 gemini/모델 (1.2s, 138자)` / `[llm] 실패 gemini/모델 (0.4s) · HTTP 429 …` 한 줄로 남습니다 (API 키는 나오지 않음). 모델이 사고 수준 파라미터를 거부해서 조용히 빼고 다시 보낸 경우도 `[llm] … 거부했어요` 로 남습니다. 런처의 로그 탭에서 "LLM" 보기로 이 줄만 모아 볼 수 있습니다.

## 가사 검색어 보정 (`POST /lyrics/normalize`)

클라이언트의 실시간 가사가 곡을 못 찾았을 때만 부릅니다. 본문 `{"title": "...", "artist": "..."}` 를 서버 LLM(사고 수준 off, 보조 호출)으로 정리해 `{"is_music": true, "candidates": [{"title", "artist"}]}` 를 돌려줍니다 (로마자 곡명 → 원문, 한국어 표기, 영어 표기 후보 최대 3개). 결과는 DB(`kv`)에 저장해서 같은 곡은 LLM 을 다시 부르지 않아요. 구현은 `lyrics_query.py`, 시험은 `python -m unittest tests.test_lyrics_query`.
