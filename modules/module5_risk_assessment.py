"""
MODULE 5 — RISK ASSESSMENT MODULE (TWO-LEVEL)
=================================================
Implements the two-level escalation logic:

    Level 1 (rule-based, no AI):
        risk_score = Rule Score  (Module 4A's max triggered-rule score)
        if risk_score <  LEVEL1_THRESHOLD -> "warning" tier -> dashboard alert + beep,
                                              continue monitoring, NO AI is used.
        if risk_score >= LEVEL1_THRESHOLD -> escalate to Level 2.

    Level 2 (AI verification, only for escalated tracks):
        Module 4B (CNN -> LSTM) produces P_threat for the escalated track.
        final_score = level1_weight * risk_score + level2_weight * P_threat
        "confirmed" once P_threat clears the AI confirmation threshold for
        `confirmation_windows` consecutive frames -> "critical" tier ->
        siren + evidence save + push notification.

A legacy single-formula `compute()` (R = alpha*P_threat + beta*D_persistence +
gamma*C_density) is retained for backward compatibility / unit tests that
target the original Module 5 spec.
"""
from dataclasses import dataclass, field
from collections import deque, defaultdict
from typing import List

from modules.config import CFG


@dataclass
class RiskResult:
    score: float
    tier: str
    level: int = 1                 # 1 = rule-based only, 2 = AI-verified
    triggered_rules: List[str] = field(default_factory=list)
    p_threat: float = 0.0
    d_persistence: float = 0.0
    c_density: float = 0.0
    ai_verified: bool = False
    confirmed: bool = False        # True once the N-consecutive-window rule is satisfied


TIER_ACTIONS = {
    "normal":   "No action — routine monitoring continues.",
    "elevated": "Log event; increase sampling attention on this track_id.",
    "warning":  "Level-1 rule triggered — dashboard yellow alert + beep. Awaiting AI verification.",
    "high":     "Flag on operator dashboard; begin short-term recording clip.",
    "critical": "AI-verified — trigger siren + push notification + save evidence.",
}


class RiskAssessmentModule:
    def __init__(self, alpha: float = None, beta: float = None, gamma: float = None,
                 tiers: dict = None, confirmation_windows: int = None):
        weights = CFG.risk_weights
        self.alpha = alpha if alpha is not None else weights.alpha
        self.beta = beta if beta is not None else weights.beta
        self.gamma = gamma if gamma is not None else weights.gamma
        self.level1_weight = weights.level1_weight
        self.level2_weight = weights.level2_weight

        self.tiers = tiers or CFG.risk_tiers.as_dict()
        self.level1_threshold = CFG.level1_ai_verification_threshold

        ai_cfg = CFG.ai_verification
        self.confirmation_windows = confirmation_windows or ai_cfg.confirmation_windows
        self.confirm_probability_threshold = ai_cfg.confirm_probability_threshold

        # rolling AI-confirmation history per tracked person (Level-2 only)
        self._ai_history = defaultdict(lambda: deque(maxlen=self.confirmation_windows))
        # legacy rolling score history (kept for compute())
        self._history = defaultdict(lambda: deque(maxlen=self.confirmation_windows))

    # ------------------------------------------------------------------
    # LEVEL 1 — rule-based, no AI
    # ------------------------------------------------------------------
    def evaluate_level1(self, rule_result) -> RiskResult:
        """rule_result: modules.module4a_rule_engine.RuleResult"""
        score = max(0.0, min(1.0, rule_result.score))
        needs_escalation = score >= self.level1_threshold
        tier = self._tier_for_score(score) if not needs_escalation else "high"
        if not needs_escalation and rule_result.is_triggered:
            tier = "warning"
        return RiskResult(score=score, tier=tier, level=1,
                           triggered_rules=list(rule_result.triggered_rules),
                           ai_verified=False, confirmed=False)

    def needs_ai_verification(self, level1_score: float) -> bool:
        return level1_score >= self.level1_threshold

    # ------------------------------------------------------------------
    # LEVEL 2 — AI verification (CNN + LSTM), only for escalated tracks
    # ------------------------------------------------------------------
    def evaluate_level2(self, track_id, level1_result: RiskResult, p_threat: float) -> RiskResult:
        final_score = (self.level1_weight * level1_result.score +
                        self.level2_weight * p_threat)
        final_score = max(0.0, min(1.0, final_score))

        ai_confirms = p_threat >= self.confirm_probability_threshold
        self._ai_history[track_id].append(ai_confirms)
        confirmed = (len(self._ai_history[track_id]) == self.confirmation_windows and
                     all(self._ai_history[track_id]))

        tier = "critical" if (confirmed and ai_confirms) else self._tier_for_score(final_score)
        if tier != "critical":
            tier = "high" if final_score >= self.level1_threshold else self._tier_for_score(final_score)

        return RiskResult(score=final_score, tier=tier, level=2,
                           triggered_rules=list(level1_result.triggered_rules),
                           p_threat=p_threat, ai_verified=True, confirmed=confirmed)

    # ------------------------------------------------------------------
    # Legacy single-stage formula — retained for backward compatibility
    # ------------------------------------------------------------------
    def compute(self, track_id: int, p_threat: float, d_persistence: float,
                c_density: float) -> RiskResult:
        score = self.alpha * p_threat + self.beta * d_persistence + self.gamma * c_density
        score = max(0.0, min(1.0, score))
        tier = self._tier_for_score(score)

        self._history[track_id].append(tier == "critical")
        confirmed = (len(self._history[track_id]) == self.confirmation_windows and
                     all(self._history[track_id]))

        return RiskResult(score=score, tier=tier, level=2, p_threat=p_threat,
                           d_persistence=d_persistence, c_density=c_density,
                           ai_verified=True, confirmed=confirmed)

    # ------------------------------------------------------------------
    def _tier_for_score(self, score: float) -> str:
        for tier_name, (low, high) in self.tiers.items():
            if low <= score < high:
                return tier_name
        return "critical" if score >= 0.85 else "normal"  # fallback safety net

    def action_for(self, tier: str) -> str:
        return TIER_ACTIONS.get(tier, "Unknown tier — check config.yaml risk_tiers.")

    def forget(self, track_id: int):
        self._history.pop(track_id, None)
        self._ai_history.pop(track_id, None)
