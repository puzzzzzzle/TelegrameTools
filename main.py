import asyncio
import os
import logging
import argparse
import sys

from telethon import TelegramClient
import src.utils as utils

from src.config import load_config, SESSION_NAME

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s | %(name)s | [%(levelname)s] | %(filename)s:%(lineno)d | %(message)s')
logger = logging.getLogger(__name__)


class TGTools:
    def __init__(self, config):
        self.config = config
        self.client = TelegramClient(SESSION_NAME, config["api_id"], config["api_hash"])

    async def start(self):
        await self.client.start()

    async def show_dialogs(self, args):
        """
        展示所有对话
        """
        # 创建客户端
        for dialog_id, name in (await utils.get_dialogs(self.client)).items():
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
        # 下载媒体文件
        # 在这里实现下载逻辑


async def main():
    logger.info(f"Start at path {os.getcwd()}")
    # 创建客户端
    tools = TGTools(load_config("./config.yaml"))

    # 配置argparse
    parser = argparse.ArgumentParser(description="Telegram Tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 添加show_dialog子命令
    parser_show = subparsers.add_parser('show', help=tools.show_dialogs.__doc__)
    parser_show.set_defaults(func=tools.show_dialogs)

    # 添加clear_personal_chats子命令
    parser_clear = subparsers.add_parser('clear_personal_chats', help=tools.clear_personal_chats.__doc__)
    parser_clear.add_argument(
        'reserved_chats',
        nargs='*',
        type=str,
        help="忽略的对话列表, 不提供就清理所有私聊"
    )
    parser_clear.set_defaults(func=tools.clear_personal_chats)

    # 添加download子命令
    parser_download = subparsers.add_parser('download', help=tools.download_media.__doc__)
    parser_download.add_argument('dialog_id', type=int, help='Dialog ID to download from')
    parser_download.set_defaults(func=tools.download_media)

    # 解析参数
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    async with tools.client:
        await tools.start()
        # 执行对应命令
        await args.func(args)


# 运行脚本
if __name__ == "__main__":
    asyncio.run(main())
