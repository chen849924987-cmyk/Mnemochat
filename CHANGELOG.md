# 更新日志（Changelog）

本文件记录 Mnemochat 的版本历史。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)——当前处于 1.0 之前的快速迭代期：
**次版本号**（0.x）随功能新增递增，**修订号**（0.x.y）随修复与小改进递增。
自 v0.12.0 起每个版本会在 GitHub 上打 tag；此前未打 tag 的历史版本以下表对应。

## [Unreleased]

### 新增
- **来源标注注入**：回忆块与核心记忆钉的每条记忆带来源——
  （群里聊到的）/（别的群里听来的）/（你们私下聊的）/（私下听来的）/（你自己记下的），
  模型可分辨"亲耳听的"和"哪里听来的"；
- 验收套件对齐社区规格的验收表：清会话仍认对人、间接召回（凭属性问出关联记忆）、
  来源可区分；
- GUI 记忆表新增"来源"列（谁说的 + 所属会话）。

### 新增
- **启动体检扩容**（`--check` 与 GUI「🩺 一键体检」共用同一套检查）：
  数据库完整性 quick_check、数据目录可写、NapCat 正向 WS 端口连通探测
  （QQ 模式最常见翻车点，不可达为警告级）、串门关系逐条校验、
  Telegram bot token getMe 验证、记忆隔离/守口如瓶配置提示；
- **API 试聊**：聊天页一键在 离线模拟 ↔ 真实 API 间切换（只重建管线不落盘），
  未配置密钥时拒绝并提示；
- 嵌入不可用时召回自动退化为纯关键词（BM25/实体），机器人不再哑巴；
- 新增 `scripts/build_exe.py`：构建/更新可执行版且**不破坏用户数据**——
  程序文件（exe、_internal）与用户文件（config.yaml/.env/personas/data）分离同步，
  修复此前 PyInstaller 整目录重建导致已保存配置被清空的问题。

### 变更
- 聊天/提取模型的默认输出上限由 1024/2048 提升至 8192
  （输出上限非目标、按实际输出计费；群聊回复依旧很短），
  DeepSeek 4.1 Flash 最大 384K 输出可在 GUI 自行填写。

### 修复
- **召回候选池按群圈定**：向量候选此前只在当前群内检索，
  导致 `visibility: global` 与 `cross_group` 的跨群记忆完全无法被召回
  （隐私过滤本就由边界层统一执行，候选池改回全池，守口如瓶拦截不受影响）。

## [0.11.0] - 2026-10-27

### 新增
- **注入端拟人化（借鉴社区"五层记忆法"后的落地）**，存储层仍原文不动、永不删库：
  - 分级压缩注入：保留率低的老记忆以"记得大概（一句）／印象模糊（关键词）"的形式
    出现在回忆里，省 token 且更像人的模糊记忆（`recall.compress`）；
  - 人保底：关于具体的人的记忆在召回注入中至少保 3 席，
    事件细节挤不掉关系（`recall.person_priority` / `person_seats`）；
  - 核心记忆钉：每轮必带"长期在意的人与事"（关系/偏好/关于具体的人），
    与当前话题无关也能自然想起（`recall.core_memory` / `core_top_k`）。

## [0.10.1] - 2026-10-27

### 变更
- 守口如瓶由开关升级为三档保密等级 `private_chat.secrecy_level`：
  - **2 · 守口如瓶**（默认）：私聊记忆硬拦截，绝不流入群聊（优先级高于 cross_group）；
  - **1 · 偶尔说漏嘴**：每次群聊回复约 1/3 概率放行相关私聊记忆，且只说个大概；
  - **0 · 口无遮拦**：私聊记忆当普通记忆在群里用（无视 cross_group）。
- 旧字段 `tight_lipped` 仍兼容（false → 0 档）；非法值收敛到 [0, 2]。

## [0.10.0] - 2026-10-27

### 新增
- **好友私聊**：群员加机器人好友私聊可用，每个会话 = `private:<QQ号>` 虚拟群，
  记忆与所有群聊完全分开（巩固按会话分桶，归纳不跨界）；
  - `always_reply`：私聊每条必回（默认开，可关走兴趣评分）；
  - `tight_lipped`：守口如瓶（默认开，v0.10.1 升级为三档）；
- OneBot 适配器私聊解析与 `send_private_msg` 回复路由；Telegram 私聊同步支持。

## [0.9.1] - 2026-10-26

### 新增
- **QQ 顶号串门**：客卡与主卡的发言经同一个账号发出
  （适配器注入 `social_sender`，`prefix_guest_name` 前缀防身份混淆），
  自动调度与手动触发共用引擎；过滤自身消息回声防自问自答。

## [0.9.0] - 2026-10-26

### 新增
- **社交与串门**：客卡用自己的角色卡+记忆库到主卡所在群露面，
  发言双库写入（客库记自己的经历、主库当"来自客人的消息"走完整管线），
  关系由互动自然生长（`social.visits`）。
- 记忆页重构为**多记忆宇宙**：主卡 + 全部角色卡 + 串门客卡各自独立查看；
  危险区清空（先自动备份 JSON）。

## [0.8.0] - 2026-10-26

### 新增
- **记忆按角色卡隔离**（`per_persona_memory`）：每张卡自动 `data/memory_<卡名>.db`，
  换卡即换记忆宇宙；表情包库仍共享（`sticker.database`，默认 `data/stickers_shared.db`）。

### 修复
- GUI `_rebuild` 旧库连接泄漏。

## [0.7.2] - 2026-10-26

### 新增
- 表情包遗忘机制（`sticker.retain_days`，默认 90 天，0=关闭）：
  发送热度按"使用次数 ÷ 时间距离"衰减（软遗忘）+ 巩固时清理久未出现的梗（硬遗忘）。

## [0.7.1] - 2026-10-26

### 新增
- 视觉打标可改用聊天模型本身（`sticker.vision.use_chat`）——多模态主模型零额外配置；
  视觉密钥开放为独立表单项；模型不支持图片时自动退回语境学习。

## [0.7.0] - 2026-10-26

### 新增
- **图形控制台 `--gui`**：聊天模拟器 + 记忆管理 + 人格卡 + 配置生成，
  本地网页一体化（设置页保存即生成 config.yaml/.env，可一键热重建管线）；
  PyInstaller 打包支持（双击 exe 即用，首启自动播种）。

## [0.6.0] - 2026-10-25

### 新增
- **兴趣度回复**：@必回之外按兴趣分决定接话
  （话题 + 问句 + 角色兴趣词 + 记忆相关 + 情绪抖动 − 冷却），纯本地毫秒级；
  角色卡 `interests` 可在修改器调整；模拟器/GUI 显示兴致明细。

## [0.5.0] - 2026-10-25

### 新增
- **表情包子系统**：旁观收集群友表情包（md5 去重/大小门槛/语境记录）→
  语境学习 + 可选视觉打标 → 回复中 `[[sticker:分类]]` 先图后字发送；
- `docs/qq-test-guide.md` QQ 真机测试指南 + `start.bat` 菜单式启动器。

## [0.4.0] - 2026-10-25

### 新增
- 人格卡修改器（本地网页：滑条调参 / 酒馆卡 PNG/JSON 导入 / system prompt 预览 /
  保存即热重载）；
- DeepSeek 小鲸鱼、大肥鱼示例卡；OneBot 按行拆条发送；
- `--check` 启动体检；角色卡 AI 味黑名单接入漂移评审。

## [0.3.0] - 2026-10-25

### 新增
- 人格漂移检测（巩固时抽样评审 → 漂移告警 → 角色校准提醒闭环）；
- Telegram 适配器；记忆导出/导入；GitHub Actions CI；英文 README。

## [0.2.0] - 2026-10-25

### 新增
- QQ OneBot v11 适配器（正向 WebSocket / 断线重连 / @必回）；
  管线多群安全改造。

## [0.1.0] - 2026-10-25

### 新增
- 分层长期记忆核心：STM→Staging→LTM 漏斗、艾宾浩斯衰减（R=e^(-Δt/S)）、
  检索强化（×1.25）、睡眠巩固（晋升/矛盾取代/语义归纳）、人格锚定与知识边界、
  混合检索（BM25/向量/实体图）+ salience 重排；
- 控制台群聊模拟器；38 项离线测试。

[Unreleased]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.9.1...v0.10.0
[0.9.1]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.7.2...v0.8.0
[0.7.2]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/chen849924987-cmyk/Mnemochat/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chen849924987-cmyk/Mnemochat/commits/v0.1.0
