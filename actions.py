from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Text

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.forms import FormValidationAction
from rasa_sdk.types import DomainDict
from rasa_sdk.events import SlotSet, EventType, UserUtteranceReverted


YES_SET = {"yes", "y", "yeah", "yep", "true", "1", "ok", "okay"}
NO_SET = {"no", "n", "nope", "false", "0"}


def to_bool(val: Any) -> Optional[bool]:
    """Convert common yes/no forms to bool. Returns None if unrecognized."""
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in YES_SET:
        return True
    if s in NO_SET:
        return False
    return None


def norm_text(val: Any) -> Optional[str]:
    """Trim text; return None for empty."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def norm_hazard(raw: Any) -> Optional[str]:
    """Normalize hazard type to a canonical label."""
    s = norm_text(raw)
    if not s:
        return None
    s = s.lower()

    # direct canonical values
    allowed = {"earthquake", "building collapse", "flood", "wildfire", "fire", "storm", "other"}
    if s in allowed:
        return s

    # mapping synonyms
    mapping = {
        "eq": "earthquake",
        "earth quake": "earthquake",
        "aftershock": "earthquake",
        "collapse": "building collapse",
        "rubble": "building collapse",
        "flooding": "flood",
        "wild fire": "wildfire",
        "forest fire": "wildfire",
        "smoke": "fire",
        "gas leak": "fire",
        "hurricane": "storm",
        "cyclone": "storm",
        "typhoon": "storm",
        "tornado": "storm",
        "unknown": "other",
    }
    if s in mapping:
        return mapping[s]

    # heuristic mapping for phrases
    if "earthquake" in s or "aftershock" in s:
        return "earthquake"
    if "collapse" in s or "rubble" in s:
        return "building collapse"
    if "flood" in s:
        return "flood"
    if "wild" in s and "fire" in s:
        return "wildfire"
    if "fire" in s or "smoke" in s or "gas" in s:
        return "fire"
    if "storm" in s or "hurricane" in s or "tornado" in s or "cyclone" in s:
        return "storm"

    return "other"


def safe_slot_bool(tracker: Tracker, slot_name: str, default: bool = False) -> bool:
    """Safely read a boolean slot even if it was stored as 'yes'/'no' strings."""
    b = to_bool(tracker.get_slot(slot_name))
    return b if b is not None else default


@dataclass
class RiskResult:
    level: str           # "low" | "medium" | "high"
    reasons: List[str]
    score: int


class ActionDefaultFallback(Action):
    def name(self) -> Text:
        return "action_default_fallback"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> List[EventType]:
        dispatcher.utter_message(
            text=(
                "I’m not fully sure I understood. Are you asking about:\n"
                "(1) immediate safety steps, (2) shelter, (3) evacuation, or (4) first aid?"
            )
        )
        return [UserUtteranceReverted()]


class ValidateTriageForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_triage_form"

    def validate_hazard_type(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        hazard = norm_hazard(slot_value)
        allowed = {"earthquake", "building collapse", "flood", "wildfire", "fire", "storm", "other"}

        if not hazard or hazard not in allowed:
            dispatcher.utter_message(
                text="Please tell me the emergency type: earthquake / building collapse / flood / wildfire / fire / storm / other."
            )
            return {"hazard_type": None}

        return {"hazard_type": hazard}

    def validate_location(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        loc = norm_text(slot_value)
        if not loc or len(loc) < 2:
            dispatcher.utter_message(text="Please type your location (city/area/landmark).")
            return {"location": None}
        return {"location": loc}

    def validate_injury_level(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        v = norm_text(slot_value)
        if not v:
            dispatcher.utter_message(text="Please answer: none, minor, or serious.")
            return {"injury_level": None}

        s = v.lower()
        allowed = {"none", "minor", "serious"}

        # infer from longer phrases
        if s not in allowed:
            if any(k in s for k in ["unconscious", "not breathing", "severe", "serious"]):
                s = "serious"
            elif any(k in s for k in ["bleed", "cut", "minor"]):
                s = "minor"
            elif "no" in s and "injur" in s:
                s = "none"

        if s not in allowed:
            dispatcher.utter_message(text="Please answer: none, minor, or serious.")
            return {"injury_level": None}

        return {"injury_level": s}

    def validate_is_trapped(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        b = to_bool(slot_value)
        if b is None:
            dispatcher.utter_message(text="Please answer yes or no: is anyone trapped?")
            return {"is_trapped": None}
        return {"is_trapped": b}

    def validate_is_fire_nearby(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        b = to_bool(slot_value)
        if b is None:
            dispatcher.utter_message(text="Please answer yes or no: is there fire/smoke or smell of gas nearby?")
            return {"is_fire_nearby": None}
        return {"is_fire_nearby": b}

    def validate_flood_depth(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        # Only meaningful if hazard is flood; otherwise force "none"
        hazard = (tracker.get_slot("hazard_type") or "").strip().lower()
        hazard = norm_hazard(hazard) or "other"
        if hazard != "flood":
            return {"flood_depth": "none"}

        v = norm_text(slot_value)
        if not v:
            dispatcher.utter_message(text="Please answer: none / ankle / knee / waist / above waist.")
            return {"flood_depth": None}

        s = v.lower()
        allowed = {"none", "ankle", "knee", "waist", "above waist"}

        if "above" in s:
            s = "above waist"

        if s not in allowed:
            dispatcher.utter_message(text="Please answer: none / ankle / knee / waist / above waist.")
            return {"flood_depth": None}

        return {"flood_depth": s}


def assess_risk(
    hazard: str,
    injury: str,
    trapped: bool,
    fire: bool,
    flood_depth: str,
) -> RiskResult:
    reasons: List[str] = []
    score = 0

    # Injury
    if injury == "serious":
        score += 4
        reasons.append("serious injury reported")
    elif injury == "minor":
        score += 1
        reasons.append("minor injury reported")

    # Trapped
    if trapped:
        score += 4
        reasons.append("someone is trapped")

    # Fire/gas/smoke nearby
    if fire:
        score += 3
        reasons.append("fire/smoke/gas risk nearby")

    # Hazard-specific risk adders
    if hazard in {"earthquake", "building collapse"}:
        score += 2
        reasons.append("structural hazard possible (aftershock/collapse risk)")

    if hazard in {"wildfire", "fire"}:
        score += 2
        reasons.append("rapidly spreading hazard possible")

    if hazard == "flood":
        if flood_depth in {"waist", "above waist"}:
            score += 3
            reasons.append(f"dangerous flood water level ({flood_depth})")
        elif flood_depth in {"ankle", "knee"}:
            score += 1
            reasons.append(f"flooding present ({flood_depth})")

    # Thresholds
    if score >= 7:
        level = "high"
    elif score >= 3:
        level = "medium"
    else:
        level = "low"

    return RiskResult(level=level, reasons=reasons, score=score)


class ActionRiskAssessment(Action):
    def name(self) -> Text:
        return "action_risk_assessment"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> List[EventType]:
        hazard = norm_hazard(tracker.get_slot("hazard_type")) or "other"
        location = tracker.get_slot("location") or "your area"
        injury = (tracker.get_slot("injury_level") or "none").strip().lower()

        # IMPORTANT: do NOT use bool(slot) because "no" -> True
        trapped = safe_slot_bool(tracker, "is_trapped", default=False)
        fire = safe_slot_bool(tracker, "is_fire_nearby", default=False)

        flood_depth = (tracker.get_slot("flood_depth") or "none").strip().lower()
        if hazard != "flood":
            flood_depth = "none"

        result = assess_risk(hazard, injury, trapped, fire, flood_depth)

        # Deterministic, hazard-aware guidance
        if result.level == "high":
            dispatcher.utter_message(
                text=(
                    f"Risk level: HIGH. If you are in immediate danger in {location}, contact emergency services now. "
                    "If safe to do so: move away from fire/smoke/gas, avoid unstable structures, and seek help from nearby people."
                )
            )
        elif result.level == "medium":
            dispatcher.utter_message(
                text=(
                    f"Risk level: MEDIUM. Stay alert in {location}. Move to a safer area if conditions change. "
                    "Avoid hazards (downed lines, smoke, unstable buildings) and prepare to evacuate if advised by authorities."
                )
            )
        else:
            dispatcher.utter_message(
                text=(
                    f"Risk level: LOW (based on current answers). Stay informed and follow official instructions for {location}. "
                    "If anything changes (injury, trapped, fire/gas), tell me immediately."
                )
            )

        reasons_str = ", ".join(result.reasons) if result.reasons else "no critical flags detected"
        summary = (
            f"HAZARD={hazard} | LOCATION={location} | INJURY={injury} | "
            f"TRAPPED={trapped} | FIRE/GAS={fire} | FLOOD_DEPTH={flood_depth} | "
            f"SCORE={result.score} | RISK={result.level.upper()} | REASONS={reasons_str}"
        )

        return [
            SlotSet("risk_level", result.level),
            SlotSet("handover_summary", summary),
        ]


class ActionHumanHandover(Action):
    def name(self) -> Text:
        return "action_human_handover"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> List[EventType]:
        risk = (tracker.get_slot("risk_level") or "").strip().lower()
        summary = tracker.get_slot("handover_summary") or ""

        if risk == "high":
            dispatcher.utter_message(text="I’m escalating this to a human operator. Please stay available.")
            dispatcher.utter_message(text=f"[Handover summary for operator]\n{summary}")
        else:
            dispatcher.utter_message(text="If you want, I can escalate to a human operator. Say: 'escalate to human'.")

        return []


class ActionShelterLookup(Action):
    def name(self) -> Text:
        return "action_shelter_lookup"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> List[EventType]:
        # Placeholder: replace with a real shelter API / GIS lookup later
        location = tracker.get_slot("location") or "your area"
        dispatcher.utter_message(
            text=(
                f"I don’t have a live shelter database connected yet. For {location}: "
                "check official emergency channels or municipal updates for the nearest open shelters. "
                "If you share a more specific landmark, I can narrow general guidance."
            )
        )
        return []
