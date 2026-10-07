DEFINITION = {
    "name": "sleep_hours",
    "version": 1,
    "entity": "days",
    "sql_type": "REAL",
    "unit": "hours",
    "inputs": ["sleep"],
    "lookback_days": 0,
    "description": "Garmin sleepTimeSeconds divided by 3600, on the requested sleep date.",
}


def calculate(inputs):
    seconds = inputs["sleep"][0].get("dailySleepDTO", {}).get("sleepTimeSeconds")
    return seconds / 3600 if seconds is not None else None
