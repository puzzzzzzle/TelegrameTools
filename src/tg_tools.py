import argparse
import logging
from telethon import TelegramClient

from . import utils
from . import config as cfg
from . import downloader
logger = logging.getLogger(__name__)


class TGTools:
    def __init__(self):
        self.config = cfg.load_config(cfg.CONFIG_PATH)
        config = self.config
        self.client = TelegramClient(cfg.SESSION_PATH, config["api_id"], config["api_hash"])

    async def start(self):
        await self.client.start()

    async def show_dialogs(self, args):
        """
        展示所有对话
        """
        # 创建客户端
        dialogs = await utils.get_dialogs(self.client)
        for dialog_id, name in dialogs.items():
            logger.info(f"{dialog_id} : {name}")

    async def clear_personal_chats(self, args):
        """
        清除所有私聊
        """
        reserved_chats: list[str] = args.reserved_chats  # 获取可选的名称列表
        reserved_set = set(reserved_chats) if reserved_chats else set()
        await utils.clear_all_personal_chats(self.client, reserved_set)

    async def download_media(self, args):
        """
        下载媒体文件
        """
        await downloader.download_by_config(self.client,self.config)

    def create_args(self):
        # 配置argparse
        parser = argparse.ArgumentParser(description="Telegram Tools")
        subparsers = parser.add_subparsers(dest="command", required=True)

        # 添加show_dialog子命令
        parser_show = subparsers.add_parser('show_dialogs', help=self.show_dialogs.__doc__)
        parser_show.set_defaults(func=self.show_dialogs)

        # 添加clear_personal_chats子命令
        parser_clear = subparsers.add_parser('clear_personal_chats', help=self.clear_personal_chats.__doc__)
        parser_clear.add_argument(
            'reserved_chats',
            nargs='*',
            type=str,
            help="忽略的对话列表, 不提供就清理所有私聊"
        )
        parser_clear.set_defaults(func=self.clear_personal_chats)

        # 添加download子命令
        parser_download = subparsers.add_parser('download', help=self.download_media.__doc__)
        parser_download.add_argument(
            'dialog_id',
            nargs='*',
            type=int,
            help='Dialog ID to download from, if empty, download all from config'
        )
        parser_download.set_defaults(func=self.download_media)
        return parser

    async def run_args(self, args):
        async with self.client:
            await self.start()
            # 执行对应命令
            await args.func(args)
