import asyncio
import os
import logging
import argparse
import sys
from src.tg_tools import TGTools,create_args

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s | %(name)s | [%(levelname)s] | %(filename)s:%(lineno)d | %(message)s')
logger = logging.getLogger(__name__)

async def main():
    logger.info(f"Start at path {os.getcwd()}")
    # 创建客户端
    tools = TGTools()
    parser = create_args(tools)

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
