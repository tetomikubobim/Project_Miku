<div align="center">

# 🎤 Project Miku

**Live2D 캐릭터의 스킨을 쓴 AI 어시스턴트**

말을 걸면 듣고, 생각하고, 표정을 지으며, 목소리로 대답합니다.

![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Electron](https://img.shields.io/badge/Electron-33-47848F?logo=electron&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

<!-- 대표 이미지 또는 GIF를 아래 주석을 풀고 경로를 맞춰 넣으세요
<img src="docs/images/hero.gif" width="720" alt="Project Miku 미리보기">
-->

[다운로드](../../releases) · [빠른 시작](#-빠른-시작) · [기능](#-주요-기능) · [문서](#-문서)

</div>

---

## ✨ 주요 기능

| | 기능 | 설명 |
|---|---|---|
| 💬 | **대화 (LLM)** | Gemini / OpenAI 호환 / Groq / 로컬 Ollama 중 선택. 최근 대화와 장기 기억을 함께 사용 |
| 🗣️ | **음성 합성 (TTS)** | edge-tts 또는 GPT-SoVITS, 필요하면 RVC 변환. 립싱크 연동 |
| 🎙️ | **음성 인식 (STT)** | faster-whisper를 로컬에서 실행. 외부 STT 서버 불필요 |
| 🔊 | **음성 분석** | Silero VAD, WebRTC AEC3 에코 제거, 화자 인증 |
| 😊 | **표정 · 모션 연동** | 감정 태그를 모델의 표정/모션에 연결. 모델별로 따로 관리 |
| 🧑‍🎤 | **모델별 persona** | 모델마다 캐릭터 설정, 감정 표, 대화 기록, 음성을 따로 저장 |
| 🎵 | **실시간 가사** | 재생 중인 곡을 감지해 가사 표시 (알송 + LRCLIB), 번역 줄 지원 |
| 👀 | **시선 · 화면 공유** | 마우스를 따라보는 시선, 화면 캡처를 보여 주고 대화 가능 |
| ⏰ | **음성 명령 · 알림** | 음악 제어, 볼륨, 일정/타이머, 먼저 말 걸기를 서버 없이 즉시 처리 |
| 🚀 | **통합 런처** | 서버/클라이언트 실행, 설정 편집, 로그, 리소스 확인, 모듈 설치를 한 화면에서 |

<!-- 기능별 스크린샷 자리 (주석을 풀고 사용)
<table>
  <tr>
    <td><img src="docs/images/chat.png" alt="대화"></td>
    <td><img src="docs/images/lyrics.png" alt="가사"></td>
  </tr>
  <tr>
    <td align="center">대화와 표정</td>
    <td align="center">실시간 가사</td>
  </tr>
</table>
-->

## 🎬 데모

<!-- 영상은 GitHub 이슈/PR 입력창에 파일을 끌어다 놓으면 생기는 주소를 붙여 넣으면 됩니다
https://github.com/user-attachments/assets/...
-->

*(준비 중)*

## 🧩 구성

```
Project_Miku/
├─ Project_Miku_client/     Electron + pixi-live2d-display  (화면, 마이크, 말풍선)
├─ Project_Miku_server/     FastAPI + SQLite                (LLM, 기억, TTS/STT)
└─ Project_Miku_launcher/   PySide6 통합 런처               (실행 · 설정 · 설치)
```

```mermaid
flowchart LR
    M[🎙️ 마이크] --> C[클라이언트<br/>AEC · VAD · 화자 인증]
    C -->|WebSocket| S[서버]
    S --> STT[STT<br/>faster-whisper]
    S --> LLM[LLM<br/>Gemini / OpenAI / Groq / Ollama]
    S --> TTS[TTS<br/>edge / GPT-SoVITS / RVC]
    TTS --> C
    C --> L[🧑‍🎤 Live2D 모델<br/>표정 · 립싱크 · 말풍선]
```

## 📋 요구 사항

- Windows
- Python 3.10 이상 (서버, 런처)
- Node.js (클라이언트)
- LLM API 키 (Gemini / OpenAI / Groq 중 하나) **또는** 로컬 Ollama

## 🚀 빠른 시작

### 방법 1. 런처로 한 번에 (추천)

1. [Releases](../../releases)에서 `MikuLauncher.exe`를 다운합니다.
2. 'Project_Miku_server/config.example.yaml'의 사본을 만든 다음, 파일 이름을 'config.yaml'로 고칩니다.
4. **설치** 탭에서 서버 패키지와 클라이언트를 설치합니다. (음성 인식, RVC, GPT-SoVITS는 선택 설치)
5. **서버 설정** 탭에서 LLM 제공자와 API 키를 입력합니다.
6. **런처** 탭에서 서버와 클라이언트를 켭니다.

### 방법 2. 직접 실행

폴더 세 개를 나란히 둔 채로 진행하세요.

```bash
# 1) 서버
cd Project_Miku_server
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy config.example.yaml config.yaml    # 열어서 api_key 입력
python server.py

# 2) 클라이언트 (새 터미널)
cd Project_Miku_client
npm install
npm start
```

<details>
<summary><b>LLM 제공자별 설정 예시</b></summary>

`Project_Miku_server/config.yaml`에서 `llm.provider`를 고릅니다.

| 제공자 | 값 | 비고 |
|---|---|---|
| Google Gemini | `gemini` | `llm.gemini.api_key` |
| OpenAI 호환 | `openai` | OpenAI, OpenRouter, 로컬 서버 등 (`base_url` 지정) |
| Groq | `groq` | `llm.groq.api_key` |
| Ollama | `ollama` | 로컬 실행, API 키 불필요 |

</details>

## 🧑‍🎤 나만의 캐릭터 만들기

1. `Project_Miku_client/models/<모델이름>/`에 Live2D 모델 폴더를 넣습니다. (VTube Studio 모델 폴더 그대로 사용 가능하며, 편집 역시 가능합니다.)
2. 모델 폴더 안에 `persona.txt`를 만들어 캐릭터 설정을 적습니다(클라이언트의 설정창에서도 작성 가능). 호칭과 이름도 여기에 적습니다.
3. `emotionAliases.json`으로 감정 태그와 표정을 연결합니다. 클라이언트 설정창의 **모델** 탭에서 고를 수도 있습니다.
4. 트레이 메뉴에서 모델을 선택하면 끝입니다.

예시 파일은 `persona_example.txt`, `emotionAliases_example.json`에 있습니다.

## 📚 문서

| 문서 | 내용 |
|---|---|
| [클라이언트](Project_Miku_client/README.md) | 모델 추가, 감정 연결, 설정 창 |
| [음성 명령 가이드](Project_Miku_client/COMMANDS.md) | 음악, 볼륨, 일정, 먼저 말 걸기 |
| [서버](Project_Miku_server/README.md) | `config.yaml` 설명, STT/TTS, 기억 |
| [런처](Project_Miku_launcher/README.md) | 설치 탭, 설정 편집, 로그, 종료 방식 |

## 🔒 개인 설정 파일

`config.yaml`(API 키), `config.json`, `launcher.json`은 개인 설정이라 저장소에 올라가지 않습니다 (`.gitignore`).
기본값은 `config.example.yaml`, `config.default.json`에 있습니다.

## ⚠️ 알아두세요

- GPT-SoVITS와 RVC의 **음성 모델은 포함되어 있지 않습니다.** 사용하려는 목소리의 사용 권한을 직접 확인하고 준비해 주세요.
- 설치 중 내려받는 패키지와 모델은 각자의 라이선스를 따릅니다.

## 📄 라이선스

직접 작성한 코드는 [MIT](LICENSE.md)입니다.
Live2D Cubism Core, Hiyori 샘플 모델, 캐릭터(하츠네 미쿠 등), 설치 중 받는 패키지·모델은 각자의 라이선스를 따릅니다.
자세한 내용은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 확인하세요.
