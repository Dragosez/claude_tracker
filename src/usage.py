"""Parsing helpers for the claude.ai /usage API response."""

# Friendly names for legacy per-model usage keys
MODEL_MAPPINGS = {
    "seven_day_omelette": "Claude Design",
    "seven_day_sonnet": "Sonnet",
    "seven_day_fable": "Fable",
    "seven_day_opus": "Opus",
    "seven_day_haiku": "Haiku",
    "iguana_necktie": "Fable",
}

# Legacy keys that are shown even at 0% utilization
ALWAYS_SHOW_KEYS = ("iguana_necktie", "seven_day_fable")


def _legacy_name(key):
    if key in MODEL_MAPPINGS:
        return MODEL_MAPPINGS[key]
    if key.startswith("seven_day_"):
        return key[len("seven_day_"):].replace("_", " ").title()
    return key.replace("_", " ").title()


def _to_percent(utilization):
    if isinstance(utilization, float) and utilization <= 1.0:
        return int(utilization * 100)
    return int(utilization)


def extract_model_limits(data):
    """Return per-model usage rows from a /usage API response.

    Each row is {"key", "name", "percent", "resets_at"}, sorted by name.

    Modern responses report model usage as scoped entries in the `limits`
    array (the legacy seven_day_*/iguana_necktie keys are null there);
    older responses only have the legacy keys, so fall back to those.
    """
    rows = []

    for entry in data.get("limits") or []:
        scope = entry.get("scope") or {}
        model = scope.get("model") or {}
        name = model.get("display_name")
        if not name:
            continue
        rows.append({
            "key": f"scoped:{name}",
            "name": name,
            "percent": int(entry.get("percent") or 0),
            "resets_at": entry.get("resets_at"),
        })

    if not rows:
        for key, value in data.items():
            if not (key.startswith("seven_day_") or key == "iguana_necktie"):
                continue
            if value is None:
                continue
            percent = _to_percent(value.get("utilization", 0))
            if percent <= 0 and key not in ALWAYS_SHOW_KEYS:
                continue
            rows.append({
                "key": key,
                "name": _legacy_name(key),
                "percent": percent,
                "resets_at": value.get("resets_at"),
            })

    rows.sort(key=lambda r: r["name"])
    return rows
