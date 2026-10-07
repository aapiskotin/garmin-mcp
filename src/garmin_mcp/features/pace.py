DEFINITION = {
    "name": "pace",
    "version": 1,
    "entity": "activities",
    "sql_type": "REAL",
    "unit": "seconds/km",
    "inputs": ["activity_list"],
    "lookback_days": 0,
    "description": "Elapsed duration divided by distance; absent for zero-distance activities.",
}


def calculate(inputs):
    raw = inputs["activity_list"][0]
    summary = raw.get("summaryDTO") or raw
    distance, duration = summary.get("distance"), summary.get("duration")
    if distance is None or duration is None or distance <= 0:
        return None
    return duration * 1000 / distance
