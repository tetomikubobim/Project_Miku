# 서드파티 고지 (Third-Party Notices)

이 프로젝트의 코드는 [MIT 라이선스](LICENSE.md)입니다. 아래 항목은 **각자의 라이선스**를 따르며, 이 라이선스가 그 권리를 바꾸지 않습니다.
버전과 조건은 바뀔 수 있으니, 배포나 상업적 이용 전에는 각 항목의 원문을 확인하세요.

## 1. 저장소/배포 파일에 함께 들어 있는 것

| 항목 | 위치 | 라이선스 / 출처 |
|---|---|---|
| Live2D Cubism Core | `Project_Miku_client/vendor/live2dcubismcore.min.js` | (C) Live2D Inc. 파일 맨 위에 "Redistributable Code" 로 표시돼 있으며 [Live2D Proprietary Software License Agreement](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html) 를 따릅니다. **파일 맨 위의 저작권/라이선스 주석을 지우지 마세요.** 사업 규모에 따라 Live2D 와 별도 계약(Release License)이 필요할 수 있으니 직접 확인하세요. |
| Hiyori 샘플 모델 | `Project_Miku_client/models/Hiyori/` | Live2D Inc. 가 제공하는 샘플 모델이며 Live2D 의 Free Material License Agreement 를 따릅니다 ([live2d.com](https://www.live2d.com/)). 모델을 직접 추가할 때는 그 모델의 라이선스를 확인하세요. |
| 런처 실행 파일 `MikuLauncher.exe` + `_internal/` (Releases 로 배포) | GitHub Releases | PyInstaller 로 묶은 파이썬 런타임과 아래 라이브러리가 들어 있습니다 (아래 2번). |

## 2. 런처 (Releases 의 exe 에 포함)

| 라이브러리 | 라이선스 |
|---|---|
| **PySide6 / Qt for Python** (Qt 6 DLL 포함) | **LGPL-3.0** (또는 GPL-3.0 / 상용). Qt 와 PySide6 는 `_internal/PySide6/` 에 **공유 라이브러리(DLL)로 따로** 들어 있어서 교체할 수 있습니다. 소스: <https://code.qt.io/> · 라이선스 원문: <https://www.gnu.org/licenses/lgpl-3.0.html>. 배포 압축 파일에 LGPL-3.0 원문을 `licenses/` 폴더로 같이 넣어 주세요. |
| psutil | BSD-3-Clause |
| requests | Apache-2.0 |
| PyYAML | MIT |
| nvidia-ml-py (선택) | BSD-3-Clause |
| Python (런타임) | PSF License |
| PyInstaller (빌드 도구) | GPL-2.0 + bootloader exception (만들어진 exe 에는 GPL 이 적용되지 않음) |

## 3. 클라이언트 (설치 탭/`npm install` 로 받는 것, 저장소에는 없음)

| 패키지 | 라이선스 |
|---|---|
| Electron 33 | MIT |
| pixi.js 6 | MIT |
| pixi-live2d-display | MIT |
| onnxruntime-node | MIT |
| @ennuicastr/webrtcaec3.js | BSD-3-Clause |

## 4. 서버 (설치 탭/`pip` 로 받는 것, 저장소에는 없음)

| 패키지 | 라이선스 |
|---|---|
| FastAPI | MIT |
| Uvicorn | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| tzdata | Apache-2.0 |
| edge-tts | LGPL-3.0 (설치해서 쓰기만 하며 코드를 수정해 넣지 않음) |
| miniaudio | MIT |
| ddgs | MIT |
| faster-whisper (선택) | MIT |
| rvc-python (선택, RVC) | 설치 시점의 [저장소](https://github.com/daswer123/rvc-python) 라이선스를 확인하세요 |

## 5. 사용할 때 내려받는 모델

| 모델 | 출처 / 조건 |
|---|---|
| Silero VAD (`silero_vad.onnx`) | [snakers4/silero-vad](https://github.com/snakers4/silero-vad), MIT |
| WeSpeaker ResNet34 (`speaker.onnx`, 화자 인증) | [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 릴리스 파일. 모델 라이선스와 학습 데이터(VoxCeleb) 조건은 원 출처를 확인하세요 |
| Whisper 가중치 (faster-whisper 로 받음) | MIT |
| RVC 음성 모델: **츠쿠요미짱 공식 RVC 모델** (CV.夢前黎, © Rei Yumesaki). 배포 페이지: <https://tyc.rei-yumesaki.net/work/software/rvc/> | [공식 이용 약관](https://tyc.rei-yumesaki.net/work/software/rvc/terms/) 과 [캐릭터 라이선스](https://tyc.rei-yumesaki.net/about/terms/)를 따릅니다. **이 저장소에는 포함돼 있지 않고**, `get_tsukuyomi_rvc.py` 또는 런처 설치 탭의 옵션 항목(사용자가 버튼을 눌렀을 때만)이 공식 배포처에서 받아 필요한 `.pth` / `.index` 만 꺼냅니다 (파일 이름만 바뀌고 내용은 그대로). 사용 조건(요약, 원문 확인 필요): 특정 정치·종교·사상 찬반 호소 활동, 실존 인물·단체·상품 비판·공격 활동, 그런 활동을 하는 명의·계정에서의 사용, 타인 권리 침해 금지. 변환한 음성을 공개할 때는 **"변환 전 음성"과 "츠쿠요미짱 공식 RVC 모델" 크레딧**이 필요합니다. 변환 전 음성(이 프로젝트에서는 Edge TTS)의 이용 조건도 함께 확인하세요 |
| RVC 공통 모델 (`hubert_base.pt`, `rmvpe.pt`) | [lj1995/VoiceConversionWebUI](https://huggingface.co/lj1995/VoiceConversionWebUI), MIT. 이 프로젝트가 직접 받거나 동봉하지 않습니다 (RVC 실행 환경이 쓰는 것) |

## 6. 외부 서비스 (이 프로젝트와 무관한 제3자 서비스)

- **LLM**: Google Gemini, OpenAI 호환, Groq, Ollama. 각 서비스 약관을 따르며 API 키는 사용자가 직접 발급해서 `config.yaml` 에 넣습니다 (저장소에 키는 들어 있지 않습니다).
- **Edge TTS**: Microsoft Edge 의 읽기 기능에 비공식으로 접근하는 방식이라 언제든 막히거나 약관이 바뀔 수 있습니다.
- **알송 가사 서버**(`lyrics.alsong.co.kr`): 비공식 접근이며 HTTPS 가 아닙니다. 언제든 막힐 수 있습니다. 가사 저작권은 각 권리자에게 있고, 이 프로젝트는 가사를 저장소에 포함하지 않습니다.
- **LRCLIB** ([lrclib.net](https://lrclib.net)), **웹 검색**(ddgs 를 통한 Google/DuckDuckGo).

## 7. 캐릭터 / 상표

이 프로젝트는 **Crypton Future Media, Inc.** 및 "하츠네 미쿠"와 **관련이 없는 비공식 개인 프로젝트**입니다. 하츠네 미쿠 등 캐릭터의 권리는 각 권리자에게 있습니다. 캐릭터 관련 모델/음성을 사용하거나 배포할 때는 해당 권리자의 가이드라인(예: 피아프로 캐릭터 라이선스)을 확인하세요.

기본 음색으로 안내하는 **츠쿠요미짱(つくよみちゃん)** 은 © Rei Yumesaki 의 프리 소재 캐릭터이며, 이 프로젝트는 공식 프로젝트가 아닙니다. 사용 조건은 위 5번의 링크를 보세요.
