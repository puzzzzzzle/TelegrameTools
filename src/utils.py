from telethon import TelegramClient
import telethon.tl.functions as fns
import logging

logger = logging.getLogger(__name__)


async def get_dialogs(client: TelegramClient) -> dict[str, str]:
    # 获取所有对话
    result = {}
    dialogs = await client.get_dialogs()
    for dialog in dialogs:
        result[dialog.id] = dialog.name
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
