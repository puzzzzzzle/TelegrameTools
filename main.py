import asyncio
import os
import logging
import argparse
import sys
from src.tg_tools import TGTools

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s | %(name)s | [%(levelname)s] | %(filename)s:%(lineno)d | %(message)s')
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
