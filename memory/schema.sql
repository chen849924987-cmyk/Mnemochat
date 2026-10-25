-- 记忆库 schema：一切只追加、不物理删除。
-- "遗忘"= status/权重变化（superseded/archived/衰减），原始行永远保留，可溯源可恢复。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 全量原始聊天日志（机器人未回复的消息同样入库——旁观学习）
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    user_name   TEXT NOT NULL,
    content     TEXT NOT NULL,
    ts          REAL NOT NULL,
    replied     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_group_ts ON messages(group_id, ts);

CREATE TABLE IF NOT EXISTS memories (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    stage            TEXT NOT NULL DEFAULT 'staging' CHECK(stage IN ('staging', 'ltm')),
    kind             TEXT NOT NULL CHECK(kind IN ('episodic', 'semantic', 'preference', 'fact', 'event', 'bond')),
    group_id         TEXT NOT NULL,                    -- 在哪个群习得（溯源）
    subject_user_id  TEXT,                             -- 关于谁（NULL=群/世界本身）
    actor_user_id    TEXT,                             -- 谁说的
    content          TEXT NOT NULL,                    -- 第三人称、自包含（"小明对花生过敏"）
    keywords         TEXT NOT NULL DEFAULT '[]',       -- JSON array
    entities         TEXT NOT NULL DEFAULT '[]',       -- JSON array（人名/事物名）
    importance       REAL NOT NULL DEFAULT 5,          -- 0-10
    emotion          REAL NOT NULL DEFAULT 0,          -- 0-10 情感强度
    visibility       TEXT NOT NULL DEFAULT 'group' CHECK(visibility IN ('group', 'global', 'self')),
    access_count     INTEGER NOT NULL DEFAULT 0,       -- 被召回注入次数（检索即强化）
    recurrence_count INTEGER NOT NULL DEFAULT 0,       -- 写入侧复现次数（同一信息再次出现）
    last_accessed_at REAL,
    created_at       REAL NOT NULL,
    strength         REAL NOT NULL,                    -- 艾宾浩斯时间常数 S（天）
    status           TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'superseded', 'archived')),
    superseded_by    INTEGER REFERENCES memories(id),  -- 被哪条新记忆取代（事实变更）
    archived_into    INTEGER REFERENCES memories(id),  -- 被哪条语义记忆归纳（睡眠巩固）
    source_msg_ids   TEXT NOT NULL DEFAULT '[]'        -- JSON array，溯源到原始消息
);
CREATE INDEX IF NOT EXISTS idx_memories_group_stage ON memories(group_id, stage, status);
CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(subject_user_id);

-- 嵌入按 (memory_id, model) 存储：不同嵌入模型向量空间互不兼容，
-- 切换 provider 后旧向量保留，由 --reembed 增量补齐。
CREATE TABLE IF NOT EXISTS embeddings (
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    vec       BLOB NOT NULL,
    PRIMARY KEY (memory_id, model)
);

-- 实体索引（轻量图）：实体名 → 记忆。共享同一实体的记忆互为一跳邻居。
CREATE TABLE IF NOT EXISTS memory_entities (
    name      TEXT NOT NULL,
    group_id  TEXT NOT NULL,
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    PRIMARY KEY (name, group_id, memory_id)
);
CREATE INDEX IF NOT EXISTS idx_entities_group_name ON memory_entities(group_id, name);

-- BM25 全文索引（内容与关键词分开两列，各自命中均计分）
CREATE VIRTUAL TABLE IF NOT EXISTS fts_memories USING fts5(
    content_seg,
    keywords_seg,
    mem_id UNINDEXED
);

CREATE TABLE IF NOT EXISTS consolidation_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    REAL NOT NULL,
    finished_at   REAL,
    promoted      INTEGER NOT NULL DEFAULT 0,
    reinforced    INTEGER NOT NULL DEFAULT 0,
    superseded    INTEGER NOT NULL DEFAULT 0,
    semantic_created INTEGER NOT NULL DEFAULT 0,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 人格漂移检测报告（巩固时抽样评审机器人近期发言与角色卡的一致性）
CREATE TABLE IF NOT EXISTS drift_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    adherence   REAL NOT NULL,           -- 0-10 与角色卡的贴合度
    drifted     INTEGER NOT NULL,        -- 是否判定漂移
    issues      TEXT NOT NULL DEFAULT '[]',
    suggestion  TEXT NOT NULL DEFAULT '',
    sample_size INTEGER NOT NULL DEFAULT 0
);

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
