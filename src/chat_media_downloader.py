import copy
import asyncio
import datetime
import re
import shutil
import time
from pprint import pformat
from telethon import TelegramClient
import logging
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, DocumentAttributeFilename
from telethon.errors.rpcerrorlist import FileReferenceExpiredError
import dataclasses
import json
from pathlib import Path

from . import utils
from . import config as cfg
from .config import get_id_cache_path

logger = logging.getLogger(__name__)


def is_telegram_message_link(text: str) -> bool:
    pattern = r'https?://(t\.me|telegram\.me)/[\w\d_]+/\d+'
    return re.match(pattern, text) is not None


def message_is_telegram_link(message) -> bool:
    # message: Telethon Message 对象
    if hasattr(message, 'message') and message.message:
        return is_telegram_message_link(message.message.strip())
    return False


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
    def on_task_finish_simple(cls, msg_id, chat_id, is_success: bool):
        file_path = get_id_cache_path(chat_id)
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

    def __init__(self, chat_id, msg_id, chat_name: str, file_name: str,
                 message,
                 file_path: Path, tag: str, need_record_finish: bool = True):
        self.retry_count = 0
        self.msg_id = msg_id
        self.chat_id = chat_id
        self.task_id = None
        self.chat_name = chat_name
        self.file_name = file_name
        self.message = message
        self.file_path = file_path
        self.tag = tag
        self.last_log_time = time.time()
        self.start_time = datetime.datetime.now()
        self.need_record_finish = need_record_finish

        pass

    def __str__(self):
        return f"[{self.task_id}; {self.file_path}; {self.chat_name}; {self.tag}];"

    async def download(self, client: TelegramClient):
        file_name = self.file_name
        message = self.message
        file_path = self.file_path

        temp_path = cfg.TEMP_PATH / self.chat_name / (file_name + ".tmp")
        temp_path.parent.mkdir(parents=True, exist_ok=True)

        # 断点续传起点，已下载字节数
        offset = temp_path.stat().st_size if temp_path.exists() else 0

        logger.info(f"Start downloading {file_path} from offset {offset}")

        # 获取文件大小（如果能获取）
        file_size = None
        if message.media and hasattr(message.media, 'document') and message.media.document:
            file_size = message.media.document.size

        # 以追加模式打开临时文件
        with open(temp_path, 'ab') as f:
            # 迭代下载，从offset开始
            stream = client.iter_download(
                message.media,
                offset=offset,
                chunk_size=512 * 1024,
                file_size=file_size
            )

            try:
                while True:
                    start_time = time.time()
                    # 给每个 chunk 的接收设置超时，比如 100 秒
                    chunk = await asyncio.wait_for(stream.__anext__(), timeout=100)
                    f.write(chunk)
                    offset += len(chunk)
                    time_use = time.time() - start_time
                    self.on_task_net_stat_event(
                        file_path,
                        offset,
                        file_size if file_size is not None else 1,
                        time_use,
                        len(chunk)
                    )

            except StopAsyncIteration:
                # 下载完成
                pass
            except asyncio.TimeoutError:
                # 超时处理
                logger.error("Chunk download timeout")
                await stream.aclose()  # 关闭异步生成器，释放资源
                raise
            except Exception as e:
                logger.error(f"Chunk download error: {type(e).__name__}: {e}", exc_info=True)
                raise
        # 下载完成，重命名临时文件到目标路径
        file_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"will move {temp_path} to {file_path}")
        shutil.move(str(temp_path), str(file_path))
        logger.info(f"Download finished: {file_path}")

    def on_task_net_stat_event(self, file_path,
                               recv_bytes: int,
                               total_bytes: int,
                               time_use: float,
                               time_recv: int):
        try:
            if time.time() - self.last_log_time > 10:
                self.last_log_time = time.time()
                status_show = {
                    "task_tag": f"{self.chat_name} - {file_path.name} - {self.tag}",
                    "time_use": str(datetime.datetime.now() - self.start_time),
                    "last_recv_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "progress": f'{recv_bytes / total_bytes:.2%} ({recv_bytes / 1024 / 1024:.2f} MB / {total_bytes / 1024 / 1024:.2f} MB)',
                    "speed": f'{(time_recv / 1024 / time_use):.5f} KB/s',
                }
                logger.info(f"\n{status_show['task_tag']} status:\n{pformat(status_show)}\n")
        except Exception as e:
            logger.info(f"on downloader net callback error: {e}", exc_info=True)

    async def download_direct(self, client):
        """
        直接下载, 不经过下载管理器
        :return:
        """
        await self.on_task_create()
        try:
            await self.download(client)
            if self.need_record_finish:
                await self.on_task_finished()
        except Exception as e:
            logger.error(f"err {self} with {e}")
            await self.on_task_error()
            raise

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

    def __init__(self, client: TelegramClient, config: dict, chat_id: int, chat_name: str, self_config: dict,
                 parallel: int):
        self.client = client
        self.config = config
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.self_config = self_config
        self.parallel = parallel

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

    async def download_by_link(self, message, tag: str) -> bool:
        try:
            # 解析链接
            match = re.match(r'https://t.me/([^/]+)/(\d+)', message.message.strip())
            if not match:
                raise ValueError('wrong link format')
            entity = match.group(1)
            message_id = int(match.group(2))
            # 获取频道/群组实体
            chat = await self.client.get_entity(entity)
            # 获取消息
            msg = await self.client.get_messages(chat, ids=message_id)
            if msg is None:
                logger.warning(f"msg not found, maybe deleted")
                return True
            if message_is_telegram_link(msg):
                # 不允许还是链接, 防止死循环
                return True
            if msg.grouped_id is not None:
                # 获取同组的所有消息(最多检查 30 条)
                messages = await self.client.get_messages(entity, ids=range(max(message_id - 20, 0), message_id + 20))
                # 过滤出同一个 grouped_id 的消息
                album_msgs = [curr for curr in messages if curr is not None and msg.grouped_id == curr.grouped_id]
                logger.info(f"message group all len {len(album_msgs)}")
                all_result = []
                for i, group_msg in enumerate(album_msgs, 1):
                    ret = await self.download_msg(group_msg, tag, message.id, f" - g{group_msg.id}", False)
                    all_result.append(ret)
                # 结束后统一记录当前消息完成
                DownloadingInfo.on_task_finish_simple(message.id, self.chat_id, True)
                return all(all_result)
            return await self.download_msg(msg, tag, message.id, f" - g{msg.id}")
        except Exception as e:
            logger.error(e, exc_info=True)
            raise

    async def download_msg(self, message, tag: str, specific_name_id: int | None = None, name_extra="",
                           need_record_finish=True) -> bool:
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
        elif message_is_telegram_link(message):
            return await self.download_by_link(message, tag)
        else:
            return True
        if name is None:
            return True
        # 类型过滤
        if "all" not in self.media_types and media_type not in self.media_types:
            return True
        if specific_name_id is not None:
            msg_id = specific_name_id

        target_path = download_path / self.chat_name
        if self.media_datetime != "":
            target_path = target_path / date.strftime(self.media_datetime)
        target_path.mkdir(parents=True, exist_ok=True)
        media_name = f"{msg_id}{name_extra} - {name}"
        target_save_path = target_path / media_name

        # 检查文件是否已经存在
        if target_save_path.exists():
            # 大小也相同
            if target_save_path.stat().st_size >= media_size:
                logger.info(f"cached {target_save_path}")
                return True
            logger.warning(
                f"target file exists but size not match, try continue {target_save_path.stat().st_size}/{media_size}: {target_save_path}")
            target_save_path.unlink(missing_ok=True)

        task = MediaDownloadTask(self.chat_id, msg_id, self.chat_name, media_name, message, target_save_path,
                                 tag, need_record_finish)
        await task.download_direct(client=self.client)
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
        min_id = max(0, min_id - 2)  # 防止漏
        logger.info(f"{self.chat_name}:  min_id: {min_id}")
        s.max_finished_id = min_id
        s.downloading_ids.clear()
        s.down_fail_ids.clear()
        s.save_to_file(info_path)
        # 获取对话中的消息
        semaphore = asyncio.Semaphore(self.parallel)  # 限制最大并发

        async def sem_download_msg(message, progress_str):
            async with semaphore:
                over_time_limit = 10
                while True:
                    try:
                        already_finished = await self.download_msg(message, progress_str)
                        if already_finished:
                            DownloadingInfo.on_task_finish_simple(message.id, self.chat_id, True)
                        break
                    except FileReferenceExpiredError as e:
                        logger.warning(f"refresh file reference")
                        over_time_limit -= 1
                        if over_time_limit <= 0:
                            break
                        logger.info(f"refresh file reference retry download")
                        message = await self.client.get_messages(self.chat_id, ids=message.id)
                    except Exception as e:
                        logger.error(f"download fail {e}")
                        break

        count = min_id
        tasks = []

        async for message in client.iter_messages(chat, reverse=True, min_id=min_id):
            count += 1
            progress_str = f"{count}/{total_messages}"
            # 重新 get 一遍 message 防止过期
            message = await self.client.get_messages(self.chat_id, ids=message.id)
            task = asyncio.create_task(sem_download_msg(message, progress_str))
            tasks.append(task)

        # 等待所有任务完成
        await asyncio.gather(*tasks)


async def download_by_config(client: TelegramClient, config: dict, parallel=1):
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
            if len(matching_keys) > 1:
                logger.warning(f"multiple matching keys found: {matching_keys}, use {chat_id} instead")

        # 创建下载任务
        curr_chat_downloader = ChatMediaDownloader(client, config, int(chat_id), chat_name, chat_config, parallel)
        try:
            await curr_chat_downloader.download_all_media()
        except Exception as e:
            logger.exception(e)
            pass
        logger.info(f"download {key} done")
