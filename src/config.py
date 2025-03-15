import yaml
from pathlib import Path

DATA_PATH = Path("./data")
CONFIG_PATH = DATA_PATH / "config.yaml"
SESSION_PATH = DATA_PATH / "session"
DIALOGS_PATH = DATA_PATH / "dialogs.yaml"
TEMP_PATH = DATA_PATH / "temp"
THREAD_POOL_SIZE = 1

def init_path(path:Path):
    if path.exists():
        return
    path.mkdir(exist_ok=True, parents=True)
    assert path.is_dir()


def load_config(path: str) -> dict:
    init_path(DATA_PATH)
    init_path(TEMP_PATH)
    # 读取 YAML 配置文件
    with open(path,  encoding="utf-8") as file:
        config = yaml.safe_load(file)
    download_path = Path(config["download"]["path"])
    if not download_path.is_absolute():
        download_path = DATA_PATH/download_path
    init_path(download_path)
    config["download"]["path"] = download_path.absolute().as_posix()
    return config
