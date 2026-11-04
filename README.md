# Project Miku

<img width="1372" height="1007" alt="image" src="https://github.com/user-attachments/assets/d226f0ab-3772-4772-b3f9-1b4132cb7c6a" />

Live2D 캐릭터가 데스크톱에 떠 있는 AI 어시스턴트입니다. 대화(LLM), 음성 인식(STT), 음성 합성(TTS), 립싱크·표정 연동을 지원합니다.

## 구성

| 폴더 | 설명 |
|---|---|
| `Project_Miku_client/` | Electron + pixi-live2d-display 클라이언트 (화면, 마이크, 자막 말풍선) |
| `Project_Miku_server/` | FastAPI + SQLite 서버 (LLM 호출, 기억, TTS/STT) |
| `Project_Miku_launcher/` | 서버·클라이언트를 한 화면에서 켜고 끄는 통합 런처 (PySide6) |

## 요구 사항

- Windows
- Python 3.10+ (서버, 런처)
- Node.js (클라이언트)
- LLM API 키 (Gemini / OpenAI / Groq 중 하나) 또는 로컬 Ollama

## 빠른 시작

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

런처를 쓰면 위 과정을 GUI로 대신할 수 있습니다. 자세한 내용은 [런처 README](Project_Miku_launcher/README.md)를 보세요.
`MikuLauncher.exe`는 저장소가 아니라 [Releases](../../releases)에서 받으세요.

## 자세한 설명

- [클라이언트](Project_Miku_client/README.md) · [명령어](Project_Miku_client/COMMANDS.md)
- [서버](Project_Miku_server/README.md)
- [런처](Project_Miku_launcher/README.md)

## 개인 설정 파일

`config.yaml`(서버 API 키), `config.json`, `launcher.json`은 개인 설정이라 저장소에 올라가지 않습니다 (`.gitignore`).
기본값은 `config.example.yaml`, `config.default.json`에 있습니다.

## 라이선스

직접 작성한 코드는 [MIT](LICENSE.md)입니다.
Live2D Cubism Core, Hiyori 샘플 모델, 캐릭터(하츠네 미쿠 등), 설치 중 받는 패키지·모델은 각자의 라이선스를 따릅니다.
자세한 내용은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 확인하세요.
