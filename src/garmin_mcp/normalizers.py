from __future__ import annotations

from typing import Any


def _compact(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return None


def _pace_seconds_per_km(speed: Any) -> float | None:
    if not isinstance(speed, (int, float)) or speed <= 0:
        return None
    return round(1000.0 / speed, 1)


def normalize_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Return planning-relevant activity fields without location or owner data."""
    summary = payload.get("summaryDTO") or payload
    activity_type = payload.get("activityTypeDTO") or payload.get("activityType") or {}
    speed = _first(summary, "averageMovingSpeed", "averageSpeed")
    sport = activity_type.get("typeKey")

    zones = {
        f"zone_{zone}": payload.get(f"hrTimeInZone_{zone}")
        for zone in range(1, 6)
        if payload.get(f"hrTimeInZone_{zone}") is not None
    }

    result = _compact(
        {
            "activity_id": payload.get("activityId"),
            "name": payload.get("activityName"),
            "sport": sport,
            "start_time_local": summary.get("startTimeLocal"),
            "distance_m": summary.get("distance"),
            "duration_s": summary.get("duration"),
            "moving_duration_s": summary.get("movingDuration"),
            "elapsed_duration_s": summary.get("elapsedDuration"),
            "average_speed_mps": speed,
            "average_pace_seconds_per_km": _pace_seconds_per_km(speed),
            "max_speed_mps": summary.get("maxSpeed"),
            "average_heart_rate_bpm": summary.get("averageHR"),
            "max_heart_rate_bpm": summary.get("maxHR"),
            "average_power_watts": _first(summary, "avgPower", "averagePower"),
            "normalized_power_watts": summary.get("normalizedPower"),
            "max_power_watts": summary.get("maxPower"),
            "average_cadence": _first(
                summary,
                "averageRunningCadenceInStepsPerMinute",
                "averageRunCadence",
                "averageBikingCadenceInRevPerMinute",
            ),
            "max_cadence": _first(
                summary,
                "maxRunningCadenceInStepsPerMinute",
                "maxRunCadence",
                "maxBikingCadenceInRevPerMinute",
            ),
            "elevation_gain_m": summary.get("elevationGain"),
            "elevation_loss_m": summary.get("elevationLoss"),
            "calories": summary.get("calories"),
            "steps": summary.get("steps"),
            "training_effect_label": summary.get("trainingEffectLabel"),
            "aerobic_training_effect": summary.get("aerobicTrainingEffect"),
            "anaerobic_training_effect": summary.get("anaerobicTrainingEffect"),
            "aerobic_training_effect_message": summary.get("aerobicTrainingEffectMessage"),
            "anaerobic_training_effect_message": summary.get(
                "anaerobicTrainingEffectMessage"
            ),
            "body_battery_change": summary.get("differenceBodyBattery"),
            "workout_id": payload.get("workoutId"),
            "lap_count": payload.get("lapCount"),
        }
    )
    if zones:
        result["heart_rate_zone_seconds"] = zones
    return result


def normalize_splits(payload: dict[str, Any]) -> dict[str, Any]:
    """Return compact lap metrics without coordinates or raw sensor streams."""
    laps = []
    for lap in payload.get("lapDTOs") or []:
        speed = _first(lap, "averageMovingSpeed", "averageSpeed")
        laps.append(
            _compact(
                {
                    "lap_index": lap.get("lapIndex"),
                    "workout_step_index": lap.get("wktStepIndex"),
                    "intensity_type": lap.get("intensityType"),
                    "distance_m": lap.get("distance"),
                    "duration_s": lap.get("duration"),
                    "moving_duration_s": lap.get("movingDuration"),
                    "elapsed_duration_s": lap.get("elapsedDuration"),
                    "average_speed_mps": speed,
                    "average_pace_seconds_per_km": _pace_seconds_per_km(speed),
                    "max_speed_mps": lap.get("maxSpeed"),
                    "average_heart_rate_bpm": lap.get("averageHR"),
                    "max_heart_rate_bpm": lap.get("maxHR"),
                    "average_cadence": _first(
                        lap,
                        "averageRunCadence",
                        "averageBikingCadenceInRevPerMinute",
                    ),
                    "max_cadence": _first(
                        lap,
                        "maxRunCadence",
                        "maxBikingCadenceInRevPerMinute",
                    ),
                    "elevation_gain_m": lap.get("elevationGain"),
                    "elevation_loss_m": lap.get("elevationLoss"),
                }
            )
        )
    return {
        "activity_id": payload.get("activityId"),
        "lap_count": len(laps),
        "laps": laps,
    }


def normalize_sleep(payload: dict[str, Any]) -> dict[str, Any]:
    daily = payload.get("dailySleepDTO") or {}
    metrics = _compact(
        {
            "sleep_start_local": daily.get("sleepStartTimestampLocal"),
            "sleep_end_local": daily.get("sleepEndTimestampLocal"),
            "total_sleep_seconds": daily.get("sleepTimeSeconds"),
            "deep_sleep_seconds": daily.get("deepSleepSeconds"),
            "light_sleep_seconds": daily.get("lightSleepSeconds"),
            "rem_sleep_seconds": daily.get("remSleepSeconds"),
            "awake_seconds": daily.get("awakeSleepSeconds"),
            "nap_seconds": daily.get("napTimeSeconds"),
        }
    )
    return {"available": bool(metrics), "date": daily.get("calendarDate"), **metrics}


def normalize_hrv(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"available": False}
    summary = payload.get("hrvSummary") or payload
    return _compact(
        {
            "available": True,
            "date": _first(summary, "calendarDate", "date"),
            "weekly_average_ms": _first(summary, "weeklyAvg", "weeklyAverage"),
            "last_night_average_ms": _first(summary, "lastNightAvg", "lastNightAverage"),
            "last_night_5_min_high_ms": _first(
                summary, "lastNight5MinHigh", "lastNightFiveMinuteHigh"
            ),
            "baseline_low_ms": _first(summary, "baselineLowUpper", "baselineLow"),
            "baseline_high_ms": _first(summary, "baselineBalancedUpper", "baselineHigh"),
            "status": _first(summary, "status", "statusKey"),
            "feedback": _first(summary, "feedbackPhrase", "feedback"),
        }
    )


def normalize_body_battery(payload: list[dict[str, Any]]) -> dict[str, Any]:
    if not payload:
        return {"available": False}
    days = []
    for day in payload:
        values = day.get("bodyBatteryValuesArray") or []
        numeric_values = [
            item[-1]
            for item in values
            if isinstance(item, (list, tuple))
            and item
            and isinstance(item[-1], (int, float))
        ]
        days.append(
            _compact(
                {
                    "date": day.get("date"),
                    "charged": day.get("charged"),
                    "drained": day.get("drained"),
                    "minimum": min(numeric_values) if numeric_values else None,
                    "maximum": max(numeric_values) if numeric_values else None,
                    "latest": numeric_values[-1] if numeric_values else None,
                }
            )
        )
    available = any(any(key != "date" for key in day) for day in days)
    return {"available": available, "days": days}


def normalize_training_readiness(payload: list[dict[str, Any]]) -> dict[str, Any]:
    if not payload:
        return {"available": False}
    item = payload[0]
    return _compact(
        {
            "available": True,
            "date": item.get("calendarDate"),
            "score": item.get("score"),
            "level": _first(item, "level", "levelKey"),
            "feedback_short": item.get("feedbackShort"),
            "feedback_long": item.get("feedbackLong"),
            "recovery_time": item.get("recoveryTime"),
            "acute_load": item.get("acuteLoad"),
            "sleep_score": item.get("sleepScore"),
        }
    )


def normalize_training_status(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {"available": False}
    status = payload.get("mostRecentTrainingStatus")
    load = payload.get("mostRecentTrainingLoadBalance")
    vo2_max = payload.get("mostRecentVO2Max")
    acclimation = payload.get("heatAltitudeAcclimationDTO")
    status_data = status.get("latestTrainingStatusData", status) if isinstance(status, dict) else {}
    load_data = load if isinstance(load, dict) else {}
    vo2_data = vo2_max if isinstance(vo2_max, dict) else {}
    acclimation_data = acclimation if isinstance(acclimation, dict) else {}

    metrics = _compact(
        {
            "date": _first(status_data, "calendarDate", "date"),
            "status": _first(status_data, "trainingStatus", "status", "statusKey"),
            "feedback": _first(
                status_data, "trainingStatusFeedbackPhrase", "feedbackPhrase", "feedback"
            ),
            "weekly_training_load": _first(
                status_data, "weeklyTrainingLoad", "trainingLoad"
            ),
            "acute_training_load": _first(load_data, "acuteTrainingLoad", "acuteLoad"),
            "chronic_training_load": _first(load_data, "chronicTrainingLoad", "chronicLoad"),
            "acute_chronic_ratio": _first(
                load_data, "acuteChronicWorkloadRatio", "acuteChronicRatio"
            ),
            "load_balance": _first(load_data, "trainingLoadBalance", "loadBalance"),
            "vo2_max": _first(vo2_data, "vo2MaxPreciseValue", "vo2MaxValue", "vo2Max"),
            "heat_acclimation": _first(
                acclimation_data, "heatAcclimation", "heatAcclimationPercent"
            ),
            "altitude_acclimation": _first(
                acclimation_data, "altitudeAcclimation", "altitudeAcclimationPercent"
            ),
        }
    )
    return {"available": bool(metrics), **metrics}
