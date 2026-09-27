"""Rubric grading for DevOps-Gym tasks.

Two sides to a grade:

  * **tests** — the task's own deterministic verifier (`tests/test.sh`),
    run inside the container by harbor. Binary reward.
  * **rubric** — `tests/rubric.json`, graded here by a judge council against
    the agent's transcript, the post-run workspace, and the tests' output.

The rubric side runs host-side after the trial because the trajectory does not
exist inside the container and judge credentials must never be placed there.
"""

from judge.council import (  # noqa: F401
    CouncilMember,
    MemberHealth,
    council_health,
    council_health_summary,
    council_members,
    grade_council,
    judge_pin_status,
    parse_verdict_text,
    transport_summary,
)

__all__ = [
    "CouncilMember",
    "MemberHealth",
    "council_health",
    "council_health_summary",
    "council_members",
    "grade_council",
    "judge_pin_status",
    "parse_verdict_text",
    "transport_summary",
]
