# 偶像大师生日提醒

AstrBot 插件：每天从萌娘百科“偶像大师系列/相关人士生日信息”页面读取生日表，并向白名单群聊发送角色、声优及相关人士生日祝贺。

## 安装

把本目录放到 AstrBot 的插件目录，例如：

```text
AstrBot/data/plugins/astrbot_plugin_imas_birthday
```

然后在 AstrBot WebUI 的插件管理中安装依赖并重载插件。

## 配置

- `white_umos`：群聊白名单。进入目标群发送 `/imasbd sid` 查看当前 UMO，或使用 `/imasbd bind` 自动加入。
- `send_time`：每日发送时间，默认 `09:00`。
- `catch_up_send`：错过当天推送时间后补发，默认开启。
- `timezone`：日期时区，默认 `Asia/Shanghai`。
- `include_characters`：是否包含角色，默认开启。
- `include_kr_characters`：是否包含 `THE IDOLM@STER.KR` 真人企划成员，默认关闭。
- `require_character_birthday`：当天没有角色生日时不推送，默认开启。
- `include_seiyuu`：是否包含声优，默认开启。
- `include_related_people`：是否包含其他相关人士，默认关闭。
- `include_events`：是否包含企划事件，默认关闭。
- `render_card`：是否同时生成生日卡片，默认开启。
- `birthday_send_mode`：生日祝贺图片发送方式，默认 `combined_component_base64`，即文字和图片同一条消息，并绕过本地文件类型判断。
- `enable_send_test`：是否启用图片发送兼容性测试指令，默认关闭。
- `debug_send_test`：输出图片发送测试调试日志，默认关闭。
- `send_test_timeout`：图片发送测试单次超时秒数，默认 `10`。
- `card_title` / `card_subtitle`：生日卡标题文案。

`message_template` 支持这些变量：

- `{year}`：当前年份，例如 `2025`
- `{month}` / `{day}`：生日月日
- `{date}`：`MM-DD`
- `{slash_date}`：`YYYY/MM/DD`
- `{birthday_time}`：`YYYY/MM/DD 00:00`
- `{beijing_time}`：`北京时间 YYYY/MM/DD 00:00`
- `{fancy_year}` / `{fancy_slash_date}` / `{fancy_birthday_time}`：把数字转成 `𝟎𝟏𝟐` 风格
- `{fancy_beijing_time}`：`北京时间 𝟐𝟎𝟐𝟓/𝟎𝟒/𝟎𝟏 𝟎𝟎:𝟎𝟎`
- `{decorated_beijing_time}`：`°.✩┈ 北京時間 𝟐𝟎𝟐𝟓/𝟎𝟒/𝟎𝟏 𝟎𝟎:𝟎𝟎 ┈✩.°`
- `{items}`：生日条目列表

例如：

```text
{decorated_beijing_time}
祝{items}生日快乐！
```

## 本地角色图片

角色图片建议放在插件目录外，避免更新插件时被覆盖。配置项 `character_assets_dir` 留空时，AstrBot 部署中默认使用：

```text
AstrBot/data/imas_birthday_assets/characters/
```

也可以在配置里把 `character_assets_dir` 改成任意绝对路径，或设置环境变量 `IMAS_BIRTHDAY_ASSETS_DIR`。插件运行时只会从这个目录读取角色图片。

图片映射由仓库里的 `character_assets.py` 提供，例如：

```python
CHARACTER_IMAGE_ASSETS = {
    "天海春香": "the_idolmaster/amami_haruka.png",
    "如月千早": "the_idolmaster/kisaragi_chihaya.png",
}
```

推荐按官网品牌统一建子目录：

```text
the_idolmaster/
cinderellagirls/
millionlive/
sidem/
shinycolors/
gakuen_idolmaster/
va_liv/
dearlystars/
starlitseason/
876_pro/
961_pro/
```

插件会根据第一级目录给卡片打上官网品牌名，例如 `THE IDOLM@STER`、`シンデレラガールズ`、`ミリオンライブ！`、`SideM`、`シャイニーカラーズ`、`学園アイドルマスター`、`ヴイアライヴ`。图片建议提前裁成竖图或方图，卡片会用 `object-fit: cover` 自动铺满。

如果已经有图片，或被萌娘百科限流了，直接扫描本地图片目录生成映射，不会访问网络：

```powershell
python .\tools\sync_character_assets.py
```

如果想指定图片目录：

```powershell
python .\tools\sync_character_assets.py --assets-dir D:\imas_birthday_assets\characters
```

如果图片还在旧插件目录，可以先迁移到当前配置目录：

```powershell
python .\tools\migrate_character_assets.py --source-dir .\assets\characters
python .\tools\sync_character_assets.py
```

也可以从萌娘百科生日页抓取角色链接，并从角色页的主图补齐缺图：

```powershell
python .\tools\fetch_moegirl_character_assets.py
```

脚本会先检查本地是否已有同名图片；已有就直接复用，不会请求该角色的萌娘百科图片 API。缺图时才会下载到外部角色图片目录的 `<brand>/` 子目录，并生成 `character_assets.py`。如果确实要重下图片，加 `--overwrite`。如果只想测试前几个角色：

```powershell
python .\tools\fetch_moegirl_character_assets.py --limit 10 --dry-run
```

如果想指定图片目录：

```powershell
python .\tools\fetch_moegirl_character_assets.py --assets-dir D:\imas_birthday_assets\characters
```

萌娘百科下载到的缩略图通常会带萌百水印，卡片底部会保留来源感谢。

也可以用手动清单批量导入，用来替换你不满意的图片。先复制 `assets_manifest.example.csv` 为 `assets_manifest.csv`，按下面格式维护清单：

```csv
name,brand,source,filename
天海春香,the_idolmaster,C:\path\to\amami_haruka.png,amami_haruka.png
月村手毬,gakuen_idolmaster,https://gakuen.idolmaster-official.jp/assets/img/idol/temari/default.png,tsukimura_temari.png
```

然后运行：

```powershell
python .\tools\import_character_assets.py .\assets_manifest.csv
```

脚本会把图片复制或下载到外部角色图片目录的 `<brand>/` 子目录，并重新生成 `character_assets.py`。已有文件默认不会覆盖，除非加 `--overwrite`。插件启动时会自动读取这个生成文件。

如果 `source` 是本地路径，脚本会复制图片；如果是 `https://...` 图片链接，脚本会下载图片。之后重启/重载插件即可。

`/imasbd assets MM-DD` 可以查看指定日期每个角色的映射和实际读取路径。萌娘百科生日表里的 `ミント` 是 KR 企划成员 Mint。KR 真人企划成员默认过滤；如果打开 `include_kr_characters`，插件会按 `Mint` 显示和匹配，`Mint.jpg` / `ミント.jpg` 都能识别。

## 指令

```text
/imasbd sid
/imasbd status
/imasbd bind
/imasbd today
/imasbd date 06-22
/imasbd refresh
/imasbd assets
/imasbd assets 06-22
/imasbd sendtest
```

`bind`、`refresh` 和 `sendtest` 需要管理员权限。`sendtest` 还需要先在配置里打开 `enable_send_test`，它会实际测试分开发送、组合 `file_image`、组件本地文件、组件 base64 等图片发送方式，方便排查 OneBot/aiocqhttp/NapCat 的图片兼容性。需要详细定位时打开 `debug_send_test`，插件会在 AstrBot 日志和群聊里输出每一步进度；某一步卡住会按 `send_test_timeout` 超时并继续下一项。

`birthday_send_mode` 可选：

```text
combined_component_base64
combined_file_image
combined_component_file
split_file_image
```

推荐保持默认的 `combined_component_base64`，它会把祝贺文字和生日卡片放在同一条消息里，并避免 OneBot/NapCat 对本地文件类型判断失败。`split_file_image` 会回到文字、图片分开发送。

## 数据来源

默认来源是萌娘百科：

```text
https://zh.moegirl.org.cn/偶像大师系列/相关人士生日信息
```

页面中带彩色方块的条目会归为角色，普通文字归为声优，斜体归为相关人士，灰色文字归为事件。
