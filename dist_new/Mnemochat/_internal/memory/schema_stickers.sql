-- 表情包库 schema：可独立成共享库（多个角色卡的机器人共用一个梗宇宙），
-- 也可随主记忆库（schema.sql 之外单独执行本文件）。

-- 表情包图库（旁观收集：群友发的表情包按内容哈希去重入库）
CREATE TABLE IF NOT EXISTS stickers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    md5          TEXT UNIQUE NOT NULL,
    file_path    TEXT NOT NULL,
    source_url   TEXT NOT NULL DEFAULT '',
    group_id     TEXT NOT NULL,
    first_sender TEXT NOT NULL DEFAULT '',
    first_seen   REAL NOT NULL,
    last_seen    REAL NOT NULL,
    use_count    INTEGER NOT NULL DEFAULT 1,
    tags         TEXT NOT NULL DEFAULT '[]',   -- JSON：语境/视觉学习的分类标签
    description  TEXT NOT NULL DEFAULT ''      -- 视觉模型的一句话描述（可空）
);

-- 表情包出现语境（标签学习的数据源：每次出现时的前后消息）
CREATE TABLE IF NOT EXISTS sticker_contexts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sticker_id   INTEGER NOT NULL REFERENCES stickers(id),
    ts           REAL NOT NULL,
    group_id     TEXT NOT NULL,
    sender       TEXT NOT NULL DEFAULT '',
    context_text TEXT NOT NULL DEFAULT ''
);
