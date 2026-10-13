"""Tier routing. Order: explicit `tier:` in the task file > first matching keyword rule > optional classifier > economy.
Defaults never assume premium is better; rules only apply to `tier: auto` tasks."""
import json
import re

TIERS = ("economy", "premium")


def static(task, cfg):
    """Returns (tier | None, reason). None = undecided (classifier or economy default)."""
    if task.tier != "auto":
        return task.tier, "task file"
    text = f"{task.title}\n{task.body}"
    for rule in cfg.routes:
        for kw in rule["keywords"]:
            if re.search(rf"\b{re.escape(kw)}\b", text, re.I):
                return rule["tier"], f"keyword '{kw}'"
    return None, "no rule matched"


def classify_prompt(task):
    return (
        "Classify this software task for model routing. Answer with one JSON object and nothing else: "
        '{"tier": "economy" | "premium", "reason": "<short>"}. '
        "premium = needs deep reasoning, architecture or high-impact decisions; economy = routine implementation.\n\n"
        f"Title: {task.title}\n{task.body}")


def parse_classification(text):
    for blob in re.findall(r"\{[^{}]*\}", text or ""):
        try:
            tier = json.loads(blob).get("tier")
        except (ValueError, AttributeError):
            continue
        if tier in TIERS:
            return tier
    return None
