"""
Unit tests for the modules that don't require a webcam/GPU/trained model --
risk assessment formula, tier boundaries, the 3-window confirmation rule,
persistence tracking, and crowd density normalisation.

Run with:
    python -m pytest tests/ -v
"""
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

from modules.module5_risk_assessment import RiskAssessmentModule
from modules.module4a_rule_engine import RuleEngineModule
from modules.module3_detection_tracking import Detection
from modules.utils import PersistenceTracker, FrameSequenceBuffer, compute_crowd_density


TIERS = {
    "normal": [0.0, 0.4],
    "elevated": [0.4, 0.65],
    "high": [0.65, 0.85],
    "critical": [0.85, 1.01],
}


@pytest.fixture
def risk_module():
    return RiskAssessmentModule(alpha=0.5, beta=0.3, gamma=0.2, tiers=TIERS, confirmation_windows=3)


def test_risk_score_matches_formula(risk_module):
    result = risk_module.compute(track_id=1, p_threat=0.8, d_persistence=0.5, c_density=0.3)
    expected = 0.5 * 0.8 + 0.3 * 0.5 + 0.2 * 0.3
    assert result.score == pytest.approx(expected, abs=1e-6)


def test_risk_score_is_clamped_to_unit_interval(risk_module):
    result = risk_module.compute(track_id=1, p_threat=10.0, d_persistence=10.0, c_density=10.0)
    assert 0.0 <= result.score <= 1.0


@pytest.mark.parametrize("p_threat,d_persistence,c_density,expected_tier", [
    (0.0, 0.0, 0.0, "normal"),
    (0.5, 0.5, 0.5, "elevated"),
    (0.95, 0.9, 0.9, "critical"),
])
def test_tier_assignment(risk_module, p_threat, d_persistence, c_density, expected_tier):
    result = risk_module.compute(track_id=1, p_threat=p_threat, d_persistence=d_persistence, c_density=c_density)
    assert result.tier == expected_tier


def test_critical_threshold_boundary(risk_module):
    # exactly at the paper's stated critical threshold R >= 0.85
    result = risk_module.compute(track_id=1, p_threat=1.0, d_persistence=1.0, c_density=0.75)
    assert result.score == pytest.approx(1.0 * 0.5 + 1.0 * 0.3 + 0.75 * 0.2)
    assert result.tier == "critical"


def test_three_window_confirmation_rule_requires_three_consecutive_critical(risk_module):
    # window 1: critical -> not yet confirmed (only 1/3)
    r1 = risk_module.compute(track_id=5, p_threat=1.0, d_persistence=1.0, c_density=1.0)
    assert r1.tier == "critical" and r1.confirmed is False
    # window 2: critical -> still not confirmed (2/3)
    r2 = risk_module.compute(track_id=5, p_threat=1.0, d_persistence=1.0, c_density=1.0)
    assert r2.confirmed is False
    # window 3: critical -> now confirmed (3/3 consecutive)
    r3 = risk_module.compute(track_id=5, p_threat=1.0, d_persistence=1.0, c_density=1.0)
    assert r3.confirmed is True


def test_three_window_confirmation_rule_resets_on_non_critical_window(risk_module):
    risk_module.compute(track_id=7, p_threat=1.0, d_persistence=1.0, c_density=1.0)   # critical
    risk_module.compute(track_id=7, p_threat=1.0, d_persistence=1.0, c_density=1.0)   # critical
    # a normal window breaks the streak
    r3 = risk_module.compute(track_id=7, p_threat=0.0, d_persistence=0.0, c_density=0.0)
    assert r3.tier == "normal" and r3.confirmed is False
    # need 3 fresh consecutive critical windows again
    risk_module.compute(track_id=7, p_threat=1.0, d_persistence=1.0, c_density=1.0)
    risk_module.compute(track_id=7, p_threat=1.0, d_persistence=1.0, c_density=1.0)
    r_final = risk_module.compute(track_id=7, p_threat=1.0, d_persistence=1.0, c_density=1.0)
    assert r_final.confirmed is True


def test_persistence_tracker_all_suspicious():
    tracker = PersistenceTracker(window_seconds=2.0)
    now = time.time()
    for i in range(5):
        tracker.update(track_id=1, is_suspicious=True, timestamp=now + i * 0.1)
    assert tracker.get_persistence(1) == pytest.approx(1.0)


def test_persistence_tracker_mixed_events():
    tracker = PersistenceTracker(window_seconds=2.0)
    now = time.time()
    tracker.update(1, True, timestamp=now)
    tracker.update(1, False, timestamp=now + 0.1)
    tracker.update(1, True, timestamp=now + 0.2)
    tracker.update(1, False, timestamp=now + 0.3)
    assert tracker.get_persistence(1) == pytest.approx(0.5)


def test_persistence_tracker_expires_old_events():
    tracker = PersistenceTracker(window_seconds=1.0)
    now = time.time()
    tracker.update(1, True, timestamp=now - 5.0)   # far outside the window
    tracker.update(1, False, timestamp=now)
    assert tracker.get_persistence(1) == pytest.approx(0.0)


def test_frame_sequence_buffer_pads_when_not_full():
    buf = FrameSequenceBuffer(seq_len=4, feature_dim=3)
    buf.push(track_id=1, feature_vector=np.array([1.0, 1.0, 1.0]))
    seq = buf.get_sequence(1)
    assert seq.shape == (4, 3)
    assert not buf.is_ready(1)
    assert np.allclose(seq[-1], [1.0, 1.0, 1.0])
    assert np.allclose(seq[0], [0.0, 0.0, 0.0])


def test_frame_sequence_buffer_ready_when_full():
    buf = FrameSequenceBuffer(seq_len=2, feature_dim=2)
    buf.push(1, np.array([1.0, 2.0]))
    buf.push(1, np.array([3.0, 4.0]))
    assert buf.is_ready(1)
    seq = buf.get_sequence(1)
    assert np.allclose(seq, [[1.0, 2.0], [3.0, 4.0]])


def test_crowd_density_normalisation_clips_to_unit_interval():
    density = compute_crowd_density(num_detections=1000, frame_area=640 * 640, reference_density=0.0008)
    assert 0.0 <= density <= 1.0


def test_crowd_density_zero_detections():
    assert compute_crowd_density(0, 640 * 640) == 0.0


# ======================================================================
# Two-level architecture: Module 4A (Rule Engine) + Module 5 Level-1/Level-2
# ======================================================================

def test_rule_engine_loitering_triggers_after_threshold():
    engine = RuleEngineModule()
    engine.cfg.loitering.seconds_threshold = 0.05
    engine.cfg.loitering.radius_px = 1000  # generous, so tiny jitter doesn't reset the anchor

    bbox = (100, 100, 160, 260)
    det = Detection(track_id=1, bbox=bbox, confidence=0.9, class_id=0)

    t0 = time.time()
    r1 = engine.evaluate([det], [], (480, 640), timestamp=t0)
    assert r1[1].score == 0.0  # not loitering yet

    r2 = engine.evaluate([det], [], (480, 640), timestamp=t0 + 0.2)
    assert "loitering" in r2[1].triggered_rules
    assert r2[1].score > 0.0


def test_rule_engine_running_triggers_on_high_speed():
    engine = RuleEngineModule()
    engine.cfg.running.speed_px_per_sec_threshold = 50

    t0 = time.time()
    det_a = Detection(track_id=2, bbox=(0, 0, 50, 150), confidence=0.9, class_id=0)
    engine.evaluate([det_a], [], (480, 640), timestamp=t0)

    det_b = Detection(track_id=2, bbox=(200, 0, 250, 150), confidence=0.9, class_id=0)
    result = engine.evaluate([det_b], [], (480, 640), timestamp=t0 + 0.2)
    assert "running" in result[2].triggered_rules


def test_rule_engine_crowd_density_triggers_for_all_persons():
    engine = RuleEngineModule()
    engine.cfg.crowd_density.count_threshold = 2
    dets = [Detection(track_id=i, bbox=(i * 10, 0, i * 10 + 40, 100), confidence=0.9, class_id=0)
            for i in range(3)]
    result = engine.evaluate(dets, [], (480, 640), timestamp=time.time())
    assert all("crowd_density" in result[d.track_id].triggered_rules for d in dets)


def test_level1_below_threshold_yields_warning_tier_no_ai():
    risk = RiskAssessmentModule()
    from modules.module4a_rule_engine import RuleResult
    rule_result = RuleResult(score=0.55, triggered_rules=["loitering"])
    level1 = risk.evaluate_level1(rule_result)
    assert level1.tier == "warning"
    assert level1.ai_verified is False
    assert risk.needs_ai_verification(level1.score) is False


def test_level1_above_threshold_escalates_to_ai_verification():
    risk = RiskAssessmentModule()
    from modules.module4a_rule_engine import RuleResult
    rule_result = RuleResult(score=0.9, triggered_rules=["restricted_zone"])
    level1 = risk.evaluate_level1(rule_result)
    assert risk.needs_ai_verification(level1.score) is True


def test_level2_confirms_after_three_consecutive_high_confidence_windows():
    risk = RiskAssessmentModule()
    from modules.module4a_rule_engine import RuleResult
    level1 = risk.evaluate_level1(RuleResult(score=0.9, triggered_rules=["restricted_zone"]))

    r1 = risk.evaluate_level2(track_id=9, level1_result=level1, p_threat=0.9)
    assert r1.confirmed is False
    r2 = risk.evaluate_level2(track_id=9, level1_result=level1, p_threat=0.9)
    assert r2.confirmed is False
    r3 = risk.evaluate_level2(track_id=9, level1_result=level1, p_threat=0.9)
    assert r3.confirmed is True
    assert r3.tier == "critical"
    assert r3.ai_verified is True
