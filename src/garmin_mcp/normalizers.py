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


def _recency_key(source: dict[str, Any]) -> str:
    value = _first(source, "timestamp", "timestampLocal", "calendarDate", "date")
    return str(value) if value is not None else ""


def _latest_mapping_value(source: dict[str, Any]) -> dict[str, Any]:
    values = [value for value in source.values() if isinstance(value, dict)]
    return max(values, key=_recency_key, default={})


def _metric_data(
    source: Any,
    direct_keys: tuple[str, ...],
    preferred_nested_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    if any(key in source for key in direct_keys):
        return source
    for key in preferred_nested_keys:
        nested = source.get(key)
        if isinstance(nested, dict):
            if any(metric_key in nested for metric_key in direct_keys):
                return nested
            nested_value = _latest_mapping_value(nested)
            if nested_value:
                return nested_value
    return _latest_mapping_value(source)


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
            "normalized_power_watts": _first(summary, "normalizedPower", "normPower"),
            "max_power_watts": summary.get("maxPower"),
            "average_cadence": _first(
                summary,
                "averageRunningCadenceInStepsPerMinute",
                "averageRunCadence",
                "averageBikingCadenceInRevPerMinute",
                "averageBikeCadence",
            ),
            "max_cadence": _first(
                summary,
                "maxRunningCadenceInStepsPerMinute",
                "maxRunCadence",
                "maxBikingCadenceInRevPerMinute",
                "maxBikeCadence",
            ),
            "elevation_gain_m": summary.get("elevationGain"),
            "elevation_loss_m": summary.get("elevationLoss"),
            "calories": summary.get("calories"),
            "steps": summary.get("steps"),
            "training_effect_label": summary.get("trainingEffectLabel"),
            "aerobic_training_effect": summary.get("aerobicTrainingEffect"),
            "anaerobic_training_effect": summary.get("anaerobicTrainingEffect"),
            "aerobic_training_effect_message": summary.get("aerobicTrainingEffectMessage"),
            "anaerobic_training_effect_message": summary.get("anaerobicTrainingEffectMessage"),
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
                        "averageBikeCadence",
                    ),
                    "max_cadence": _first(
                        lap,
                        "maxRunCadence",
                        "maxBikingCadenceInRevPerMinute",
                        "maxBikeCadence",
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
    baseline = summary.get("baseline")
    if not isinstance(baseline, dict):
        baseline = {}
    baseline_low = _first(baseline, "balancedLow", "lowUpper")
    if baseline_low is None:
        baseline_low = _first(summary, "baselineLowUpper", "baselineLow")
    baseline_high = _first(baseline, "balancedUpper")
    if baseline_high is None:
        baseline_high = _first(summary, "baselineBalancedUpper", "baselineHigh")
    return _compact(
        {
            "available": True,
            "date": _first(summary, "calendarDate", "date"),
            "weekly_average_ms": _first(summary, "weeklyAvg", "weeklyAverage"),
            "last_night_average_ms": _first(summary, "lastNightAvg", "lastNightAverage"),
            "last_night_5_min_high_ms": _first(
                summary, "lastNight5MinHigh", "lastNightFiveMinuteHigh"
            ),
            "baseline_low_ms": baseline_low,
            "baseline_high_ms": baseline_high,
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
            if isinstance(item, (list, tuple)) and item and isinstance(item[-1], (int, float))
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


def normalize_training_readiness(
    payload: list[dict[str, Any]] | dict[str, Any] | None,
) -> dict[str, Any]:
    if not payload:
        return {"available": False}
    if isinstance(payload, dict):
        item = payload
    else:
        snapshots = [snapshot for snapshot in payload if isinstance(snapshot, dict)]
        item = max(snapshots, key=_recency_key, default={})
    if not item:
        return {"available": False}
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
    raw_status_data = (
        status.get("latestTrainingStatusData", status) if isinstance(status, dict) else {}
    )
    status_data = _metric_data(
        raw_status_data,
        ("calendarDate", "date", "trainingStatus", "status", "statusKey"),
    )
    acute_load_data = status_data.get("acuteTrainingLoadDTO")
    if not isinstance(acute_load_data, dict):
        acute_load_data = {}
    load_data = _metric_data(
        load,
        (
            "acuteTrainingLoad",
            "acuteLoad",
            "chronicTrainingLoad",
            "chronicLoad",
            "acuteChronicWorkloadRatio",
            "acuteChronicRatio",
            "trainingLoadBalance",
            "loadBalance",
        ),
        ("metricsTrainingLoadBalanceDTOMap",),
    )
    vo2_data = _metric_data(
        vo2_max,
        ("vo2MaxPreciseValue", "vo2MaxValue", "vo2Max"),
        ("generic",),
    )
    acclimation_data = acclimation if isinstance(acclimation, dict) else {}
    acute_training_load = _first(
        acute_load_data,
        "dailyTrainingLoadAcute",
        "acuteTrainingLoad",
        "acuteLoad",
    )
    if acute_training_load is None:
        acute_training_load = _first(load_data, "acuteTrainingLoad", "acuteLoad")
    chronic_training_load = _first(
        acute_load_data,
        "dailyTrainingLoadChronic",
        "chronicTrainingLoad",
        "chronicLoad",
    )
    if chronic_training_load is None:
        chronic_training_load = _first(load_data, "chronicTrainingLoad", "chronicLoad")
    acute_chronic_ratio = _first(
        acute_load_data,
        "dailyAcuteChronicWorkloadRatio",
        "acuteChronicWorkloadRatio",
        "acuteChronicRatio",
    )
    if acute_chronic_ratio is None:
        acute_chronic_ratio = _first(
            load_data, "acuteChronicWorkloadRatio", "acuteChronicRatio"
        )

    metrics = _compact(
        {
            "date": _first(status_data, "calendarDate", "date"),
            "status": _first(status_data, "trainingStatus", "status", "statusKey"),
            "feedback": _first(
                status_data, "trainingStatusFeedbackPhrase", "feedbackPhrase", "feedback"
            ),
            "weekly_training_load": _first(status_data, "weeklyTrainingLoad", "trainingLoad"),
            "acute_training_load": acute_training_load,
            "chronic_training_load": chronic_training_load,
            "acute_chronic_ratio": acute_chronic_ratio,
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
