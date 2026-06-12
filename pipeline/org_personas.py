"""
pipeline/org_personas.py
────────────────────────
Enforce customer-declared personas on IntelligenceConfig after the orchestrator.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from schemas.config import DashboardSection, IntelligenceConfig, Persona, PersonaView

log = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _persona_names_match(role: str, org_name: str) -> bool:
    r, n = _norm(role), _norm(org_name)
    if not r or not n:
        return False
    if r == n:
        return True
    return r in n or n in r


def enforce_org_personas(
    config: IntelligenceConfig,
    required: list[dict[str, Any]] | None,
) -> list[str]:
    """
    Align config.personas to the org onboarding list.

    - Drops agent-invented personas not in the required list
    - Adds empty shells for required personas the agent missed
    - Sets persona.org_persona_id and normalizes role to org name
    - Reorders to match onboarding order

    Returns human-readable change messages.
    """
    if not required:
        return []

    changes: list[str] = []
    order = {str(p["id"]): i for i, p in enumerate(required)}
    used_ids: set[str] = set()
    kept: list[PersonaView] = []

    for pv in config.personas:
        role = pv.persona.role
        matched = None
        for req in required:
            rid = str(req["id"])
            if rid in used_ids:
                continue
            if _persona_names_match(role, str(req.get("name", ""))):
                matched = req
                break
        if matched:
            rid = str(matched["id"])
            pv.persona.org_persona_id = rid
            if pv.persona.role != matched["name"]:
                changes.append(f"persona role '{pv.persona.role}' → '{matched['name']}'")
                pv.persona.role = str(matched["name"])
            kept.append(pv)
            used_ids.add(rid)
        else:
            changes.append(f"dropped agent persona '{role}' (not in org list)")

    for req in required:
        rid = str(req["id"])
        if rid in used_ids:
            continue
        name = str(req.get("name", "Persona"))
        kept.append(
            PersonaView(
                persona=Persona(
                    role=name,
                    org_persona_id=rid,
                    focus_areas=[],
                    rationale=f"Reserved for {name} — re-run pipeline to populate KPIs.",
                    persona_level="manager",
                ),
                dashboard_sections=[
                    DashboardSection(
                        id=f"{_norm(name).replace(' ', '_')}_overview",
                        title=f"{name} Overview",
                        description="Awaiting KPI design for this persona.",
                        kpis=[],
                    )
                ],
            )
        )
        changes.append(f"added shell persona '{name}' (agent did not emit)")

    kept.sort(key=lambda pv: order.get(pv.persona.org_persona_id or "", 9999))
    config.personas = kept
    return changes


def filter_config_for_persona(
    config_dict: dict[str, Any],
    org_persona_id: str | None,
) -> dict[str, Any]:
    """Return a copy of the config with only the matching PersonaView."""
    if not org_persona_id:
        return config_dict
    personas = config_dict.get("personas") or []
    matched = [
        p for p in personas
        if (p.get("persona") or {}).get("org_persona_id") == org_persona_id
    ]
    if not matched:
        # Fallback: match by role name if id not stamped yet (legacy configs)
        return config_dict
    out = dict(config_dict)
    out["personas"] = matched
    return out
