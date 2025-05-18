import asyncio
import copy
import datetime

from telethon import TelegramClient
import logging
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, DocumentAttributeFilename
from pathlib import Path

from . import utils
from . import config as cfg
from .download_worker import DownloadTaskBase, DownloadWorkerMng, DownloadingInfo
from .config import get_id_cache_path

logger = logging.getLogger(__name__)


class MediaDownloadTask(DownloadTaskBase):
    """
    下载任务
    """

    def __init__(self, msg_id, max_retry_count, no_data_recv_time: int, chat_id: int, chat_name: str, file_name: str,
                 message,
                 file_path: Path, tag: str):
        super().__init__(max_retry_count, no_data_recv_time, msg_id, chat_id)
        self.chat_name = chat_name
        self.file_name = file_name
        self.message = message
        self.file_path = file_path
        self.tag = tag
        pass

    def __str__(self):
        return f"[{self.task_id}; {self.file_path}; {self.chat_name}; {self.tag}];"

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
        start_time = datetime.datetime.now()

        # 记录最后一次收到数据的时间
        last_progress_time = asyncio.get_event_loop().time()

        def callback(current, total):
            nonlocal last_progress_time
            # 更新最后一次收到数据的时间
            last_progress_time = asyncio.get_event_loop().time()
            logger.debug(f"{file_path} : {current} / {total}")
            if self.on_downloader_net_callback is not None:
                self.on_downloader_net_callback(self.task_id, f"{self.chat_name} - {self.file_path.name} - {self.tag}",
                                                start_time, datetime.datetime.now(), current, total)

        # 下载, 每次有收据时更新收到的时间
        async def download_with_timeout():
            task = asyncio.create_task(
                client.download_media(message.media, temp_path.as_posix(), progress_callback=callback))
            try:
                while True:
                    # 使用wait同时等待任务完成和超时检查
                    done, pending = await asyncio.wait(
                        [task],
                        timeout=1,  # 缩短检查间隔到1秒
                        return_when=asyncio.FIRST_COMPLETED
                    )

                    # 如果任务已完成
                    if task.done():
                        # 主动获取结果以抛出可能存在的异常
                        task.result()
                        break

                    # 检查超时
                    if asyncio.get_event_loop().time() - last_progress_time > self.no_data_recv_time:
                        raise asyncio.TimeoutError("long time not recv data, canceled")

            except Exception as e:
                task.cancel()  # 确保取消正在运行的任务
                logger.info(f"download fail {e} target_path:{file_path}")
                raise
            finally:
                if not task.done():
                    task.cancel()
                    await task  # 等待任务取消完成

        await download_with_timeout()
        # 移动到目标路径
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.rename(file_path.as_posix())
        logger.info(f"end {file_path}")


class ChatMediaDownloader:
    """
   下载器
    """

    def __init__(self, client: TelegramClient, config: dict, chat_id: int, chat_name: str, self_config: dict,
                 download_worker: DownloadWorkerMng, run_forever=False):
        self.client = client
        self.config = config
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.self_config = self_config
        self.download_worker = download_worker
        self.run_forever = run_forever

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
        media_size = 1
        if isinstance(message.media, MessageMediaPhoto):
            name = f"photo.jpg"
            media_type = "photo"
        if isinstance(message.media, MessageMediaDocument):
            document = message.media.document
            for attr in document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    name = attr.file_name
            media_size = document.size
            media_type_list = str(document.mime_type).split("/")
            if len(media_type) > 0:
                media_type = media_type_list[0]

        return name, media_type, media_size

    async def download_msg(self, message, tag: str) -> bool:
        download_path = Path(self.config["download"]["path"])
        # 获取基础信息
        msg_id = message.id
        date: datetime.datetime = message.date
        if isinstance(message, MessageMediaPhoto):
            name = f"{msg_id}"
            media_type = "photo"
            media_size = 1
            # media_size = message.photo.sizes
            pass
        elif message.media:
            name, media_type, media_size = self.get_media_meta(message)
        else:
            return True
        if name is None:
            return True
        # 类型过滤
        if "all" not in self.media_types and media_type not in self.media_types:
            return True

        target_path = download_path / self.chat_name
        if self.media_datetime != "":
            target_path = target_path / date.strftime(self.media_datetime)
        target_path.mkdir(parents=True, exist_ok=True)
        media_name = f"{msg_id} - {name}"
        target_save_path = target_path / media_name

        # 检查文件是否已经存在
        if target_save_path.exists():
            # 大小也相同
            if target_save_path.stat().st_size >= media_size:
                logger.info(f"cached {target_save_path}")
                return True
            logger.warning(
                f"target file exists but size not match, redownload {target_save_path.stat().st_size}/{media_size}: {target_save_path}")
            target_save_path.unlink(missing_ok=True)

        task = MediaDownloadTask(msg_id, 3, 180, self.chat_id, self.chat_name, media_name, message, target_save_path,
                                 tag)
        await self.download_worker.push_download_task(task)
        return False

    async def create_all_download_tasks(self):
        """
        下载对话中的所有媒体文件到指定目录
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

        # 获取之前的下载进度
        info_path = get_id_cache_path(self.chat_id)
        s = DownloadingInfo.load_from_file(info_path)

        tmp_ids: list[int] = copy.deepcopy(s.downloading_ids)
        tmp_ids.append(s.max_finished_id)
        min_id = min(tmp_ids)
        min_id = max(0, min_id - 5) # 防止漏
        logger.info(f"{self.chat_name}:  min_id: {min_id}")
        s.max_finished_id = min_id
        s.downloading_ids.clear()
        s.save_to_file(info_path)
        # 获取对话中的消息
        count = min_id
        async for message in client.iter_messages(chat, reverse=True, min_id=min_id):
            count += 1
            try:
                already_finished = await self.download_msg(message, f"{count}/{total_messages}")
                if already_finished:
                    DownloadingInfo.on_task_finish_impl(message.id, get_id_cache_path(self.chat_id))
            except Exception as e:
                logger.error(f"download fail {e}")


async def download_by_config(client: TelegramClient, config: dict):
    dialogs: dict[str, str] = await utils.get_dialogs(client, use_cache=True)
    download_worker = DownloadWorkerMng(config,max_parallel=2)
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
