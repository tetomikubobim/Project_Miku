"""설정 파일(config.yaml) 읽기."""
import os

LEGACY_LOCAL_NAME = "config.local.json"   # 예전 설정 창이 만들던 파일. 이제 읽지 않는다 (런처의 "설정 병합"으로 config.yaml 에 합친다)
_warned = False

YAML_HINT = "들여쓰기는 탭이 아니라 스페이스로, ':' 뒤에는 공백이 필요하고, 'no'/'on' 같은 글자는 따옴표로 감싸야 해요."


class ConfigError(Exception):
    pass


def load(base):
    path = os.path.join(base, "config.yaml")
    if not os.path.exists(path):
        hint = " (예전 config.json 은 더 이상 읽지 않아요. config.example.yaml 을 참고해 옮겨 주세요.)" if os.path.exists(os.path.join(base, "config.json")) else ""
        raise ConfigError("config.yaml 이 없어요. config.example.yaml 을 복사해서 config.yaml 로 만드세요." + hint)
    try:
        import yaml
    except ImportError:
        raise ConfigError("YAML 을 읽으려면 설치가 필요해요: pip install pyyaml") from None
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"config.yaml 문법 오류:\n{e}\n({YAML_HINT})") from None
    if not isinstance(cfg, dict):
        raise ConfigError("config.yaml 은 '항목: 값' 형태여야 해요 (비어 있거나 최상위가 목록이면 안 돼요).")
    global _warned
    if os.path.exists(os.path.join(base, LEGACY_LOCAL_NAME)) and not _warned:
        _warned = True
        print(f"[config] {LEGACY_LOCAL_NAME} 는 더 이상 읽지 않아요. 그 안의 값을 쓰려면 런처의 '설정 병합'을 실행하세요 (config.yaml 의 값만 적용 중).")
    return cfg


def replace_inplace(dst, src):
    """dst 를 src 와 같은 내용으로 그 자리에서 바꾼다. 딕셔너리 객체는 그대로 두고 안쪽만 바꿔서,
    설정 딕셔너리를 이미 들고 있는 곳(엔진, 음성 인식 등)에도 새 값이 보이게 한다."""
    for k in [k for k in dst if k not in src]:
        del dst[k]
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            replace_inplace(dst[k], v)
        else:
            dst[k] = v
    return dst
