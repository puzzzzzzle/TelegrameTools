import datetime

from telethon import TelegramClient
import logging
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from pathlib import Path
from . import utils
from . import config as cfg

logger = logging.getLogger(__name__)


class ChatMediaDownloader:
    """
   下载器
    """

    def __init__(self, client: TelegramClient, config: dict, chat_id: int, chat_name: str, self_config: dict):
        self.client = client
        self.config = config
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.self_config = self_config

        chat_title = config["download"]["file_path_prefix"]["chat_title"]
        assert isinstance(chat_title, bool)
        self.file_name_base = chat_name if chat_title else ""
        self.media_datetime = config["download"]["file_path_prefix"]["media_datetime"]
        assert isinstance(self.media_datetime, str)
        self.media_types = set(self.self_config["media_types"])

    def _needs_download(self, message) -> bool:
        if "all" in self.media_types:
            return True
        if isinstance(message, MessageMediaPhoto) and "photo" in self.media_types:
            return True

        if isinstance(message.media, MessageMediaDocument):
            # 根据文档类型进一步判断
            document = message.media.document
            if document is None:
                return False
            # todo: 检查文件类型
            # 检查文件类型
            for attr in document.attributes:
                if hasattr(attr, 'video') and "video" in self.media_types:
                    return True
                if hasattr(attr, 'voice') and "voice" in self.media_types:
                    return True
                if hasattr(attr, 'document') and "document" in self.media_types:
                    return True
            # 如果未匹配到特定类型，但允许下载文档
            if "document" in self.media_types:
                return True
        return False

    def _get_file_name(self, message) -> str | None:
        result = self.file_name_base
        if self.media_datetime != "":
            date: datetime.datetime = message.date
            format_data = date.strftime("%Y-%m")
            result += f"/{format_data}"
        if isinstance(message, MessageMediaPhoto) and "photo" in self.media_types:
            date: datetime.datetime = message.date
            file_name = f"{message.id}_{date.strftime('%Y%m%d_%H%M%S')}.jpg"
            result += f"/{file_name}"
            return result
        # 如果消息有媒体文件，尝试获取媒体文件名
        if message.media and isinstance(message.media, MessageMediaDocument):
            document = message.media.document
            if document:
                # 查找文件名属性
                for attr in document.attributes:
                    if hasattr(attr, 'file_name'):
                        file_name = attr.file_name
                        result += f"/{file_name}"
                        return result

        return None

    async def _download_one_file(self, file_name, message, file_path):
        client = self.client
        temp_path = cfg.TEMP_PATH / (file_name + ".tmp")
        # 强制清理
        temp_path.unlink(missing_ok=True)

        # 下载媒体文件, 先下载到 tmp 目录, 再移动到目标目录
        logger.info(f"Downloading {file_name}...")
        await client.download_media(message.media, temp_path.as_posix())

        # 移动到目标路径
        temp_path.rename(file_path.as_posix())
        logger.info(f"Downloaded {file_name}")

    async def download_all(self):
        """
        下载对话中的所有媒体文件到指定目录
        :param client:
        :param target_chat:
        :param download_path:
        :return:
        """
        # 获取目标对话
        client = self.client
        target_chat = self.chat_id
        download_path = Path(self.config["download"]["path"])

        chat = await client.get_entity(target_chat)

        # 获取对话中的消息总数
        total_messages = (await client.get_messages(chat, limit=0)).total
        logger.info(f"Total messages in chat: {total_messages}")

        # 获取对话中的消息
        async for message in client.iter_messages(chat):
            if message.media:
                if not self._needs_download(message):
                    continue
                file_name = self._get_file_name(message)

                if file_name is None:
                    logger.error(f"Failed to get file name for message {message.id}")
                    continue

                file_path = download_path / file_name

                # 检查文件是否已经存在
                if file_path.exists():
                    logger.info(f"File {file_name} already exists, skipping...")
                    continue
                await self._download_one_file(file_name, message, file_path)


async def download_by_config(client: TelegramClient, config: dict):
    dialogs: dict[str, str] = await utils.get_dialogs(client, use_cache=True)

    for key, chat_config in config["download"]["chats_to_download"].items():
        if key in dialogs:
            chat_id = key
            chat_name = dialogs[chat_id]
        else:
            # 找 dialogs 中 value == key 的 key
            matching_keys = [k for k, v in dialogs.items() if v == key]
            if not matching_keys:
                logger.warning(f"{key} not found in dialogs, ignore")
                continue
            # 如果有多个匹配项，只取第一个
            chat_id = matching_keys[0]
            chat_name = key
            logger.warning(f"multiple matching keys found: {matching_keys}, use {chat_id} instead")

        downloader = ChatMediaDownloader(client, config, int(chat_id), chat_name, chat_config)
        try:
            await downloader.download_all()
        except Exception as e:
            logger.error(f"Error downloading chat {chat_id}: {e}")
            continue
