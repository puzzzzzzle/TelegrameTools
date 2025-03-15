from telethon import TelegramClient
import logging
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from pathlib import Path

logger = logging.getLogger(__name__)

def _needs_download():
    pass

async def download_one_chat(client: TelegramClient, target_chat: int, download_path: str):
    """
    下载对话中的所有媒体文件到指定目录
    :param client:
    :param target_chat:
    :param download_path:
    :return:
    """
    # 获取目标对话
    chat = await client.get_entity(target_chat)

    # 获取对话中的消息总数
    total_messages = (await client.get_messages(chat, limit=0)).total
    logger.info(f"Total messages in chat: {total_messages}")

    # 获取对话中的消息
    async for message in client.iter_messages(chat):
        if message.media:
            # 获取媒体文件的文件名
            if isinstance(message.media, MessageMediaPhoto):
                file_name = f"{message.id}.jpg"
            elif isinstance(message.media, MessageMediaDocument):
                file_name = message.media.document.attributes[0].file_name
            else:
                continue

            file_path = Path(download_path) / file_name

            # 检查文件是否已经存在
            if file_path.exists():
                logger.info(f"File {file_name} already exists, skipping...")
                continue

            # 下载媒体文件
            logger.info(f"Downloading {file_name}...")
            await client.download_media(message.media, file_path.as_posix())
