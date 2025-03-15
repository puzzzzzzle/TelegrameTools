import yaml
from pathlib import Path

DATA_PATH = Path("./data")
CONFIG_PATH = DATA_PATH / "config.yaml"
SESSION_PATH = DATA_PATH / "session"
DIALOGS_PATH = DATA_PATH / "dialogs.yaml"


def _init_data_path():
    if DATA_PATH.exists():
        return
    DATA_PATH.mkdir(exist_ok=True, parents=True)
    assert DATA_PATH.is_dir()


def load_config(path: str) -> dict:
    _init_data_path()
    # 读取 YAML 配置文件
    with open(path,  encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config
