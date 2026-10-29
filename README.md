# Mnemochat · 群聊长期记忆机器人

[![CI](https://github.com/chen849924987-cmyk/Mnemochat/actions/workflows/ci.yml/badge.svg)](https://github.com/chen849924987-cmyk/Mnemochat/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-97%20passed-brightgreen)]()

**English README → [README.en.md](README.en.md)** · [更新日志 → CHANGELOG.md](CHANGELOG.md)

> 让 API 驱动的群聊机器人像人一样记住群友——会遗忘、会想起、越想起越牢固，但**永不删库**。

一个自研的轻量记忆核心：分层漏斗（STM → Staging → LTM）+ 艾宾浩斯衰减 + 检索强化 + 混合检索（BM25/向量/实体图）+ 人格锚定 + 人格漂移检测。单文件 SQLite 存储，零外部服务依赖。已支持接入 **QQ 群**（OneBot v11）与 **Telegram 群**，内置控制台模拟器用于离线开发验证。

## 为什么不是"定期摘要删库"

把上下文管理当成"压缩问题"，就要在压缩瞬间判断"什么未来可能有用"——这个判断经常是错的，且"摘要的摘要"会逐轮失真。本项目把上下文管理重新定义为**存储与检索问题**：

- **一切只追加、不物理删除**：原始消息全量落库，"遗忘"只是检索权重衰减（记忆沉底，仍可被关键词/实体直接捞回）；
- **衰减影响排序，不影响存在**：保留率 `R = exp(-Δt/S)`，强度 S 随每次召回 ×1.25（封顶一年）——"越想起越牢固"；
- **事实变更不等于销毁历史**：新事实与旧事实矛盾时旧记忆标记 `superseded`，检索不再注入但可溯源（支持"我以前说过…"这类对话）。

## 架构

```
群消息（无论机器人是否回复）
   │
   ▼
 messages 全量日志（append-only，旁观学习）
   │ 每积累 N 条（异步，不阻塞回复）
   ▼
 ┌─────────── 写入 ingest ───────────┐
 │ LLM 提取候选记忆（重要性/情感/实体）│
 │ 噪声门控：低信息量直接丢弃          │
 │ 复现去重：相似度≥阈值 → 强化旧记忆  │
 └──────────────┬────────────────────┘
                ▼
            staging（筛选区）
                │ 巩固（定时"睡眠"）
                ▼
 ┌─────────── 巩固 consolidate ─────────────┐
 │ 晋升：够久 +（重要 或 被复现过）→ LTM     │
 │ 矛盾取代：同主体冲突事实 → superseded     │
 │ 睡眠巩固：情节聚类 → LLM 归纳为语义记忆    │
 │            （源记忆 archived，保留原文）   │
 └──────────────┬───────────────────────────┘
                ▼
   LTM（SQLite + 嵌入 + FTS5 + 实体索引）
                │ 每次回复前（纯本地，无 LLM 调用）
                ▼
 ┌─────────── 检索 recall ──────────────────┐
 │ BM25(jieba+FTS5) ∪ 向量余弦 ∪ 实体一跳    │
 │ 过滤：superseded / 新staging / 人格边界    │
 │ salience 重排：语义+BM25+保留率+访问+重要性│
 │ Top-K 注入 prompt（带相对时间）            │
 └──────────────┬───────────────────────────┘
                ▼
   检索即强化：access_count+1，S×1.25，衰减时钟重置
```

**人格锚定层**：角色卡（`personas/*.yaml`）的核心人格每次固定注入 system prompt，永不参与衰减巩固；知识边界（`boundary.cross_group` 等）决定"这个角色被允许知道哪些记忆"，拦截跨群隐私与失效设定。

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows；Linux/macOS 用 .venv/bin/pip

# 图形控制台（推荐）：聊天模拟器 + 记忆 + 人格 + 配置，一个本地网页全搞定
python main.py --gui --mock      # --mock 完全离线，不调任何 API
python main.py --gui             # 接真实 API（未配密钥时自动先离线运行）

# 终端版模拟器
python main.py --sim --mock

# 接真实 API
python main.py --init            # 生成 config.yaml 与 .env（也可在 GUI「设置」页直接保存生成）
# 编辑 .env 填入 DEEPSEEK_API_KEY、SILICONFLOW_API_KEY（或其他 OpenAI 兼容端点）
python main.py --check           # 启动体检：角色卡/数据库完整性/分词/数据目录/NapCat 端口/串门配置/聊天与嵌入 API/TG token
python main.py --sim
```

接入真实 QQ 群（OneBot v11）：

```bash
# 1. 部署任一 OneBot 11 实现（以 NapCat 为例）并登录你的机器人 QQ 号
# 2. 在其网络设置里开启「正向 WebSocket 服务器」，端口默认 3001
# 3. config.yaml 的 onebot.ws_url 指向它，然后：
python main.py --onebot
```

> Windows 用户可以直接双击 `start.bat` 菜单式启动。**从零到真实 QQ 群的完整步骤（含 NapCat 部署、测试剧本、常见问题）见 [docs/qq-test-guide.md](docs/qq-test-guide.md)。**

@机器人 必回；叫名字/小概率插话由 `behavior` 配置决定。所有群消息（包括机器人没回复的）都会进入记忆管线——先听，才记得。

## 图形控制台（--gui）

```bash
python main.py --gui [--mock] [--port 8765]
```

本地网页一体化入口，四个标签页（零额外依赖，仅监听 127.0.0.1，自动打开浏览器）：

- **聊天**：群聊模拟器——以任意群友身份发言，机器人按兴趣评分决定接不接话（未回话时显示兴致明细）；⏩ 时间快进、手动巩固、立即入库、清空对话窗口都在页面上点；
- **记忆**：多记忆宇宙总览与切换（主卡 + 各串门客卡）、记忆库浏览（保留率/强度/召回/复现一目了然）、召回预演、危险区清空（先自动备份）；
- **人格**：内置人格卡修改器——滑条调性格参数、改兴趣词、导入酒馆卡、保存即热重载；
- **设置**：聊天/提取/嵌入模型与密钥、群聊行为（兴趣门槛等）、串门关系、QQ/Telegram 接入——**保存即生成 config.yaml 与 .env，可一键"应用到运行中机器人"热重建管线，全程不用改代码、不用重启**。界面未展示的高级调参项（ingest/consolidation/recall/decay）保存时从原配置回填，手工调过的不会被覆盖。

接入 Telegram 群：

```bash
# 1. @BotFather 创建机器人，拿到 token 填入 .env 的 TELEGRAM_BOT_TOKEN
# 2. /setprivacy → Disable（关闭隐私模式，否则收不到未 @ 机器人的消息）
# 3. 把机器人拉进群，然后：
python main.py --telegram
```

## 备份与迁移

```bash
python main.py --export              # 导出 messages/memories/实体索引为 JSON（不含嵌入）
python main.py --import 备份.json    # 按 id 合并恢复
python main.py --reembed             # 恢复后重建向量（切换嵌入模型后同样用它）
```

## 人格卡修改器

```bash
python main.py --edit-persona                        # 编辑 config 指向的角色卡
python main.py --edit-persona personas/deepseek_whale.yaml
```

本地网页 UI（零依赖，仅监听 127.0.0.1）：核心人格/说话风格/口头禅编辑、**性格参数 0-10 滑条**（注入 prompt 作为分寸参考）、知识边界开关、最终 system prompt 实时预览；**保存即热重载**——运行中的机器人下一条消息就换人格，无需重启。

**导入 SillyTavern（酒馆）角色卡**：修改器支持直接上传 chub.ai 等站点下载的 PNG 卡（内嵌 JSON）或 V1/V2/V3 JSON，自动映射为本项目人格卡（[V2 规范](https://github.com/malfoyslastname/character-card-spec-v2) / [V3 规范](https://github.com/kwaroran/character-card-spec-v3)）。依赖工具调用的设定（表情包、发说说、联网搜索）暂不支持，导入后请人工检查。

内置示例卡：`personas/default.yaml`（小满）、`personas/dafeiyu.yaml`（大肥鱼·DeepSeek 娘）、`personas/deepseek_whale.yaml`（DeepSeek 小鲸鱼·QQ 群友版，去AI味铁律+梗生态+示例对话，OneBot 适配器已支持"一条一行"自动拆条发送）。

## 表情包（存 · 学 · 发）

机器人会**旁观收集**群友发的表情包（按内容 md5 去重，超过 `collect_max_kb` 的图视为照片不入库），每次出现都记录语境；巩固时批量**学习分类**——两种模式：

- **语境学习**（默认，零额外成本）：根据表情包每次出现时的前后消息打标签（发三次"哈哈"时出现的图自动获得"搞笑"标签）
- **视觉理解**（可选）：打标前先让视觉模型描述画面，描述与语境一起参与打标，更准。标签判定始终由提取模型完成，"看图"两种接法：
  - `sticker.vision.use_chat: true`——聊天模型本身是多模态（如 DeepSeek 4.1 Flash）→ **直接用聊天模型看图**，零额外配置；模型实际不支持图片时自动退回纯语境学习，功能不中断
  - `use_chat: false` + `vision.model` 填独立 VL 模型——主模型是纯文本时另接一个视觉 API（推荐硅基流动 Qwen-VL，和嵌入同一个 key；设置页可配密钥）

**发送**：模型看到系统提示里的可用分类清单，想发表情就在回复中嵌入 `[[sticker:分类]]` 标记；适配器先发图、间隔半秒再发文字（与角色卡的 send_sticker 规范一致），带发图冷却防刷屏。模拟器用 `/stickers` 查看图库。

**遗忘**（表情包版）：收集按内容 md5 去重不会重复占空间，此外有两层新陈代谢——发送选取的热度按"使用次数 / 时间距离"衰减，久未出现的冷梗自动沉底（软遗忘）；巩固时清理群里 `sticker.retain_days`（默认 90 天，0=关闭）没出现过的梗，删除文件与记录（硬遗忘——表情包没有记忆那种溯源价值，这里破例删库是合理的）。

## 好友私聊（守口如瓶模式）

群员加机器人好友私聊，直接可用（`private_chat.enabled`，默认开）：

- 每个私聊 = 一个 `private:<QQ号>` 的虚拟群：**记忆与所有群聊完全分开**，
  你俩聊过的只属于这个会话；
- 私聊默认**每条必回**（对方是直接跟你说话），`always_reply` 可关；
- **守口如瓶参数**（`secrecy_level`，默认 2）：私聊内容会不会在群里说漏——
  - **2 · 守口如瓶**：绝不提私聊内容。硬拦截优先级高于 `cross_group`，并在群聊发言前注入保密提醒；
  - **1 · 偶尔说漏嘴**：每次群聊回复约 1/3 概率把相关私聊记忆漏进上下文，且 prompt 要求"只说个大概、不透露是谁说的"——活人味的那种嘴不严；
  - **0 · 口无遮拦**：私聊记忆当普通记忆在群里用（无视 `cross_group`）；
  - 单向封印不变：群聊记忆能否在私聊里想起，仍由角色卡边界决定。旧字段 `tight_lipped` 仍兼容。

Telegram 私聊同样支持。模拟器/GUI 是单一虚拟群，暂无多会话界面。

## 社交与串门（多角色卡互访）

配合「每张角色卡独立记忆库」，让不同人格长出活人交际：

```yaml
social:
  enabled: true
  host_replies: true      # 客人发言后主卡接话
  visits:
    - persona_file: personas/dafeiyu.yaml
      database: data/memory_dafeiyu.db   # 客人自己的记忆宇宙（留空=与主卡共用）
      every_hours: 8
```

- 客卡用自己的记忆库回应主卡群里正在进行的话题，发一条自然的串门消息；
- 发言**两边都记住**：客库记自己的经历，主库把它当"来自客人的消息"走完整回复管线；
- 关系不是写死的——串门几次后双方各自记得对方，之后自然互相提起；
- GUI「设置 · 社交与串门」可视化维护关系列表，「聊天」页 👋 按钮随时手动触发；
- 两种出场方式：模拟器/GUI（虚拟群）客人以自己的名字出现；**QQ 顶号模式**——
  客人发言与主卡接话经**同一个账号**发出（`--onebot` 下自动生效），
  默认加【名字】前缀防身份混淆（`social.prefix_guest_name` 可关）；
- 想要两个号码真实同群互聊，用双开双卡方案（两个实例各自带卡与库拉进同一群）。

## 人格漂移检测

每次巩固后，抽样机器人近期发言与角色卡比对（评审模型打贴合度分）。判定漂移（客服腔、AI 助手腔、风格突变）时：
- 报告落库（`drift_reports` 表，人格健康趋势可查）
- 自动把"角色校准提醒"注入后续回复的 system prompt，直到下一轮检测恢复

这是"人格存活"的最后防线：记忆系统保住了它知道什么，漂移检测保住它是谁。角色卡的 `ai_flavor_blocklist`（AI 味黑名单，小鲸鱼卡内置 12 条）会自动进入评审标准——卡面上的"出现即失败"变成自动告警。`drift.enabled: false` 可关闭。

两分钟看懂它怎么"活"：

```
群聊> 小明: 我对花生过敏，大家点菜注意点
  📖 记住了（staging）：小明对花生过敏
群聊> /warp 14d                     ← 时间快进两周（触发巩固）
  😴 巩固：晋升 1 条 · ⬆ 晋升 LTM：小明对花生过敏
群聊> /window reset                 ← 清空对话窗口（机器人"失忆"眼前）
群聊> 小明: 小满，今晚聚餐吃什么好
小满: 我记得——约两周前，小明对花生过敏。这方面我会留意的。
```

## 打包成 exe（免 Python 环境）

```bash
pip install pyinstaller
python scripts/build_exe.py            # 构建并同步到 dist/Mnemochat
python scripts/build_exe.py --skip-build   # 只同步（已构建过时）
```

生成 `dist/Mnemochat/`——把**整个文件夹**拷到任何 Windows 电脑（或本机其他位置），
双击 `Mnemochat.exe` 即启动图形控制台并自动打开浏览器，无需安装 Python 和任何依赖。
默认双击 = `--gui`；也可在命令行传参（`Mnemochat.exe --onebot` 等）。

**更新版本时配置不丢**：构建脚本只覆盖程序文件（Mnemochat.exe、_internal），
`config.yaml`、`.env`、`personas/`、`data/` 原样保留（personas 仅新增不覆盖）。
注意更新前先关掉正在运行的 Mnemochat（Windows 不允许覆盖运行中的 exe）。
手动升级同理：用新版覆盖 `Mnemochat.exe` 与 `_internal/` 两个程序文件即可，
其余都是你的数据。

首次运行会在 exe 旁自动生成 `personas/` 模板与 `config.example.yaml`；
`config.yaml`、`.env`、`data/`（记忆库）都保存在 exe 目录——整个文件夹拷走即完成迁移。
注意 `dist/Mnemochat/_internal/` 是程序本体的一部分，删了就跑不起来。

## 模拟器命令

| 命令 | 作用 |
|---|---|
| `名字: 消息` | 以群成员身份发言（机器人按行为策略决定是否插话） |
| `/warp 7d\|12h\|30m` | 时间快进：验证衰减曲线与巩固触发 |
| `/consolidate` | 手动巩固（晋升/矛盾取代/睡眠归纳） |
| `/ingest` | 立即处理未入库的消息缓冲 |
| `/memories [staging\|ltm\|all]` | 查看记忆库（R/S/召回/复现一目了然） |
| `/recall <文本>` | 召回预演：看会想起什么、各因子得分 |
| `/window reset` | 清空对话窗口，此后只能靠长期记忆 |
| `/stats` | 统计 |

管理台：`python main.py --inspect`（list/show/recall/consolidate/stats/messages）。

## 配置要点（config.yaml）

- **chat / extractor 分离**：回复主模型与记忆提取模型可分别配置（提取用便宜模型）；任何 OpenAI 兼容端点均可（DeepSeek、硅基流动、智谱、OpenAI…）。
- **嵌入双通道**：`embedding.provider: api | local`。
  - `api`：OpenAI 兼容 /embeddings（推荐硅基流动 `BAAI/bge-m3`，中文效果好、有免费额度；DeepSeek 本身不提供 embedding 接口）。
  - `local`：`pip install -r requirements-local-embed.txt` 后切换，完全离线。
  - 不同模型向量空间不兼容——切换后运行 `python main.py --reembed` 补齐存量向量（旧向量保留，按模型分行存储）。
- 衰减（`decay.*`）、显著性权重（`recall.weights.*`）、触发策略（`behavior.*`）全部可调。
- **记忆按卡隔离**：`per_persona_memory: true` 后每张角色卡自动使用 `data/memory_<卡名>.db`——换卡即换记忆宇宙，互不相通；表情包库仍共享（自动 `data/stickers_shared.db`，也可用 `sticker.database` 显式指定），梗是大家共有的。旧库数据不自动迁移，可用 `--export` / `--import` 搬运。

## 注入端拟人化（分级压缩 + 人保底 + 核心记忆钉）

存储层永远原文、永不删库；"遗忘"体现在**注入端**：

- **分级压缩**：保留率 R 低的老记忆以降级形式出现——R≥0.45 注入原文，
  0.18~0.45 注入"（记得大概）+首句"，<0.18 注入"（印象模糊，只记得关键词：…）"——
  人对旧事的记忆本来就是模糊摘要，还省 token；
- **人保底**：关于具体的人的记忆在召回注入中至少保 3 席，事件细节挤不掉关系；
- **体检与试聊**：`--check` 和 GUI「🩺 一键体检」同套检查项（10+ 项，含 NapCat 端口连通、
  TG token 验证、数据库完整性）；聊天页「🧪 API 试聊」一键切真实模型试聊、随时切回；
  嵌入不可用时召回自动退化为关键词；
- **话题延续**：热聊窗口内的跟话/追问加保底分，"它的刺多吗"这类代词追问不再被无视；
- **角色语料库**：语料（台词/黑话/情绪样本）存独立 JSON，按轮抽样注入【语感参考】——
  角色卡忠实还原不压缩，语感随扩随用；GUI 人格页可视化编辑；
- **思维链**：DS 4.1 Flash 等模型默认返回思维链，聊天页可折叠查看；
  `llm.extra_body_json` 可透传思考开关等厂商私有参数；
- **来源标注**：每条注入记忆带来源——（群里聊到的）/（别的群里听来的）/
  （你们私下聊的）/（你自己记下的），模型能分辨亲历与转述；
- **核心记忆钉**：每轮必带"长期在意的人与事"（关系/偏好/关于具体的人），
  与当前话题无关也能自然想起——对应"人和约定不沉底，事反正能再查"。

均可在 `recall.*` 配置关闭。

## 设计细节

- **衰减数学**：`R = exp(-Δt天/S)`，初始 `S = 2 + importance` 天；每次召回 `S ← min(S×1.25, 365)`。R 只影响排序权重，不影响存在性。
- **salience**：`0.35·语义 + 0.25·BM25 + 0.20·保留率 + 0.10·log(访问) + 0.10·(重要性+情感)/2`（权重可调）。
- **中文 BM25**：SQLite FTS5 的 unicode61 分词器不切中文，入库与查询两侧统一用 jieba 切词后以空格拼接。
- **查询串带发言人昵称**：谁在说话，关于谁的记忆就更容易浮上来（人也是这样）。
- **检索零 LLM 调用**：纯 SQL + numpy，千条规模毫秒级；LLM 只用在异步入库提取与定时巩固。
- **旁观学习**：机器人不回复的消息同样入库并参与记忆提取——群聊记忆的前提是先听。
- **兴趣度回复**：@/叫名字必回；其余消息按兴趣分决定接不接话（话题基础 + 问句 + **角色兴趣词命中** + 记忆相关 + 情绪抖动 − 冷却惩罚），纯本地毫秒级计算。每张角色卡有自己的兴趣词（修改器可调）——小满对美食猫猫感兴趣，小鲸鱼对 AI 模型摸鱼感兴趣，同一句话两个角色接不接完全不同。模拟器会显示每条消息的兴致评分明细。

## 项目结构

```
main.py            入口：--gui / --sim / --onebot / --telegram / --inspect / --export / --import / --reembed / --init
config.py          配置加载（yaml + ${ENV} 密钥插值）
adapter/           平台适配层：QQ OneBot v11（正向 WS）· Telegram（长轮询）
chat/              llm 客户端、mock 后端、群聊行为策略、兴趣度评分、回复主管线（多群安全）
memory/            schema/store/ingest/consolidate/recall/decay
embed/             api / local / mock 三通道嵌入
persona/           角色卡、人格守卫（知识边界/注入格式）、漂移检测、酒馆卡转换
stickers/          表情包图库（旁观收集/语境+视觉打标/按分类发图）
editor/            人格卡修改器（本地网页：滑条调参/卡导入/预览/热重载）
gui/               图形控制台（聊天模拟器+记忆管理+人格卡+配置生成，本地网页）
sim/               控制台模拟器、记忆管理台、表格渲染
personas/          角色卡（default 小满 / dafeiyu 大肥鱼 / deepseek_whale 小鲸鱼）
tests/             97 个离线测试（衰减/入库/检索/巩固/人格/漂移/兴趣评分/多群/适配器/卡转换/表情包/导出/图形控制台/端到端验收）
```

## 测试

```bash
python -m pytest tests/ -q      # 97 passed，全程离线
```

验收用例 `tests/test_e2e_acceptance.py::test_remember_allergy_after_two_weeks`：说过过敏 → 快进 14 天 → 清空对话窗口 → 相关话题时以"约两周前"的口吻召回，且召回强化生效。

## 路线图

- [x] QQ 适配器（OneBot v11 正向 WebSocket：NapCat / LLOneBot / Lagrange）
- [x] Telegram 适配器（getUpdates 长轮询，无需公网）
- [x] 人格漂移检测（巩固时抽样评审 + 角色校准提醒闭环）
- [x] 记忆导出/导入（备份迁移，`--export` / `--import`）
- [x] 人格卡修改器（网页滑条调参 + SillyTavern 卡导入 + 热重载）
- [x] 图形控制台（--gui：聊天模拟器 + 记忆管理 + 人格卡 + 配置生成，一体化本地网页）
- [x] 表情包：旁观收集 → 语境/视觉打标 → `[[sticker:分类]]` 发图
- [ ] `visibility: self` 的机器人自我反思记忆
- [ ] Discord 适配器、多角色多群管理台、记忆可视化面板
- [ ] Telegram 发图表情、说说/联网搜索等工具调用

> ⚠️ 合规提示：非官方 QQ 协议实现（NapCat 等）存在违反腾讯服务条款导致账号风控的风险，请自行评估并使用小号。本仓库不包含任何协议实现，仅作为 OneBot 11 标准客户端。

## 致谢

设计借鉴了社区与学术界的优秀工作：[Mem0](https://github.com/mem0ai/mem0)、[Zep](https://github.com/getzep/zep)（时序知识图谱与事实有效期）、MemoryBank（艾宾浩斯遗忘曲线）、A-MEM、MENTOR/BRIDGE（人格锚定与双链记忆）。本项目为独立实现，与上述项目无关联。

## 许可证

[Apache License 2.0](LICENSE)
