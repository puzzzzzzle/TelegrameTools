## Telegram 工具集

- 非常精简的版本, 仅依赖telethon和PyYaml
    - arm(linux/mac os)/x86(win/linux) 都可以跑
- api_id/api_hash 去 tg 平台找
```
Telegram Tools

positional arguments:
  {show_dialogs,clear_personal_chats,download}
    show_dialogs        展示所有对话
    clear_personal_chats
                        清除所有私聊
    download            下载媒体文件

options:
  -h, --help            show this help message and exit
```
### 下载对话的媒体文件

- chats_to_download 里填对话名/对话 ID 都行, 对话 ID/名称 可以用后面的工具拉取, 方便复制
- 配置好后`python main.py download` 启动下载

```yaml
api_id: YOUR_API_ID
api_hash: YOUR_API_HASH
download:
  # Path for saving downloaded files
  path: "downloads"
  # Prefix format for saved files
  file_path_prefix:
    chat_title: true
    # "" if not need, format str if need
    #    media_datetime: ""
    media_datetime: "%Y-%m"

  # Chats and media types to download
  chats_to_download:
    # Both chat ID and chat name are acceptable
    "group_name_1":
      media_types:
        - audio
        - document
        - photo
        - video
        - voice
    "group_name_2":
      media_types:
        - all
    "chat_name_1":
      media_types:
        - photo
    "chat_id":
      media_types:
        - video
```

### 获取所有对话列表和 ID

- `python main.py show_dialogs` 查看所有对话和对应的 ID

### 清空私聊

- `python main.py clear_personal_chats` 清理所有私聊

