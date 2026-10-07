# 偶像大师生日提醒

AstrBot 插件：每天从萌娘百科“偶像大师系列/相关人士生日信息”页面读取生日表，并向白名单群聊发送角色、声优及相关人士生日祝贺。

## 安装

把本目录放到 AstrBot 的插件目录，例如：

```text
AstrBot/data/plugins/astrbot_plugin_imas_birthday
```

然后在 AstrBot WebUI 的插件管理中安装依赖并重载插件。

### Windows 更新时字体文件被占用

0.1.78 起，插件内附字体从内存加载，避免渲染名片后字体文件一直被占用、更新时报 `NotoEmoji.ttf` 或其他字体的 `WinError 5`。卸载或重载时也会清理字体缓存，花体、符号和 emoji 支持保持不变。

如果旧版已经报错，请先在启动器中完全停止 AstrBot，将新版 ZIP 的内容解压覆盖到 `AstrBot/data/plugins/astrbot_plugin_imas_birthday`，确认该目录下直接有 `main.py` 和 `metadata.yaml`，再启动 AstrBot。这次需要先停止进程以释放旧版字体；覆盖插件文件即可，已有担当、绑定名字和配置保存在插件目录外，无需卸载或清空数据。

## 配置

### 图片与角色资料 WebUI（0.1.80）

在 AstrBot 4.27.3 的 WebUI 中打开本插件详情，进入 Pages 的 **「角色图片与资料」**。新版安装后先重载插件并刷新详情页，页面与现有 Dashboard 共用登录验证，无需另开端口。

- 左侧搜索中文、日文或别名，选中角色后编辑；右上角可新增没有生日资料的配角。已有登记名字保持固定，不改变群友之前的担当关联。新增角色保存后即可 `加推`。
- 日文显示名、所属企划和补充别名可手动指定；日文名与企划留空时沿用基础资料。原有别名仍保留，补充别名用逗号分隔。
- 生日可以选择「沿用基础数据」「手动设置」或「没有生日／不参与生日提醒」。手动生日填写 `MM-DD`，例如 `04-16`；支持 `02-29`。修改会从原日期移除角色，再加到新日期，不影响声优生日。它不主动向群发送消息，也不重发当天已经投递的祝贺。
- 生日卡图片和担当头像独立设置。上传 PNG、JPEG、WebP 或 GIF（第一帧，最多 10 MB、2000 万像素），拖动裁切框或调整缩放、水平及垂直位置。源图自动缩小到最长边 2048 像素并保存副本，之后可继续调整；生日裁切框可切换单列、双列、三列，尺寸直接读取实际生日卡布局；整卡预览同步使用所选列数，以重复当前角色演示多人效果。切换布局不修改已保存裁切参数。
- 自定义担当头像、群友头像及空白占位使用官网头像的 170×153 圆角六边形轮廓，和官网原图居中对齐。WebUI 裁切预览与最终名片共用蒙版；上传副本本身不裁成六边形，之后仍可重新调整。
- 点击「保存修改」后立即生效；「预览已保存的名片」会调用实际 Pillow 渲染器生成生日卡和担当名片，仅在页面展示。没有生日时用 `01-01` 演示，不会登记这个日期。透明生日图片以白底显示。手动生日图片优先于该角色的立绘模式，担当图片优先于官网头像。
- 现有本地图可直接载入调整，保存时复制到管理目录，不覆盖原文件。图片选择「恢复原有来源」后保存，即恢复原本的官网头像或本地图。无需编辑图片映射文件。

手动资料是独立于自动来源的覆盖层：萌娘百科生日表、官网偶像目录继续同步，但不会覆盖你的修改。「资料来源或备注」只记录说明或链接，不会自动抓取链接内容。基础生日源地址仍在插件配置的 `source_url` 中设置；默认使用萌娘百科，其他地址需要提供同样格式的生日表。

标准部署下，新增资料与图片保存在 `AstrBot/data/imas_birthday_assets/editor/`：`characters.json` 是角色修改记录，`originals/` 是上传的源图副本，`renders/` 是生成的裁切图。这个目录位于插件目录外，覆盖更新插件时保留；备份时请复制整个 `editor/` 目录。原来的 `characters/`、`portraits/` 和 `tantou_icons/` 目录继续使用。旧版 AstrBot 若不支持 Plugin Pages，机器人指令与保存的覆盖资料仍可使用，编辑页面需升级 AstrBot。

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
- `card_title` / `card_subtitle`：生日卡标题文案。副标题默认只显示 `THE IDOLM@STER`；旧默认值 `THE IDOLM@STER Birthday` 也会自动简化，自定义文案仍按原样显示。

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

生日卡沿用可适配多角色的居中卡片样式；单人图为 480×540 图片区，双人图各为 330×400，三人图保持原尺寸。WebUI 的生日裁剪预览会使用相同尺寸。角色名上方色条使用角色应援色；白色信息区右侧会以低透明度渲染 `assets/brand_marks/` 里的企划 icon。只有声优生日时图片直接展示声优栏目，不保留空角色卡。

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

第一期担当功能支持以下群聊指令，无需官网账号：

```text
加推 月村手毬 花海佑芽
加推 小鹿なお
加推 一ノ瀬 志希 月村 手毬 花海佑芽
担当
担当 @群友
担当 123456789
减推 月村手毬
清空担当
担当改名 你的CN
担当改名 重置
担当排行
担当查询 百合子
担当查询 @群友
担当查询 123456789
```

以上指令也支持 `/` 前缀。一次最多添加 30 个名字，以空格分隔，可以混用中文全名和官网日文全名。插件会先识别 `一ノ瀬 志希`、`月村 手毬` 这样的带空格全名，再分割多个名字；全名中的空格和全角/半角字符会统一处理。`一之濑志希` 与 `一ノ瀬 志希` 对应同一个角色，重复加推不会重复登记，减推也支持两种名字。普通「加推」同时检索偶像大师角色目录与完整声优目录；加推群友须使用真实 @ 组件并通过群成员核验。角色与声优重名时列出各自身份等待确认，不自动选一位。

整批名字全部匹配后才会保存，按照用户输入的顺序追加，已有担当保留原位置。简称或错字会列出候选，找不到的名字也会保留原位置等待修正，期间这批中的完整名字同样不会提前登记。例如 `加推 花海佑芽 手毬 一ノ瀬 志希`，直接发送 `1`（也可用 `加推确认 1` 或 `加推确认 月村 手毬`）后，按 `花海佑芽 → 月村手毬 → 一之濑志希` 的顺序一次保存。多个待修正名字按提示顺序填写候选序号或完整名字，`0` 表示明确跳过；没有候选时可填写修正后的完整名字。确认仅对原发送者、原群有效，5 分钟后过期；新的加推或减推会取消上次待确认，重载后需要重新发起确认，也可以直接重新发送修正后的整批加推。

`担当 @群友` 或 `担当 QQ号` 查看指定群友在当前群的登记，一次查看一人；会忽略唤醒机器人的 @，不会读取其他群的担当。自查和查别人统一优先使用绑定的 P 名，其次使用当前群昵称。QQ 号查询通过 OneBot 的 `get_group_member_info` 获取群名片，未设名片时使用 QQ 昵称；自查使用当前消息的发送者名字。昵称查询失败时依次回退到 @ 组件里的名字、之前缓存的昵称；均没有时显示「制作人P」，不把 QQ 号或查看者的昵称当成对方名字。`担当改名 CN` 设置自己的本群名片 P 名，保留花体、符号和 emoji；末尾已有 P 时不重复添加。`担当改名 重置` 恢复使用群昵称，P 名按群和用户保存，重启后保留，不能修改别人。

有有效待确认记录时，可以直接回复 `1`、`1 1 1`、`1 0 1` 或 `0`，数量需与待修正条目相同，`0` 跳过对应条目；也可单独 @ 机器人后发送这些数字。未匹配到完整名字时提示「没找到『名字』。」并保留 `加推确认 完整名字` 的修正方式；候选提示会说明可直接回复序号。批量确认仍按原顺序一次保存，数量或序号无效时保留待确认记录，不提前登记。只在同一用户、同一群、五分钟内有效；没有记录、已过期、私聊或夹带其他群友 @／非文字内容时，纯数字消息不会被接管。

`character_supplemental_profiles.py` 单独维护生日表未收录或缺少日文资料的配角，随插件更新加载，不受生日资料重新采集影响。补充资料包含：美城常务、315 社长、十王邦夫、根绪亚纱里、美作武史，765、灰姑娘、闪耀色彩、SideM、百万动画的制作人，今西部长、三位训练员，以及 Dearly Stars 的石川实、冈本真奈美、尾崎玲子和武田苍一。支持中文名字、日文名字和明确称呼（例如 `315社长`、`美城専務`、`武内p`、`赤羽根p`、`闪P`），名片显示日文名字及对应企划。动画制作人的原始日文资料仍保留「プロデューサー」；担当图和排行默认显示武内P、中村P等中文通称，不把声优名字当作角色本名，也不会套用声优生日。资料保留官方角色页、发布报道或其他核对来源；生日未知时不生成生日条目。

此前补充极月学园的贺阳燐羽、蓝井抚子、白草四音、白草月花，学园的 Vo／Da／Vi 三位训练员，以及百万的チュパカブラ、灰姑娘的ぴにゃこら太、闪耀色彩的デビ太郎。每条资料保存核对来源；生日暂未明确，不生成生日提醒。缺少本地图时使用空白头像，可在「角色图片与资料」中上传和调整。训练员与灰姑娘的同名职业分开登记，企划显示学园。

例如发送 `加推 贺阳燐羽 蓝井抚子 白草四音 白草月花 Vo训练员 Da训练员 Vi训练员 卓帕 灰姑娘龙猫 呆笔太郎`。也支持日文全名；担当及排行均显示资料中的日文名字。

### 制作人叫法

制作人按具体角色登记，不用「本家P／百万P／346P」等泛称指向某一位。765 与 346 有多位制作人；不同角色各自统计，同一角色的别名只登记一次。担当图和排行默认显示各自中文通称，角色生日不会沿用声优生日。输入同时指向制作人角色与声优本人的名字时会列出两种身份，确认后才登记。

| 可以输入的名字 | 对应登记 |
| --- | --- |
| 闪p、闪P、夏目P、夏目响平／夏目響平 | 夏目P（沿用旧键「闪耀色彩P」） |
| 武内p、武内P、武内骏辅／武内駿輔 | 武内P |
| 米内P、U149P、米内佑希 | 米内P（U149，与武内P分开） |
| 石川P、石川界人 | 石川P（SideM） |
| 中村P、源太P、源P、中村源太 | 中村源太配音的百万动画制作人（沿用旧键「百万动画P」） |
| 百万ChiefP、百万Chief赤羽根P、Chief Producer | 赤羽根P（合并登记，仅保留一个条目） |
| 赤羽根P、赤羽根健治 | 2011 动画制作人，保留原登记与图片 |
| 间岛P／間島P、间岛淳司／間島淳司、ぷちますP | ぷちます的 P 头制作人 |
| 学p、学P、学园P、夏亚P／夏亞P | 初星学园制作人科的玩家主人公 |
| 律子P、秋月律子P | 现有秋月律子，不另建重复角色 |

例如 `加推 闪p 武内P 米内P 中村P 百万ChiefP 间岛P 学p 律子P`。担当图和排行显示赤羽根P、武内P、夏目P、石川P、中村P、米内P、间岛P、学P；手动显示名优先。输入 `武内骏辅` 会提示选择声优本人或武内P。`担当查询 灰P` 会提示「是否在找：武内P（武内駿輔）、米内P（米内佑希）」；`加推 灰P` 同样提供两位候选并等待确认，不会自动猜一位。学P与夏亚P共用同一登记和图片。学P的图片留空，在 WebUI「角色图片与资料」中搜索「学园P」即可上传、调整；其他缺图仍可按原流程上传。新制作人没有登记角色生日，律子沿用既有生日。

赤羽根P与旧「百万ChiefP」已合并为一个角色。同一群友同时登记过两者时，按原顺序保留第一处位置，统计只计一票。WebUI 自动归并旧 Chief 条目的图片配置：已有赤羽根P配置优先，缺少的图片类型从 Chief 条目沿用；原图片文件保留，合并前资料备份到 `editor/characters.before-chief-merge.json`。

声优关联参考：[闪耀动画官方演员表](https://shinycolors-anime.idolmaster-official.jp/staffcast/)、[灰姑娘官方角色页](https://imas-cinderella.com/character/)、[U149 米内P官方介绍](https://idolmaster-official.jp/news/01_15341)、[百万官方角色页](https://millionlive-anime.idolmaster-official.jp/character/)、[ぷちます官方配信演员表](https://www.b-ch.com/titles/4075/)。

### 声优担当与分类排行榜

`加推 小鹿なお 会 一太郎` 可一次登记女声优和男声优，也接受已核对的中文别名，如 `小鹿奈绪`、`进藤天音`。名字不确定时沿用 `加推确认` 或直接回复序号，`0` 跳过。`减推 小鹿なお` 移除；`担当` 名片会将声优作为独立类型展示，能确认的偶像大师配音关系标作「月村 手毬役」。`担当查询 小鹿なお` 可以反查群内担当。声优身份不会参与偶像排行或角色生日 @。旧指令 `加推女声优`、`减推女声优`、`女声优排行` 暂保留兼容，排行现已统计全部声优。

声优资料来自[声優グランプリ公开声优名鉴](https://sugotoku-seigura.secureserv.jp/directory/list.php)列表，当前快照收录 153 页共 1,836 位声优。人物详情页需要访问权限，所以这里只使用公开列表的姓名与头像；生日不由本目录推断。中文别名只用于检索。头像在第一次展示时下载至 `tantou_icons_dir/seiyuu/`，下载失败则留空白占位。维护者可运行 `python tools/sync_seiyuu_catalogue.py` 重新检查全部页面；脚本在页面或条目明显缺失时不覆盖当前快照。

`偶像排行` 只统计已登记的偶像大师角色（包括已有配角/制作人），排除群友和声优；下面的饼图按全量角色票数计算企划占比，不只统计前十名。`群友排行` 只统计真实 @ 核验后登记的群友；`声优排行` 只统计声优；`DD排行` 按本群每位 P 的去重推数排名。`担当排行` 继续显示全部类型的综合榜。这些命令都限定当前群，不跨群读取。

`tantou_llm_commentary` 默认开启：`担当` 出图后，使用当前会话在 AstrBot 配置的模型给出一句月村手毬视角的锐评。仅把角色/声优显示名传给模型，不发送 QQ 号或群友名字；未配置模型、超时或失败时直接跳过，不影响名片。关闭此项可避免模型调用。

生日图片默认不再附带重复的详细聊天文字，图片中的声优生日另立栏目「同日生日の声優」，可确认的偶像大师配音关系另写「角色名役」；图片缺失时保留原文字通知和 @。如需原来的长文字，在插件设置开启 `birthday_text_with_card`。

### 本群担当排行

发送 `担当查询 百合子` 或 `/担当查询 月村 手毬`，列出本群已保存登记中担当这位偶像的制作人及人数。支持中日文名字、已有别名和唯一简称：例如「百合子」只匹配七尾百合子，直接返回结果；「田中」匹配多人时提示候选，错字仍提示修正。这只改变查询，加推仍沿用确认流程。名字优先使用本群绑定 CN，其次使用当前群名片，再回退缓存昵称；括号中的账号用于区分同名群友。查询不会 @ 群友，也不跨群读取；待确认加推不计入。

发送 `担当查询 @群友` 或 `担当查询 123456789`，查询本群哪些制作人把这位群友登记为担当。以真实 At 组件或 QQ 号定位，一次查询一人；拒绝 @ 全体、多个目标、无效账号和冲突目标。查不到本群登记时直接提示，不尝试按群友昵称匹配偶像。目标名字同样优先 CN、当前群名片和缓存资料。

日高愛、水谷絵理及 Dearly Stars 配角使用独立的 `876_PRO` 归类、色条和官方标志，不计入 765；旧图片路径和手动图片继续使用。876 标志来自 [vα-liv 官方介绍](https://idolmaster-official.jp/va-liv/about)，具体资源见 `assets/brand_marks/SOURCES.md`。vα-liv 三人保留各自企划标志；秋月涼的现有记录为官网的 SideM 版本，保留 SideM 归类。

发送 `担当排行` 或 `/担当排行`，按本群已保存登记中的群友人数，展示前十位担当。每行左侧为小头像，右侧为显示名字、横向柱状图和人数；柱长与人数成正比，颜色沿用对应企划色，群友使用灰蓝色。人数相同时按显示名字排序，不足十位时只展示已有条目。

排行榜顶部显示圆形群头像和当前群名，下一行显示「担当排行榜」。QQ 群名通过当前机器人的 `get_group_info` 获取；群头像使用固定地址 `https://p.qlogo.cn/gh/群号/群号/640`（[NapCat 说明](https://github.com/NapNeko/NapCatQQ/discussions/1379)），按群缓存 24 小时，不接受消息或接口提供的任意图片 URL，不跟随跳转。资料读取失败时沿用缓存，缺少头像时用圆形占位；其他平台使用消息自带群名或「本群」。群名支持花体和 emoji，过长时省略末尾。

同一群友对同一担当最多计一票，重复名字和中日文别名会合并；待确认但尚未保存的加推不计入。偶像、配角及已核验的群友担当都参与统计，沿用官网日文名、手动图片和群友绑定名。加推、减推、清空后下一次查询立即更新，各群独立统计。生成或发送图片失败时返回文字排行。

### 群友也可以登记为担当

在 QQ 群里发送 `加推 @群友`，可以连续 @ 多人，按消息中的顺序追加到**发送者自己的**担当列表；`担当` 会把群友头像和名字与偶像一起排列。群友名字优先使用对方在本群绑定的 CN，其次使用登记时获取的群名片或 QQ 昵称，支持花体及 emoji。重新 `加推 @群友` 更新缓存名字，保持原来的位置；头像缓存 24 小时，失败时使用空白占位。

`减推 @群友` 移除自己的群友担当，即使对方已经退群也可以移除；`清空担当` 清空自己的全部登记。群友不会触发角色生日 @ 提醒。登记、昵称资料及头像缓存按完整群 UMO 隔离，重启后保留。

新增群友登记只接受 AstrBot 的真实 `At` 消息组件，不接受手写 `@昵称`、QQ 号或内部标识。每批最多 30 人，偶像名字和群友 @ 请分开发送；夹带其他文字、图片、全体 @、非法 ID 时拒绝整批。插件通过当前 QQ 机器人的 `get_group_member_list` 确认成员列表，再以 `get_group_member_info(no_cache=True)` 核对每人的群号和 QQ 号，只有全部通过才保存。群成员接口失败、返回身份不一致、对方不在群，或平台不支持这种核验时不登记；不会用 @ 上携带的昵称或历史缓存代替核验。

头像地址由已核验的 QQ 号生成，只请求固定的 `q1.qlogo.cn` 地址，不接受消息或接口资料里的头像 URL，不跟随跳转。下载限制为 2 MB、最大 2048 像素，验证图片后转换成最大 320 像素的 PNG；无图或下载失败时保留名字和空白头像。群友不显示偶像企划图标，也不增加企划颜色条。

`清空担当` 清除自己的本群担当和待确认加推，同时停止对应的生日 @，保留自定义 P 名；不影响其他群或群友。需要重排已有担当时，可清空后按想要的顺序重新加推。

`担当` 生成当前群、当前用户的全部担当名片 PNG。名片为 1800×1080 像素，按 90×54 mm 的横向比例设计，PNG 中写入 508 DPI。上方居中显示制作人 P 名；下方是「担当アイドル」、企划色条和官网 My Desk 六边形头像列表。色条只包含全部担当涉及的企划，每种颜色等宽，总宽度固定；分页时各张色条一致。每行最多 6 位、每张最多 18 位，分页尽量均衡并优先排满整行，最后一张放剩余担当：20 位分成 12＋8，37 位分成 18＋12＋7。依次发送全部图片，保留加推顺序。头像下只展示企划图标及官网日文名，长名字自动换行。名片不显示人数、页码、中文译名、英文装饰或额外说明。官方 PNG 已带头像裁切和透明边缘，插件直接读取；未收录角色使用本地资料中的日文名，没有日文名时用横线占位，不自行翻译。正常查询只发送图片，渲染或投递失败时返回同样不含人数的日文名字列表。

官网公共目录 `https://idolmaster-official.jp/cdn/jsons/idols/idol_list.json` 和前端实际使用的 `/assets/img/idol/hexagon/{brand}/{idol_code}.png` 是角色目录及头像来源，不需要用户登录。`character_tantou_icons.py` 保存与本地中文名字的精确映射、官网日文全名、假名、`idol_code`、数字 `id`、企划及资料链接，当前覆盖 341 位。插件把角色目录保存到 AstrBot 持久化 KV 的 `idol_catalogue_v1`，首次启动及此后每 24 小时在后台更新；按 `idol_code` 关联已有角色，保留改名前的日文名用于匹配，同步失败则保留原缓存并在一小时后重试。已有日文担当记录在读取时会归到本地角色名并去重。角色目录同步与官网账号的担当同步是独立的，本期无需账号绑定。

提供的 0.1.94 ZIP 安装包内附这些官方偶像头像；从源码安装时，总览查询会自动缓存缺少的头像。`tantou_icons_dir` 留空时缓存到 `AstrBot/data/imas_birthday_assets/tantou_icons`，并可直接读取安装包内的 `assets/tantou_icons`。官方目录尚未收录、暂时无法下载或无缓存时，回退到本地角色图；均缺失时保留名字，头像使用空白占位。

制作人昵称保留原始花体字母、符号和 emoji，不做字符兼容归一化；`assets/fonts` 随源码和安装包附带 Noto 字体及许可证，自动补充系统字体缺少的字形，无需运行时下载。emoji 使用与文字同色的单色样式，换行及截断保留完整字符组合；组合 emoji 的连字显示取决于 Pillow 的 Raqm 排版支持。更新到本版本后请安装新增的 `fonttools`、`regex` 依赖并重载插件。

本地资料的 `name_jp` 已清除假名注音、罗马字附注及「、」等分隔符，保留源资料 `raw` 供核对；后续采集和读取旧资料时同样清理。官网目录的日文名优先展示，本地日文名也可用于匹配。企划图标与名字按实际可见轮廓组成一行，整体水平居中、图标与文字垂直居中。

如需预先下载或更新全部官方头像：

```text
python tools/fetch_tantou_icons.py --icons-dir /path/to/AstrBot/data/imas_birthday_assets/tantou_icons
```

生日卡的素材设置独立生效。Linux/Docker 应安装 Noto CJK 或文泉驿字体，例如 Debian/Ubuntu 的 `fonts-noto-cjk`。渲染失败时仍返回文字列表。

担当记录按完整群 UMO 和发送者 ID 保存在 AstrBot 插件持久化 KV 中，各群分别登记，重启不会丢失。默认开启 `tantou_birthday_mentions`：当天的角色生日公告使用真正的 @ 消息组件提醒本群已登记对应担当的用户，同一人每条公告最多 @ 一次。它沿用 `white_umos`、`enabled`、`send_time`、`timezone` 和既有发送去重/失败重试机制；加推不会自动把群加入生日推送白名单。`减推` 后停止对应角色提醒。手动 `today`、`date`、`find` 查询不会触发担当 @。

本期只提供担当总览；官网账号关联、官网担当同步和今日小偶像暂不包含。

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

返回值是 `dict`，常用字段包括 `ok`、`action`、`date_key`、`entry`、`message`、`card_path`、`error`。`render_card=False` 可以只取文本和生日数据，避免 LLM 查询时生成图片；`find` 会额外返回 `best_match` 和最多 5 条 `matches`；`profile` 会返回单个角色的 `profile`，包括 `name`、`birthday`、`brand`、`brand_label`、`color`、`summary`、`introduction`、`cv`、`age`、`height`、`weight`、`birthday_text`、`blood_type`、`hometown`、`hobby`、`specialty`、`agency`、`image_path`、`portrait_path`、`source_url` 和 `raw`。官网目录中收录的角色还提供 `official_name_jp`、`official_kana`、`official_code`、`official_id` 和 `official_profile_url`；`find` 与 `profile` 都支持官网日文名。

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

## 本地验证

```text
python -m unittest discover -s tests -v
```

担当测试使用 AstrBot 消息适配器替身，验证中日文姓名及批量匹配、候选确认、官网目录同步与旧记录迁移、重启持久化、并发登记、群隔离、生日 @ 去重、名片渲染及分页发送。真实 AstrBot 指令派发与 QQ 投递需在部署环境验证。
