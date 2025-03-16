import asyncio
import logging
import math
import threading
import weakref
from pathlib import Path

from telethon import TelegramClient

from . import config as cfg

logger = logging.getLogger(__name__)


class DownloadTaskBase:
    """
    下载任务
    """

    def __init__(self, max_retry_count: int):
        self.retry_count = 0
        self.max_retry_count = max_retry_count

        pass

    async def download(self, client: TelegramClient):
        pass


class DownloadWorker:
    """
    下载工作线程
    """

    def __init__(self, mng: "DownloadWorkerMng", max_parallel: int, download_tasks: asyncio.Queue,
                 finished_tasks: asyncio.Queue,
                 error_tasks: asyncio.Queue):
        self.mng = weakref.ref(mng)
        self.max_parallel = max_parallel
        self.download_tasks = download_tasks
        self.finished_tasks = finished_tasks
        self.error_tasks = error_tasks

        self.client: TelegramClient = None

        self.curr_parallel = 0
        self.curr_parallel_lock = threading.Lock()  # 创建一个锁

    def increment_curr_parallel(self):
        with self.curr_parallel_lock:  # 加锁
            self.curr_parallel += 1

    def decrement_curr_parallel(self):
        with self.curr_parallel_lock:  # 加锁
            self.curr_parallel -= 1

    def get_curr_parallel(self):
        with self.curr_parallel_lock:  # 加锁
            return self.curr_parallel

    async def try_download_one(self):
        client = self.client
        try:
            task: DownloadTaskBase = self.download_tasks.get_nowait()
        except asyncio.QueueEmpty as e:
            return

        self.increment_curr_parallel()
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

    async def run_until_stop(self, client: TelegramClient):
        assert self.client is None
        self.client = client

        while not self.mng().is_stopped():
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
        asyncio.create_task(self.run_until_stop(client))

    def thread_main(self):
        """
        独立线程中, 创建一个client 独立运行
        :return:
        """

        # 用 asyncio 的独立循环, 最大并行 max_parallel 进行下载
        async def _run():
            # 创建 TelegramClient
            client = TelegramClient(
                session=cfg.SESSION_NAME,
                api_id=cfg.API_ID,
                api_hash=cfg.API_HASH
            )

            # 启动客户端
            async with client:
                await client.start()
                await self.run_until_stop(client)

        # 创建独立的事件循环并运行
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())

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
            DownloadWorker(self, self.worker_max_parallel, self.downloading_tasks, self.finished_tasks,
                           self.error_tasks)
            for _ in range(max(1, worker_thread_num))]
        pass

    async def on_task_create(self, task):
        logger.info(f"+++ {self.simple_stat()}; {task}")
        pass

    async def on_task_finished(self, task):
        logger.info(f"--- {self.simple_stat()}; {task}")
        pass

    async def on_task_error(self, task):
        logger.info(f"err {task}")
        if task.retry_count < task.max_retry_count:
            await self.downloading_tasks.put(task)
        else:
            logger.error(f"task {task} over max retry count")
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

    async def push_download_task(self, task: DownloadTaskBase):
        """
        添加下载任务, 异步线程会 从 chat_tasks 中获取任务, 逐个执行
        """
        await self.downloading_tasks.put(task)
        await self.on_task_create(task)

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
