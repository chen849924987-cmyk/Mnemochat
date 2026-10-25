"""人格漂移检测：评审、落库、pipeline 的校准提醒闭环。"""

from chat.pipeline import BotPipeline
from persona.drift import DriftDetector


async def test_drift_detected_and_stored(store, chat, embedder, cfg, clock, persona):
    for i in range(5):
        await store.add_message(
            "g1", persona.name, persona.name,
            f"我是AI助手，很乐意帮助您解决问题{i}", clock.now()
        )
    det = DriftDetector(store, chat, persona, cfg, clock)
    report = await det.check()

    assert not report.skipped
    assert report.drifted
    assert report.adherence < cfg.drift.adherence_threshold
    latest = await store.latest_drift_report()
    assert latest is not None and latest["drifted"]


async def test_drift_healthy_person_passes(store, chat, embedder, cfg, clock, persona):
    for _ in range(5):
        await store.add_message(
            "g1", persona.name, persona.name,
            "诶，我记得呀，上次你不是说想吃火锅", clock.now()
        )
    det = DriftDetector(store, chat, persona, cfg, clock)
    report = await det.check()
    assert not report.skipped
    assert not report.drifted
    assert report.adherence >= cfg.drift.adherence_threshold


async def test_drift_skips_insufficient_samples(store, chat, embedder, cfg, clock, persona):
    await store.add_message("g1", persona.name, persona.name, "就一条", clock.now())
    det = DriftDetector(store, chat, persona, cfg, clock)
    report = await det.check()
    assert report.skipped and report.sample_size < cfg.drift.min_samples
    assert await store.latest_drift_report() is None


async def test_drift_blocklist_from_card(store, chat, embedder, cfg, clock, persona):
    """角色卡的 AI 味黑名单进入评审标准：出现即判漂移。"""
    persona.ai_flavor_blocklist = ["首先", "综上所述"]
    for i in range(4):
        await store.add_message(
            "g1", persona.name, persona.name,
            f"首先我们来看第{i}点，综上所述就是这样", clock.now()
        )
    det = DriftDetector(store, chat, persona, cfg, clock)
    report = await det.check()
    assert not report.skipped
    assert report.drifted
    assert any("黑名单" in issue for issue in report.issues)


async def test_pipeline_reanchor_loop(store, chat, embedder, cfg, clock, persona):
    """漂移 → 注入校准提醒 → 恢复后撤销。"""
    cfg.drift.sample_size = 4
    cfg.drift.min_samples = 4
    pipe = BotPipeline(store, chat, embedder, persona, cfg, clock)

    # 机器人最近全是客服腔发言
    for i in range(4):
        await store.add_message(
            "g1", persona.name, persona.name,
            f"作为AI助手，很乐意帮助您：{i}", clock.now()
        )
    await pipe.consolidate_now()
    assert pipe.drift_hint, "漂移后应注入角色校准提醒"

    await pipe.on_user_message("g1", "小明", "小明", "在吗", force_reply=True)
    system = chat.last_chat_messages[0]["content"]
    assert "回到群友口吻" in system or "角色校准" in system

    # 发言恢复人味后，最近的抽样窗口不再包含坏样本 → 撤销提醒
    for _ in range(6):
        await store.add_message(
            "g1", persona.name, persona.name,
            "唔…让我想想，诶，我记得呀", clock.now()
        )
    await pipe.consolidate_now()
    assert pipe.drift_hint is None
