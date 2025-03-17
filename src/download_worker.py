import asyncio
import datetime
import json
import logging
import math
import threading
from typing import Callable

from telethon import TelegramClient

from . import config as cfg
from .config import DATA_PATH

logger = logging.getLogger(__name__)


class DownloadTaskBase:
    """
    下载任务
    """

    def __init__(self, max_retry_count: int, no_data_recv_time: int):
        """
        下载任务
        :param max_retry_count: 最大重试次数
        :param no_data_recv_time: 没有收到数据, 最大等待时间
        """
        self.retry_count = 0
        self.max_retry_count = max_retry_count
        self.no_data_recv_time = no_data_recv_time

        # task_id, task_tag, start_time,last_recv_time,  recv_bytes, total_bytes
        self.on_downloader_net_callback: Callable[[int, str, datetime.datetime, datetime.datetime, int,
                                                   int], None] | None = None
        self.task_id = None
        pass

    async def download(self, client: TelegramClient):
        pass

    def __str__(self):
        return f"task_{self.task_id}"


class DownloadWorker:
    """
    下载工作线程
    """

    def __init__(self, index: int, stop_event: threading.Event, max_parallel: int, download_tasks: asyncio.Queue,
                 finished_tasks: asyncio.Queue,
                 error_tasks: asyncio.Queue):
        self.index = index
        self.stop_event = stop_event
        self.max_parallel = max_parallel
        self.download_tasks = download_tasks
        self.finished_tasks = finished_tasks
        self.error_tasks = error_tasks

        self.client: TelegramClient | None = None

        self.curr_parallel = 0
        self.curr_parallel_lock = threading.Lock()  # 创建一个锁
        self.task_status = {}

    def increment_curr_parallel(self):
        with self.curr_parallel_lock:  # 加锁
            self.curr_parallel += 1

    def decrement_curr_parallel(self):
        with self.curr_parallel_lock:  # 加锁
            self.curr_parallel -= 1

    def get_curr_parallel(self):
        with self.curr_parallel_lock:  # 加锁
            return self.curr_parallel

    def on_task_net_stat_event(self, task_id: int, task_tag: str, start_time: datetime.datetime,
                               last_recv_time: datetime.datetime, recv_bytes: int,
                               total_bytes: int):
        self.task_status[task_id] = {
            "task_tag": task_tag,
            "start_time": start_time,
            "last_recv_time": last_recv_time,
            "recv_bytes": recv_bytes,
            "total_bytes": total_bytes,
        }

    def dump_status(self):
        """
        保存状态统计信息
        :return:
        """
        try:
            status_show = {
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "curr_parallel": self.curr_parallel,
            }
            v: dict
            for k, v in self.task_status.items():
                if len(v) == 0:
                    continue
                status_show[k] = {
                    "task_tag": v["task_tag"],
                    "time_use": str(datetime.datetime.now() - v["start_time"]),
                    "last_recv_time": v["last_recv_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    "progress": f'{v["recv_bytes"] / v["total_bytes"]:.2%} ({v["recv_bytes"] / 1024 / 1024:.2f} MB / {v["total_bytes"] / 1024 / 1024:.2f} MB)',
                }
            logger.debug(f"Worker{self.index} status:{status_show}")
            with open(DATA_PATH / f"Worker{self.index}.status", "wt", encoding="utf-8") as f:
                json.dump(status_show, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"dump status error: {e}")

    async def try_download_one(self):
        client = self.client
        try:
            task: DownloadTaskBase = self.download_tasks.get_nowait()
        except asyncio.QueueEmpty as e:
            return

        self.increment_curr_parallel()
        self.task_status[task.task_id] = {}
        task.on_downloader_net_callback = self.on_task_net_stat_event
        try:
            await task.download(client)
            self.download_tasks.task_done()
            await self.finished_tasks.put(task)
        except Exception as e:
            task.retry_count += 1
            logger.error(f"{task} failed : {e}")
            if task.retry_count < task.max_retry_count:
                await self.download_tasks.put(task)
            else:
                logger.error(f"{task} retry count exceed {task.max_retry_count}")
                await self.error_tasks.put(task)
        finally:
            self.decrement_curr_parallel()
            del self.task_status[task.task_id]

    async def main(self, client: TelegramClient):
        assert self.client is None
        self.client = client

        last_dump_status_time = datetime.datetime.now()
        while not self.stop_event.is_set():
            if datetime.datetime.now() - last_dump_status_time > datetime.timedelta(seconds=3):
                self.dump_status()
                last_dump_status_time = datetime.datetime.now()
            if self.curr_parallel < self.max_parallel:
                # 创建一个下载任务, 不要等待
                asyncio.create_task(self.try_download_one())
            # 等待一会, 不要创建太快/太多
            await asyncio.sleep(1)

    def start(self, client: TelegramClient):
        """
        在外部提供的 asyncio 环境中运行
        :param client:
        :return:
        """
        # 创建协程, 不等待
        asyncio.create_task(self.main(client))

    def thread_main(self):
        """
        独立线程中, 创建一个client 独立运行
        :return:
        """

        # TODO 拷贝主 session
        raise NotImplementedError("Not implemented")
        # 用 asyncio 的独立循环, 最大并行 max_parallel 进行下载
        # async def _run():
        #     # 创建 TelegramClient
        #     client = TelegramClient(
        #         session=cfg.SESSION_NAME,
        #         api_id=cfg.API_ID,
        #         api_hash=cfg.API_HASH
        #     )
        #
        #     # 启动客户端
        #     async with client:
        #         await client.start()
        #         await self.main(client)
        #
        # # 创建独立的事件循环并运行
        # loop = asyncio.new_event_loop()
        # asyncio.set_event_loop(loop)
        # loop.run_until_complete(_run())

    def thread_start(self):
        t = threading.Thread(target=self.thread_main)
        t.start()
        self.thread = t


class DownloadWorkerMng:
    """
    下载任务管理器, 多线程+多协程 同时下载
    """

    def __init__(self, worker_thread_num: int = 0, max_parallel: int = 5, mng_use_thread: bool = False):
        """
        下载管理器, 支持当前线程/启动任意个独立线程
        :param worker_thread_num: 工作线程数量, 如果为 0 , 就在当前线程的 asyncio 中运行
        """
        self.thread = None
        self.mng_use_thread = mng_use_thread
        # mng 使用线程, 下载也得用线程
        if self.mng_use_thread and worker_thread_num == 0:
            logger.warning("mng_use_thread is True, but worker_thread_num is 0, use worker_thread_num = 1")
            self.worker_thread_num = 1
        self.last_task_id = 0

        # 配置
        self.worker_thread_num = worker_thread_num
        self.max_parallel = max_parallel
        if worker_thread_num == 0:
            self.worker_max_parallel = max_parallel
        else:
            self.worker_max_parallel = math.ceil(max_parallel / worker_thread_num)

        # 各种事件队列
        self.downloading_tasks = asyncio.Queue(maxsize=10)
        self.finished_tasks = asyncio.Queue()
        self.error_tasks = asyncio.Queue()
        self.stopped = threading.Event()

        # workers
        self.workers = [
            DownloadWorker(i, self.stopped, self.worker_max_parallel, self.downloading_tasks, self.finished_tasks,
                           self.error_tasks)
            for i in range(max(1, worker_thread_num))]
        pass

    async def push_download_task(self, task: DownloadTaskBase):
        """
        添加下载任务, 异步线程会 从 chat_tasks 中获取任务, 逐个执行
        """
        self.last_task_id += 1
        task.task_id = self.last_task_id
        await self.downloading_tasks.put(task)
        await self.on_task_create(task)

    async def on_task_create(self, task):
        logger.info(f"+++ {task} ; stat: {self.simple_stat()}")
        pass

    async def on_task_finished(self, task):
        logger.info(f"--- {task} ; stat: {self.simple_stat()}")
        pass

    async def on_task_error(self, task):
        if task.retry_count < task.max_retry_count:
            await self.downloading_tasks.put(task)
            logger.info(f"rty {task}")
        else:
            logger.error(f"err {task}")
            pass
        pass

    async def main(self):
        while not self.stopped.is_set():
            # 完成下载任务
            try:
                finished_task = self.finished_tasks.get_nowait()
                await self.on_task_finished(finished_task)
            except asyncio.QueueEmpty:
                pass
            # 失败下载任务
            try:
                error_task = self.error_tasks.get_nowait()
                await self.on_task_error(error_task)
            except asyncio.QueueEmpty:
                pass
            await asyncio.sleep(1)

    def start(self, client: TelegramClient | None = None):
        if self.worker_thread_num == 0:
            assert client is not None
            assert len(self.workers) == 1
            self.workers[0].start(client)
        else:
            for worker in self.workers:
                worker.thread_start()
        if self.mng_use_thread:
            def _run():
                # 创建独立的事件循环并运行
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                assert self.worker_thread_num > 0
                self.start()
                loop.run_until_complete(self.main())

            self.thread = threading.Thread(target=_run)
        else:
            # 不阻塞启动协程
            asyncio.create_task(self.main())

    def mark_stopped(self):
        self.stopped.set()

    def is_stopped(self):
        return self.stopped.is_set()

    def simple_stat(self) -> str:
        return f"{self.total_parallel_downloading()}R;{self.downloading_tasks.qsize()}P;"

    def stat(self):
        """
        统计信息
        :return:
        """
        result = {
            "wait_download": self.downloading_tasks.qsize(),
        }
        total_downloading = 0
        for i, worker in enumerate(self.workers):
            curr_download = worker.get_curr_parallel()
            result[f"worker_{i}_downloading"] = curr_download
            total_downloading += curr_download
        result["total_downloading"] = total_downloading
        return result

    def is_all_done(self):
        """
        是否所有任务都完成
        :return:
        """
        if not self.downloading_tasks.empty():
            return False
        for worker in self.workers:
            if worker.get_curr_parallel() > 0:
                return False
        return True

    def total_parallel_downloading(self):
        total = 0
        for worker in self.workers:
            total += worker.get_curr_parallel()
        return total

    def wait_all_thread(self):
        if self.thread is not None:
            self.thread.join()
        if self.worker_thread_num != 0:
            for worker in self.workers:
                worker.thread.join()
