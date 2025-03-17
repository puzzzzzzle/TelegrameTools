import asyncio
import os
import logging
import argparse
import sys
from src.tg_tools import TGTools

DEBUG = True
if DEBUG:
    log_format = '%(asctime)s | [%(levelname)s] | %(message)s | %(name)s | %(filename)s:%(lineno)d'
    asyncio.get_event_loop().set_debug(True)
    log_level = logging.INFO
else:
    log_format = '%(asctime)s | [%(levelname)s] | %(message)s'
    log_level = logging.INFO

logging.basicConfig(level=log_level,
                    format=log_format)
logging.getLogger("telethon").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)


async def main():
    logger.info(f"Start at path {os.getcwd()}")
    # 创建客户端
    tools = TGTools()
    parser = tools.create_args()

    # 解析参数
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    await tools.run_args(args)


# 运行脚本
if __name__ == "__main__":
    asyncio.run(main())
