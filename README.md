# 偶像大师生日提醒

AstrBot 插件：每天从萌娘百科“偶像大师系列/相关人士生日信息”页面读取生日表，并向白名单群聊发送角色、声优及相关人士生日祝贺。

## 安装

把本目录放到 AstrBot 的插件目录，例如：

```text
AstrBot/data/plugins/astrbot_plugin_imas_birthday
```

然后在 AstrBot WebUI 的插件管理中安装依赖并重载插件。

## 配置

- `white_umos`：群聊白名单。进入目标群发送 `/imasbd sid` 查看当前 UMO，或使用 `/imasbd bind` 自动加入。完整格式类似 `aiocqhttp:GroupMessage:123456`；如果只填纯数字群号，会按 OneBot 群聊自动兼容。
- `send_time`：每日发送时间，默认 `09:00`。
- `catch_up_send`：错过当天推送时间后补发，默认开启。
- `catch_up_on_first_start`：首次启动且已经过推送时间时是否补发，默认关闭，避免没有历史发送状态时误推生产群。
- `timezone`：日期时区，默认 `Asia/Tokyo`。`send_time`、`today` 和每日推送日期都会按这个时区计算。
- `include_characters`：是否包含角色，默认开启。
- `include_kr_characters`：是否包含 `THE IDOLM@STER.KR` 真人企划成员，默认关闭。
- `require_character_birthday`：当天没有角色生日时不推送，默认开启。
- `include_seiyuu`：是否包含声优，默认开启。
- `include_related_people`：是否包含其他相关人士，默认关闭。
- `include_events`：是否包含企划事件，默认关闭。
- `render_card`：是否同时生成生日卡片，默认开启。
- `card_render_mode`：生日卡片渲染方式，默认 `pillow`。可选 `pillow`、`auto`、`html`；建议保持 `pillow`，避免 AstrBot 的 t2i/html_render 端点偶发返回错误页或截出异常宽度。
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

## 透明立绘素材模式

生日卡默认使用旧角色图片/卡面图，适合直接放你手动挑好的卡面。透明 PNG 立绘模式仍保留为可选实验功能。配置项 `card_asset_mode` 可选：

```text
image     # 默认：只使用旧角色图片/卡面图
portrait  # 只使用透明立绘，缺失时显示占位
```

如果不同企划想走不同素材，可以设置 `card_asset_mode_by_brand`。每行或用分号都可以：

```text
millionlive=image
shinycolors=image
cinderellagirls=portrait
sidem=portrait
gakuen_idolmaster=portrait
```

这里的 `image` 会使用旧的 `character_assets_dir` 图片或你手动替换过的卡面图，不会给透明立绘额外铺应援色背景；旧配置里的 `auto` 也会按 `image` 处理。只有显式设置 `portrait` 才会使用透明立绘和角色应援色面板。支持的企划 key 包括 `the_idolmaster`、`cinderellagirls`、`millionlive`、`sidem`、`shinycolors`、`gakuen_idolmaster`、`va_liv`、`dearlystars`、`starlitseason`、`876_pro`、`961_pro`。

生日卡的角色名上方色条使用角色应援色；白色信息区右侧会以低透明度渲染 `assets/brand_marks/` 里的企划 icon，作为不抢正文的底纹。

透明立绘建议放在插件目录外，配置项 `character_portraits_dir` 留空时，AstrBot 部署中默认使用：

```text
AstrBot/data/imas_birthday_assets/portraits/
```

本地拉取透明立绘和角色色映射：

```powershell
python .\tools\fetch_portrait_assets.py
```

脚本会从可稳定映射的 DB/官网源拉取透明 PNG：`imas.gamedbs.jp/mlth`、`imas.gamedbs.jp/cg`、SideM 官网、学马官网，并复用本地已有的闪彩官方立绘缓存。已有文件默认跳过，生成 `character_portraits.py` 与 `character_colors.py`；没法高置信匹配的条目会写到 `asset_candidates/portrait_unmatched.csv`。

如果线上素材目录不在仓库里：

```powershell
python .\tools\fetch_portrait_assets.py --portraits-dir D:\imas_birthday_assets\portraits
```

如果已经有图片，或被萌娘百科限流了，直接扫描本地图片目录生成映射，不会访问网络：

```powershell
python .\tools\sync_character_assets.py
```

如果想预览生日数据并检查 KR 角色过滤结果：

```powershell
python .\tools\export_birthday_preview.py
```

它会生成 `previews/birthday_preview.md`、`previews/birthday_preview_filtered.csv` 和 `previews/birthday_preview_removed_kr.csv`，方便直接搜索浏览。

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

如果想给内部 LLM 接口补充生日以外的角色资料，可以生成 `character_profiles.py`：

```powershell
python .\tools\fetch_moegirl_character_profiles.py --limit 10 --dry-run
python .\tools\fetch_moegirl_character_profiles.py
```

脚本会复用萌娘百科生日表里的角色链接，逐个读取角色页的基础资料字段和页面开头简介，生成 `character_profiles.py`。已有档案默认跳过，可以分批慢慢跑；如果要重抓，加 `--overwrite`。目前会尽量映射 `summary`、`introduction`、`cv`、`age`、`height`、`weight`、`measurements`、`birthday_text`、`blood_type`、`dominant_hand`、`type`、`agency`、`hometown`、`hobby`、`specialty`、`school`、`unit`、`debut` 等常见字段，未标准化但有用的字段会放在 `raw` 里。

## LLM 工具

插件暴露给 LLM 两个工具：

- `imasbd_character_profile(query)`：查偶像大师角色基础档案，适合生日、企划归属、CV、年龄、身高、体重、血型、出身、爱好、特技、代表色、本地图片路径等硬事实。
- `imasbd_birthday_lookup(date_text="")`：查今天或指定日期的生日条目，`date_text` 支持 `MM-DD` 或 `MMDD`，留空表示今天。

建议把这些结构化工具当作硬档案来源；知识库只负责性格细节、剧情语料、口癖、关系网、剧情事件、现场梗和黑话缩写。模块级内部接口见下方“内部接口”。

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

如果想替换某个角色的图，先用 `/imasbd assets MM-DD` 找到该角色的实际文件路径，直接用你准备好的图片覆盖那个文件即可。建议使用竖图或接近卡面比例的图片；生日卡会自动居中裁切。覆盖后重新发送 `/imasbd date MM-DD` 就能看到新图，不需要重新抓取萌娘百科。

## 指令

```text
/imasbd sid
/imasbd status
/imasbd reset-state
/imasbd bind
/imasbd today
/imasbd date 06-22
/imasbd date 0606
/imasbd refresh
/imasbd assets
/imasbd assets 06-22
/imasbd find 天海春香
/imasbd sendtest
```

`/imasbd find 名字` 只查询生日表里的角色条目，不会匹配声优、相关人士或事件。它支持轻量模糊查询，例如 `/imasbd find 天海真香` 会按 `天海春香` 生成单人生日预览；消息和卡片只包含这个角色，不带同日其他角色或声优信息。

`bind`、`refresh`、`reset-state` 和 `sendtest` 需要管理员权限。`reset-state` 会清除每日自动推送状态，不会立即主动发送；如果当前已经过 `send_time` 且配置允许补发，定时器会在下一轮按正常逻辑补发。`sendtest` 还需要先在配置里打开 `enable_send_test`，它会实际测试分开发送、组合 `file_image`、组件本地文件、组件 base64 等图片发送方式，方便排查 OneBot/aiocqhttp/NapCat 的图片兼容性。需要详细定位时打开 `debug_send_test`，插件会在 AstrBot 日志和群聊里输出每一步进度；某一步卡住会按 `send_test_timeout` 超时并继续下一项。

## 内部接口

插件实例提供只读接口 `await plugin.imasbd_api(...)`，供其他插件或 LLM tool 包装调用。它只返回结构化数据，不会主动向群聊发送消息。

```python
result = await imas_plugin.imasbd_api("date", date_text="06-22", render_card=False)
result = await imas_plugin.imasbd_api("find", query="天海真香", render_card=True)
result = await imas_plugin.imasbd_api("profile", query="天海春香")
result = await imas_plugin.imasbd_api("today")
result = await imas_plugin.imasbd_api("assets", date_text="06-22")
result = await imas_plugin.imasbd_api("status")
```

如果隔壁插件不方便拿到插件实例，也可以导入模块级入口：

```python
from data.plugins.astrbot_plugin_imas_birthday.main import call_imasbd_api

result = await call_imasbd_api("profile", query="天海真香")
```

返回值是 `dict`，常用字段包括 `ok`、`action`、`date_key`、`entry`、`message`、`card_path`、`error`。`render_card=False` 可以只取文本和生日数据，避免 LLM 查询时生成图片；`find` 会额外返回 `best_match` 和最多 5 条 `matches`；`profile` 会返回单个角色的 `profile`，包括 `name`、`birthday`、`brand`、`brand_label`、`color`、`summary`、`introduction`、`cv`、`age`、`height`、`weight`、`birthday_text`、`blood_type`、`hometown`、`hobby`、`specialty`、`agency`、`image_path`、`portrait_path`、`source_url` 和 `raw`。

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
