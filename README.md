# Mnemochat · 群聊长期记忆机器人

[![CI](https://github.com/chen849924987-cmyk/Mnemochat/actions/workflows/ci.yml/badge.svg)](https://github.com/chen849924987-cmyk/Mnemochat/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-58%20passed-brightgreen)]()

**English README → [README.en.md](README.en.md)**

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

# 离线体验全流程（不调任何 API，规则模拟）
python main.py --sim --mock

# 接真实 API
python main.py --init            # 生成 config.yaml 与 .env
# 编辑 .env 填入 DEEPSEEK_API_KEY、SILICONFLOW_API_KEY（或其他 OpenAI 兼容端点）
python main.py --check           # 启动体检：角色卡/数据库/分词/API 连通性
python main.py --sim
```

接入真实 QQ 群（OneBot v11）：

```bash
# 1. 部署任一 OneBot 11 实现（以 NapCat 为例）并登录你的机器人 QQ 号
# 2. 在其网络设置里开启「正向 WebSocket 服务器」，端口默认 3001
# 3. config.yaml 的 onebot.ws_url 指向它，然后：
python main.py --onebot
```

@机器人 必回；叫名字/小概率插话由 `behavior` 配置决定。所有群消息（包括机器人没回复的）都会进入记忆管线——先听，才记得。

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
- **视觉理解**（可选）：配置 OpenAI 兼容视觉端点（`sticker.vision.model`）后先用 VL 模型描述画面再打标，更准。DeepSeek 官方 chat API 是纯文本的——若你的端点提供视觉模型就填它，否则推荐硅基流动 Qwen-VL（和嵌入同一个 key）

**发送**：模型看到系统提示里的可用分类清单，想发表情就在回复中嵌入 `[[sticker:分类]]` 标记；适配器先发图、间隔半秒再发文字（与角色卡的 send_sticker 规范一致），带发图冷却防刷屏。模拟器用 `/stickers` 查看图库。

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

## 设计细节

- **衰减数学**：`R = exp(-Δt天/S)`，初始 `S = 2 + importance` 天；每次召回 `S ← min(S×1.25, 365)`。R 只影响排序权重，不影响存在性。
- **salience**：`0.35·语义 + 0.25·BM25 + 0.20·保留率 + 0.10·log(访问) + 0.10·(重要性+情感)/2`（权重可调）。
- **中文 BM25**：SQLite FTS5 的 unicode61 分词器不切中文，入库与查询两侧统一用 jieba 切词后以空格拼接。
- **查询串带发言人昵称**：谁在说话，关于谁的记忆就更容易浮上来（人也是这样）。
- **检索零 LLM 调用**：纯 SQL + numpy，千条规模毫秒级；LLM 只用在异步入库提取与定时巩固。
- **旁观学习**：机器人不回复的消息同样入库并参与记忆提取——群聊记忆的前提是先听。

## 项目结构

```
main.py            入口：--sim / --onebot / --telegram / --inspect / --export / --import / --reembed / --init
config.py          配置加载（yaml + ${ENV} 密钥插值）
adapter/           平台适配层：QQ OneBot v11（正向 WS）· Telegram（长轮询）
chat/              llm 客户端、mock 后端、群聊行为策略、回复主管线（多群安全）
memory/            schema/store/ingest/consolidate/recall/decay
embed/             api / local / mock 三通道嵌入
persona/           角色卡、人格守卫（知识边界/注入格式）、漂移检测、酒馆卡转换
stickers/          表情包图库（旁观收集/语境+视觉打标/按分类发图）
editor/            人格卡修改器（本地网页：滑条调参/卡导入/预览/热重载）
sim/               控制台模拟器、记忆管理台、表格渲染
personas/          角色卡（default 小满 / dafeiyu 大肥鱼 / deepseek_whale 小鲸鱼）
tests/             77 个离线测试（衰减/入库/检索/巩固/人格/漂移/多群/适配器/卡转换/表情包/导出/端到端验收）
```

## 测试

```bash
python -m pytest tests/ -q      # 77 passed，全程离线
```

验收用例 `tests/test_e2e_acceptance.py::test_remember_allergy_after_two_weeks`：说过过敏 → 快进 14 天 → 清空对话窗口 → 相关话题时以"约两周前"的口吻召回，且召回强化生效。

## 路线图

- [x] QQ 适配器（OneBot v11 正向 WebSocket：NapCat / LLOneBot / Lagrange）
- [x] Telegram 适配器（getUpdates 长轮询，无需公网）
- [x] 人格漂移检测（巩固时抽样评审 + 角色校准提醒闭环）
- [x] 记忆导出/导入（备份迁移，`--export` / `--import`）
- [x] 人格卡修改器（网页滑条调参 + SillyTavern 卡导入 + 热重载）
- [x] 表情包：旁观收集 → 语境/视觉打标 → `[[sticker:分类]]` 发图
- [ ] `visibility: self` 的机器人自我反思记忆
- [ ] Discord 适配器、多角色多群管理台、记忆可视化面板
- [ ] Telegram 发图表情、说说/联网搜索等工具调用

> ⚠️ 合规提示：非官方 QQ 协议实现（NapCat 等）存在违反腾讯服务条款导致账号风控的风险，请自行评估并使用小号。本仓库不包含任何协议实现，仅作为 OneBot 11 标准客户端。

## 致谢

设计借鉴了社区与学术界的优秀工作：[Mem0](https://github.com/mem0ai/mem0)、[Zep](https://github.com/getzep/zep)（时序知识图谱与事实有效期）、MemoryBank（艾宾浩斯遗忘曲线）、A-MEM、MENTOR/BRIDGE（人格锚定与双链记忆）。本项目为独立实现，与上述项目无关联。

## 许可证

[MIT](LICENSE)
