import yaml
from telethon import TelegramClient
import telethon.tl.functions as fns
import logging
from . import config as cfg

logger = logging.getLogger(__name__)


async def get_dialogs(client: TelegramClient, use_cache=False) -> dict[str, str]:
    # 获取所有对话
    result = {}
    if use_cache and cfg.DIALOGS_PATH.exists():
        with open(cfg.DIALOGS_PATH, "rt", encoding="utf-8") as f:
            result = yaml.safe_load(f)
            return result
    dialogs = await client.get_dialogs()
    for dialog in dialogs:
        result[str(dialog.id)] = str(dialog.name)
    with open(cfg.DIALOGS_PATH, "wt", encoding="utf-8") as f:
        yaml.dump(result, f, allow_unicode=True)
    logger.info(f"dialogs has been write to: {cfg.DIALOGS_PATH}")
    return result


async def clear_all_personal_chats(client: TelegramClient, reserved_set: set[str] | None = None):
    """
    清空所有私聊对话
    :param client:
    :param reserved_set: 要保留的对话列表
    :return:
    """
    # 清空所有私聊对话
    if reserved_set is None:
        reserved_set = set()

    # 获取对话列表
    total_dialogs_count = 0
    async for dialog in client.iter_dialogs():
        total_dialogs_count += 1
        if not dialog.is_user:
            continue
        name = dialog.title or dialog.name
        if name in reserved_set:
            continue
        print(f'will delete dialog: {name}')
        await client(
            fns.messages.DeleteHistoryRequest(peer=dialog.dialog.peer, max_id=0, just_clear=True))
        print(f'deleted {dialog.name}')
    print(f'total check dialog count {total_dialogs_count}')
