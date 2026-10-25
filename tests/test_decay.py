"""衰减与显著性数学的行为验证。"""

from memory import decay


def test_retention_decreases_with_time():
    assert decay.retention(0.0, 10.0) == 1.0
    assert decay.retention(1.0, 10.0) > decay.retention(5.0, 10.0)
    assert decay.retention(10.0, 10.0) > decay.retention(50.0, 10.0)


def test_retention_increases_with_strength():
    # 强度越大忘得越慢
    assert decay.retention(10.0, 20.0) > decay.retention(10.0, 5.0)
    # 永不归零、永不越界（一年后仍残留极微小的保留率）
    r = decay.retention(365.0, 1.0)
    assert 0.0 < r < 0.001


def test_initial_strength():
    assert decay.initial_strength(8) == 10.0
    assert decay.initial_strength(0) == 2.0
    assert decay.initial_strength(-3) == 2.0  # 负值按 0 处理


def test_strengthened_grows_and_caps():
    assert decay.strengthened(10.0) == 12.5
    assert decay.strengthened(300.0) == 365.0     # 300*1.25=375 → 封顶
    assert decay.strengthened(400.0) == 365.0


def test_normalize_constant_list_is_half():
    assert decay.normalize([3.0, 3.0, 3.0]) == [0.5, 0.5, 0.5]
    assert decay.normalize([1.0, 3.0]) == [0.0, 1.0]


def test_salience_monotonic_in_each_factor():
    w = decay.SalienceWeights()
    base = decay.salience(0.5, 0.5, 0.5, 1, 5, 5, w)
    assert decay.salience(0.9, 0.5, 0.5, 1, 5, 5, w) > base
    assert decay.salience(0.5, 0.9, 0.5, 1, 5, 5, w) > base
    assert decay.salience(0.5, 0.5, 0.9, 1, 5, 5, w) > base
    assert decay.salience(0.5, 0.5, 0.5, 10, 5, 5, w) > base
    assert decay.salience(0.5, 0.5, 0.5, 1, 9, 9, w) > base


def test_salience_bounded():
    w = decay.SalienceWeights()
    assert decay.salience(1, 1, 1, 100, 10, 10, w) <= sum(
        (w.similarity, w.bm25, w.retention, w.access, w.importance_emotion)
    ) + 1e-9
