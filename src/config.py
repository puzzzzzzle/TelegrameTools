import yaml

SESSION_NAME = "session"


def load_config(path: str) -> dict:
    # 读取 YAML 配置文件
    with open('config.yaml', 'r') as file:
        config = yaml.safe_load(file)
    return config
