import asyncio
import datetime
import shutil
from operator import index

from telethon import TelegramClient
import logging
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, DocumentAttributeFilename
from pathlib import Path
from . import utils
from . import config as cfg
from .download_worker import DownloadTaskBase, DownloadWorkerMng

logger = logging.getLogger(__name__)


class MediaDownloadTask(DownloadTaskBase):
    """
    下载任务
    """

    def __init__(self, chat_id: int, chat_name: str, file_name: str, message, file_path: Path, max_retry_count,
                 tag: str):
        super().__init__(max_retry_count)
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.file_name = file_name
        self.message = message
        self.file_path = file_path
        self.tag = tag
        pass

    def __str__(self):
        return f"[{self.file_path}; {self.chat_name}; {self.tag}];"

    async def download(self, client: TelegramClient):
        """
        下载媒体文件
        :param client:
        :return:
        """
        file_name = self.file_name
        message = self.message
        file_path = self.file_path

        temp_path = cfg.TEMP_PATH / (file_name + ".tmp")
        # 强制清理
        temp_path.unlink(missing_ok=True)

        # 下载媒体文件, 先下载到 tmp 目录, 再移动到目标目录
        logger.info(f"beg {file_path}")
        await client.download_media(message.media, temp_path.as_posix())

        # 移动到目标路径
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.rename(file_path.as_posix())
        logger.info(f"end {file_path}")


class ChatMediaDownloader:
    """
   下载器
    """

    def __init__(self, client: TelegramClient, config: dict, chat_id: int, chat_name: str, self_config: dict,
                 download_worker: DownloadWorkerMng):
        self.client = client
        self.config = config
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.self_config = self_config
        self.download_worker = download_worker

        chat_title = config["download"]["file_path_prefix"]["chat_title"]
        assert isinstance(chat_title, bool)
        self.file_name_base = chat_name if chat_title else ""
        self.media_datetime = config["download"]["file_path_prefix"]["media_datetime"]
        assert isinstance(self.media_datetime, str)
        self.media_types = set(self.self_config["media_types"])

    @staticmethod
    def get_media_meta(message):
        name = None
        media_type = "Unknown"
        if isinstance(message.media, MessageMediaPhoto):
            name = f"photo.jpg"
            media_type = "photo"
        if isinstance(message.media, MessageMediaDocument):
            document = message.media.document
            for attr in document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    name = attr.file_name
            media_type_list = str(document.mime_type).split("/")
            if len(media_type) > 0:
                media_type = media_type_list[0]

        return name, media_type

    async def download_msg(self, message, tag: str):
        download_path = Path(self.config["download"]["path"])
        # 获取基础信息
        msg_id = message.id
        date: datetime.datetime = message.date
        if isinstance(message, MessageMediaPhoto):
            name = f"{msg_id}"
            media_type = "photo"
            pass
        elif message.media:
            name, media_type = self.get_media_meta(message)
        else:
            return
        if name is None:
            return
        # 类型过滤
        if "all" not in self.media_types and media_type not in self.media_types:
            return

        target_path = download_path / self.chat_name
        if self.media_datetime != "":
            target_path = target_path / date.strftime(self.media_datetime)
        target_path.mkdir(parents=True, exist_ok=True)
        media_name = f"{msg_id} - {name}"
        target_save_path = target_path / media_name

        # 检查文件是否已经存在
        if target_save_path.exists():
            logger.info(f"cached {target_save_path}")
            return

        # TODO 临时代码: 如果 target_path 文件夹下有以 id 开头的文件, 且后缀相同, 就也认为也下载过了, 重命名过去吧
        for existing_file in target_path.iterdir():
            if existing_file.is_file() and existing_file.name.startswith(f"{msg_id}"):
                existing_suffix = existing_file.suffix
                new_suffix = target_save_path.suffix
                if existing_suffix == new_suffix:
                    logger.info(
                        f"cached {target_save_path}; already exists with the same suffix, renaming to {media_name}")
                    shutil.move(existing_file, target_save_path)
                    return

        task = MediaDownloadTask(self.chat_id, self.chat_name, media_name, message, target_save_path, 3, tag)
        await self.download_worker.push_download_task(task)

    async def create_all_download_tasks(self):
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
        logger.info(f"start create tasks for {self.chat_name}")
        chat = await client.get_entity(target_chat)

        # 获取对话中的消息总数
        total_messages = (await client.get_messages(chat, limit=0)).total
        logger.info(f"Total messages in chat: {total_messages}")

        # 获取对话中的消息
        count = 0
        async for message in client.iter_messages(chat, reverse=True):
            count += 1
            try:
                await self.download_msg(message, f"{count}/{total_messages}")
            except Exception as e:
                logger.error(f"download fail {e}")


async def download_by_config(client: TelegramClient, config: dict):
    dialogs: dict[str, str] = await utils.get_dialogs(client, use_cache=True)
    download_worker = DownloadWorkerMng()
    download_worker.start(client)
    downloaders = []
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
            if len(matching_keys) > 1:
                logger.warning(f"multiple matching keys found: {matching_keys}, use {chat_id} instead")

        # 创建下载任务
        curr_chat_downloader = ChatMediaDownloader(client, config, int(chat_id), chat_name, chat_config,
                                                   download_worker)
        downloaders.append(curr_chat_downloader)
    await asyncio.gather(*[x.create_all_download_tasks() for x in downloaders])
    # 等待下载完毕
    while not download_worker.is_all_done():
        await asyncio.sleep(10)
    download_worker.mark_stopped()
    download_worker.wait_all_thread()
