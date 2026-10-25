# QQ 群真机测试指南

从零到在真实 QQ 群里跑起来，按顺序做完即可。

## 第 0 步：体检（1 分钟）

```
start.bat → 1（启动体检）
```

必须全绿再继续。缺什么它都会告诉你（API key 没填、角色卡加载失败等）。

## 第 1 步：API 密钥（你来做）

1. **DeepSeek**（聊天+提取）：https://platform.deepseek.com 注册 → 充 5-10 元 → 创建 API key
2. **硅基流动**（嵌入 bge-m3，免费额度足够测试）：https://siliconflow.cn 注册 → API key
3. 还没生成配置的话先跑一次 `main.py --init`，然后编辑 `.env`：

```
DEEPSEEK_API_KEY=sk-xxx
SILICONFLOW_API_KEY=sk-xxx
```

再跑一次体检，确认"聊天模型 API"和"嵌入"两项全绿。

## 第 2 步：NapCat + 小号（你来做，约 10 分钟）

> ⚠️ 非官方协议有账号风控风险，务必用小号；新号先养几天再进群发言。

1. 下载 NapCat（GitHub Release 或官网），安装并启动
2. 用**小号**扫码登录
3. NapCat WebUI → 网络配置 → 新建 **正向 WebSocket 服务器**，端口 `3001`（默认即可）
   - 可选：设置 access_token（设了的话 config.yaml 里 `onebot.access_token` 填同一个）
4. 用大号建一个**测试群**（只有你和小号），把小号拉进去

## 第 3 步：启动（一行）

```
start.bat → 4（QQ 群模式）
```

看到 `OneBot WS 已连接` 就通了。测试群里的@小号 必回；叫名字/小概率插话。

## 第 4 步：测试剧本（照着做，验证每个子系统）

| 测什么 | 怎么做 | 预期 |
|---|---|---|
| 连通性 | @机器人 说句话 | 必回，且**只发一行**（不换行不刷屏） |
| 旁观学习 | 朋友甲说"我对花生过敏"（不@机器人），再聊几句别的 | 机器人不回但消息入库：另开终端 `--inspect` → `list staging` 能看到 |
| 记忆晋升 | 在群里正常聊半小时（默认 30 分钟自动巩固一次） | `--inspect` → staging 变 ltm；或手动 `consolidate` |
| 长期记忆 | 隔一两天再@机器人："还记得吗，甲对什么过敏" | 以模糊时间的口吻想起 |
| 表情包收集 | 群里斗图几张 | `--inspect` 或模拟器 `/stickers` 看到入库（含次数） |
| 表情包发送 | 等图库打标后@机器人："发个搞笑的表情" | 先图后字两条消息 |
| 人格稳定 | 聊几轮后 `--inspect` 看最新 drift 报告 | adherence 应在 7 分以上 |

**不想等时间流逝的加速技巧**（测试用）：`config.yaml` 里把
`consolidation.min_age_hours: 0.05`（约 3 分钟就晋升）、
`consolidation.interval_minutes: 2`（每 2 分钟巩固一次）。
看完真实效果记得改回去。

## 常见问题

| 症状 | 原因/解法 |
|---|---|
| `OneBot WS 断开…5 秒后重连` 循环 | NapCat 端口/地址不对；防火墙；token 不一致 |
| 连上了但不回 | 没触发：要 @ 或叫角色名（`behavior.names` 可加昵称）；概率插话默认 15% |
| 回复带换行/列表 | 换 deepseek-whale 卡（有去AI味铁律）；或把 `llm.temperature` 降到 0.6 |
| 记忆没提取出来 | 看 `data/bot.log`；多是 JSON 解析失败重试中；`--inspect` 查 staging |
| 表情包收不到 | NapCat 的 image url 有时效，延迟大会下载失败属正常；确认 `sticker.enabled: true` |
| 一切正常但想清库重来 | 删 `data/memory.db`（**不可逆**，先 `--export` 备份） |

## 快速替代路径：Telegram（10 分钟见效）

不想部署 NapCat 的话，Telegram 阻力最小：@BotFather 建 bot 拿 token → 填 `.env` 的 `TELEGRAM_BOT_TOKEN` → BotFather `/setprivacy` 选 Disable → 拉进群 → `start.bat → 5`。记忆/人格/漂移全功能可用（表情包发图暂不支持，仅收图打标在 QQ 生效）。
