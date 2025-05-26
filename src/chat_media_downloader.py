import copy
import asyncio
import datetime
import time
from pprint import pformat
from typing import Callable
from telethon import TelegramClient
import logging
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, DocumentAttributeFilename
import dataclasses
import json
from pathlib import Path
from telethon.tl.functions.upload import GetFileRequest
from telethon.tl.types import InputDocumentFileLocation
from telethon.errors.rpcerrorlist import FileMigrateError

from . import utils
from . import config as cfg
from .config import get_id_cache_path

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class DownloadingInfo:
    max_finished_id: int = 0
    downloading_ids: list[int] = dataclasses.field(default_factory=list)
    down_fail_ids: list[int] = dataclasses.field(default_factory=list)
    chat_name: str = ""

    def save_to_file(self, file_path: str | Path):
        """
        使用json将对象序列化到文件
        :param file_path: 文件保存路径
        """
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(dataclasses.asdict(self), f, ensure_ascii=False, indent=4)

    @classmethod
    def load_from_file(cls, file_path: str | Path) -> "DownloadingInfo":
        """
        从json文件反序列化对象
        :param file_path: 文件读取路径
        :return: DownloadingInfo实例
        """
        file_path = Path(file_path)
        if not file_path.exists():
            return cls()
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        download_info_cache = cls(**data)
        return download_info_cache

    @classmethod
    def on_task_finish(cls, task, is_success: bool):
        msg_id = task.msg_id
        file_path = get_id_cache_path(task.chat_id)
        return cls.on_task_finish_impl(msg_id, file_path, is_success)

    @classmethod
    def on_task_finish_impl(cls, msg_id, file_path, is_success: bool):
        try:
            s = DownloadingInfo.load_from_file(file_path)
            try:
                s.downloading_ids.remove(msg_id)
                if not is_success:
                    s.down_fail_ids.append(msg_id)
            except ValueError:
                pass
            if msg_id > s.max_finished_id:
                s.max_finished_id = msg_id
            s.save_to_file(file_path)
        except Exception as e:
            logger.error(f"save to file error: {e}")
            logger.exception(e)
        pass

    @classmethod
    def on_task_create(cls, task):
        msg_id = task.msg_id
        file_path = get_id_cache_path(task.chat_id)
        try:
            s = DownloadingInfo.load_from_file(file_path)
            s.downloading_ids.append(msg_id)
            s.chat_name = task.chat_name
            s.save_to_file(file_path)
        except Exception as e:
            logger.error(f"save to file error: {e}")
            logger.exception(e)
        pass


class MediaDownloadTask(object):
    """
    下载任务
    """

    def __init__(self, msg_id, max_retry_count, no_data_recv_time: int, chat_id: int, chat_name: str, file_name: str,
                 message,
                 file_path: Path, tag: str):
        self.retry_count = 0
        self.max_retry_count = max_retry_count
        self.no_data_recv_time = no_data_recv_time
        self.msg_id = msg_id
        self.chat_id = chat_id

        # task_id, task_tag, start_time,last_recv_time,  recv_bytes, total_bytes
        self.on_downloader_net_callback: Callable[[int, str, datetime.datetime, datetime.datetime, int,
                                                   int], None] | None = None
        self.task_id = None
        self.chat_name = chat_name
        self.file_name = file_name
        self.message = message
        self.file_path = file_path
        self.tag = tag
        pass

    def __str__(self):
        return f"[{self.task_id}; {self.file_path}; {self.chat_name}; {self.tag}];"

    async def download(self, client: TelegramClient):
        file_name = self.file_name
        message = self.message
        file_path = self.file_path

        temp_path = cfg.TEMP_PATH / (file_name + ".tmp")
        temp_path.parent.mkdir(parents=True, exist_ok=True)

        downloaded_bytes = 0
        if temp_path.exists():
            downloaded_bytes = temp_path.stat().st_size

        if hasattr(message.media, 'document'):
            document = message.media.document
        else:
            raise ValueError("Media does not contain a document")

        total_size = document.size

        location = InputDocumentFileLocation(
            id=document.id,
            access_hash=document.access_hash,
            file_reference=document.file_reference,
            thumb_size=""
        )

        chunk_size = 512 * 1024  # 512KB
        start_time = datetime.datetime.now()
        last_progress_time = asyncio.get_event_loop().time()

        # 记录当前 DC
        original_dc = client.session.dc_id
        target_dc = getattr(document, 'dc_id', original_dc)  # 文件所在 DC

        # 如果文件不在当前 DC，切换
        if target_dc != original_dc:
            await client._switch_dc(target_dc)

        try:
            with open(temp_path, 'ab') as f:
                while downloaded_bytes < total_size:
                    if asyncio.get_event_loop().time() - last_progress_time > self.no_data_recv_time:
                        raise asyncio.TimeoutError("long time not recv data, canceled")

                    bytes_to_download = min(chunk_size, total_size - downloaded_bytes)

                    try:
                        # result = await client(GetFileRequest(
                        #     location=location,
                        #     offset=downloaded_bytes,
                        #     limit=bytes_to_download
                        # ))
                        await client.download_media(message, temp_path)
                    except FileMigrateError as e:
                        # 如果 DC 迁移错误，更新 target_dc 并切换
                        target_dc = e.new_dc
                        await client._switch_dc(target_dc)
                        continue  # 重新尝试下载
                    except Exception as e:
                        logger.info(f"download fail {e} target_path:{file_path}")
                        raise

                    if not result.bytes:
                        break

                    f.write(result.bytes)
                    downloaded_bytes += len(result.bytes)
                    last_progress_time = asyncio.get_event_loop().time()

                    if self.on_downloader_net_callback is not None:
                        self.on_downloader_net_callback(
                            self.task_id,
                            f"{self.chat_name} - {self.file_path.name} - {self.tag}",
                            start_time,
                            datetime.datetime.now(),
                            downloaded_bytes,
                            total_size
                        )

                    logger.debug(f"Downloaded {downloaded_bytes} / {total_size} bytes")
        finally:
            # 下载完成或异常后切回原 DC
            if client.session.dc_id != original_dc:
                await client._switch_dc(original_dc)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.rename(file_path.as_posix())
        logger.info(f"end {file_path}")

    async def download_direct(self, client, chat_name):
        """
        直接下载, 不经过下载管理器
        :return:
        """
        last_log_time = time.time()

        def on_task_net_stat_event(task_id: int, task_tag: str, start_time: datetime.datetime,
                                   last_recv_time: datetime.datetime, recv_bytes: int,
                                   total_bytes: int):
            # 每 5s 输出下信息
            nonlocal last_log_time
            if time.time() - last_log_time > 10:
                last_log_time = time.time()
                status_show = {
                    "task_tag": task_tag,
                    "time_use": str(datetime.datetime.now() - start_time),
                    "last_recv_time": last_recv_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "progress": f'{recv_bytes / total_bytes:.2%} ({recv_bytes / 1024 / 1024:.2f} MB / {total_bytes / 1024 / 1024:.2f} MB)',
                }
                logger.info(f"\n{chat_name} stat:\n {pformat(status_show)}")

        await self.on_task_create()
        try:
            self.on_downloader_net_callback = on_task_net_stat_event
            await self.download(client)
            await self.on_task_finished()
        except Exception as e:
            logger.error(f"err {self} with {e}")
            await self.on_task_error()

    async def on_task_create(self):
        logger.info(f"+++ {self}")
        DownloadingInfo.on_task_create(self)
        pass

    async def on_task_finished(self):
        logger.info(f"--- {self}")
        DownloadingInfo.on_task_finish(self, True)
        pass

    async def on_task_error(self):
        DownloadingInfo.on_task_finish(self, False)
        logger.error(f"err {self}")


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
                f"target file exists but size not match, try continue {target_save_path.stat().st_size}/{media_size}: {target_save_path}")

        task = MediaDownloadTask(msg_id, 3, 180, self.chat_id, self.chat_name, media_name, message, target_save_path,
                                 tag)
        await task.download_direct(client=self.client, chat_name=self.chat_name)
        return False

    async def download_all_media(self):
        """
        下载对话中的所有媒体文件到指定目录
        :return:
        """
        # 获取目标对话
        client = self.client
        target_chat = self.chat_id
        logger.info(f"start create tasks for {self.chat_name} id: {target_chat}")
        chat = await client.get_entity(target_chat)

        # 获取对话中的消息总数
        total_messages = (await client.get_messages(chat, limit=0)).total
        logger.info(f"Total messages in chat: {total_messages}")

        # 获取之前的下载进度
        info_path = get_id_cache_path(self.chat_id)
        s = DownloadingInfo.load_from_file(info_path)

        tmp_ids: list[int] = copy.deepcopy(s.downloading_ids)
        tmp_ids.append(s.max_finished_id)
        tmp_ids.extend(s.down_fail_ids)
        min_id = min(tmp_ids)
        min_id = max(0, min_id - 5)  # 防止漏
        logger.info(f"{self.chat_name}:  min_id: {min_id}")
        s.max_finished_id = min_id
        s.downloading_ids.clear()
        s.down_fail_ids.clear()
        s.save_to_file(info_path)
        # 获取对话中的消息
        count = min_id
        async for message in client.iter_messages(chat, reverse=True, min_id=min_id):
            count += 1
            try:
                already_finished = await self.download_msg(message, f"{count}/{total_messages}")
                if already_finished:
                    DownloadingInfo.on_task_finish_impl(message.id, get_id_cache_path(self.chat_id),True)
            except Exception as e:
                logger.error(f"download fail {e}")


async def download_by_config(client: TelegramClient, config: dict, parallel=1):
    dialogs: dict[str, str] = await utils.get_dialogs(client, use_cache=True)
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
        curr_chat_downloader = ChatMediaDownloader(client, config, int(chat_id), chat_name, chat_config)
        downloaders.append(curr_chat_downloader)
        await curr_chat_downloader.download_all_media()
    # wait_stop = []
    # for downloader in downloaders:
    #     task = asyncio.create_task(downloader.download_all_media())
    #     wait_stop.append(task)
    # await asyncio.gather(*wait_stop)
    # await asyncio.gather(*[x.create_all_download_tasks() for x in downloaders])
    # 等待下载完毕
    # while not download_worker.is_all_done():
    #     await asyncio.sleep(10)
    # download_worker.mark_stopped()
    # download_worker.wait_all_thread()
