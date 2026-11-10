"""Actors and role-based access control. Enforced in the pipeline (actions) and in the UI (pages and cases).

POC: actors are picked in the sidebar (no login). Production path: IAM Identity Center / SSO groups map to these roles."""

from __future__ import annotations

from dataclasses import dataclass

from detective.models import Case

PAGES_ALL = ("console", "investigation", "case", "inbox", "performance", "results")
PAGES_OWNER = ("inbox", "case")
PAGES_PLATFORM = ("inbox", "case", "performance")


@dataclass(frozen=True)
class Actor:
    actor_id: str
    name: str
    role: str
    pages: tuple[str, ...]
    sees_all_cases: bool = False
    can_investigate: bool = False
    can_reroute_any: bool = False
    can_change_settings: bool = False


ACTORS: dict[str, Actor] = {a.actor_id: a for a in [
    Actor("finops-team", "FinOps team", "FinOps analyst", PAGES_ALL, sees_all_cases=True, can_investigate=True,
          can_reroute_any=True, can_change_settings=True),
    Actor("ravi.m", "Ravi M.", "ML engineer", PAGES_OWNER),
    Actor("anika.r", "Anika R.", "Data scientist", PAGES_OWNER),
    Actor("priya.s", "Priya S.", "Payments engineer", PAGES_OWNER),
    Actor("orders-team", "Orders team", "Service owner (orders-service)", PAGES_OWNER),
    Actor("platform-team", "Platform team", "Shared network owner; reviews AI performance", PAGES_PLATFORM),
]}

ACTIONS = ("approve", "planned", "not_mine", "reject")


class PermissionDenied(PermissionError):
    pass


def get_actor(actor_id: str) -> Actor:
    if actor_id not in ACTORS:
        raise PermissionDenied(f"Unknown actor {actor_id!r}")
    return ACTORS[actor_id]


def can_view(actor_id: str, case: Case) -> bool:
    actor = get_actor(actor_id)
    return actor.sees_all_cases or case.assigned_to == actor_id or any(d.reviewer == actor_id for d in case.decisions)


def can_act(actor_id: str, case: Case, action: str) -> bool:
    """Only the case's current owner decides; FinOps may also re-route any open case."""
    actor = get_actor(actor_id)
    if action not in ACTIONS:
        return False
    if case.assigned_to == actor_id:
        return True
    return action == "not_mine" and actor.can_reroute_any


def authorize(actor_id: str, case: Case, action: str) -> None:
    if not can_act(actor_id, case, action):
        raise PermissionDenied(f"{actor_id} may not '{action}' {case.case_id} (assigned to {case.assigned_to}).")


def visible_cases(actor_id: str, cases: list[Case]) -> list[Case]:
    return [c for c in cases if can_view(actor_id, c)]
