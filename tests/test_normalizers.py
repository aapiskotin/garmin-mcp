from garmin_mcp.normalizers import (
    normalize_activity,
    normalize_body_battery,
    normalize_hrv,
    normalize_sleep,
    normalize_splits,
    normalize_training_readiness,
    normalize_training_status,
)


def test_normalize_activity_keeps_planning_metrics_and_omits_location() -> None:
    result = normalize_activity(
        {
            "activityId": 123,
            "activityName": "Intervals",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-08-20 07:30:00",
            "distance": 5000.0,
            "duration": 1500.0,
            "averageSpeed": 4.0,
            "averageHR": 151,
            "normPower": 211,
            "startLatitude": 52.1,
            "ownerId": 99,
            "hrTimeInZone_2": 600.0,
        }
    )

    assert result["activity_id"] == 123
    assert result["average_pace_seconds_per_km"] == 250.0
    assert result["normalized_power_watts"] == 211
    assert result["heart_rate_zone_seconds"] == {"zone_2": 600.0}
    assert "startLatitude" not in result
    assert "ownerId" not in result


def test_normalize_activity_supports_detailed_response_shape() -> None:
    result = normalize_activity(
        {
            "activityId": 456,
            "activityName": "Easy",
            "activityTypeDTO": {"typeKey": "running"},
            "summaryDTO": {
                "distance": 10000.0,
                "averageMovingSpeed": 2.5,
                "averageRunCadence": 170,
                "normalizedPower": 212,
            },
        }
    )

    assert result["sport"] == "running"
    assert result["average_pace_seconds_per_km"] == 400.0
    assert result["average_cadence"] == 170
    assert result["normalized_power_watts"] == 212


def test_normalize_activity_supports_detailed_cycling_cadence() -> None:
    result = normalize_activity(
        {
            "activityId": 789,
            "activityTypeDTO": {"typeKey": "cycling"},
            "summaryDTO": {
                "averageBikeCadence": 86,
                "maxBikeCadence": 112,
            },
        }
    )

    assert result["average_cadence"] == 86
    assert result["max_cadence"] == 112


def test_normalize_splits_returns_compact_laps() -> None:
    result = normalize_splits(
        {
            "activityId": 123,
            "lapDTOs": [
                {
                    "lapIndex": 1,
                    "distance": 1000.0,
                    "duration": 300.0,
                    "averageSpeed": 1000 / 300,
                    "averageHR": 145,
                    "startLatitude": 52.1,
                }
            ],
        }
    )

    assert result["lap_count"] == 1
    assert result["laps"][0]["average_pace_seconds_per_km"] == 300.0
    assert "startLatitude" not in result["laps"][0]


def test_normalize_splits_supports_cycling_cadence() -> None:
    result = normalize_splits(
        {
            "activityId": 789,
            "lapDTOs": [
                {
                    "lapIndex": 1,
                    "averageBikeCadence": 84,
                    "maxBikeCadence": 108,
                }
            ],
        }
    )

    assert result["laps"][0]["average_cadence"] == 84
    assert result["laps"][0]["max_cadence"] == 108


def test_normalize_recovery_sources_handle_available_and_missing_data() -> None:
    sleep = normalize_sleep(
        {
            "dailySleepDTO": {
                "calendarDate": "2026-08-20",
                "sleepTimeSeconds": 27000,
                "deepSleepSeconds": 5400,
            }
        }
    )
    hrv = normalize_hrv(None)
    body_battery = normalize_body_battery(
        [
            {
                "date": "2026-08-20",
                "charged": 55,
                "drained": 30,
                "bodyBatteryValuesArray": [[1, 35], [2, 82], [3, 60]],
            }
        ]
    )

    assert sleep["total_sleep_seconds"] == 27000
    assert hrv == {"available": False}
    assert body_battery["days"][0]["maximum"] == 82
    assert body_battery["days"][0]["latest"] == 60


def test_recovery_source_with_only_a_date_is_unavailable() -> None:
    sleep = normalize_sleep({"dailySleepDTO": {"calendarDate": "2026-08-20"}})
    body_battery = normalize_body_battery([{"date": "2026-08-20"}])

    assert sleep == {"available": False, "date": "2026-08-20"}
    assert body_battery == {
        "available": False,
        "days": [{"date": "2026-08-20"}],
    }


def test_normalize_hrv_reads_nested_baseline() -> None:
    result = normalize_hrv(
        {
            "hrvSummary": {
                "calendarDate": "2026-08-20",
                "baseline": {
                    "lowUpper": 30,
                    "balancedLow": 35,
                    "balancedUpper": 55,
                },
            }
        }
    )

    assert result["baseline_low_ms"] == 35
    assert result["baseline_high_ms"] == 55


def test_normalize_training_readiness_selects_latest_snapshot() -> None:
    result = normalize_training_readiness(
        [
            {"timestamp": "2026-08-20T06:00:00", "score": 60},
            {"timestamp": "2026-08-20T12:00:00", "score": 75},
        ]
    )

    assert result["score"] == 75


def test_normalize_training_readiness_supports_single_snapshot() -> None:
    result = normalize_training_readiness(
        {"calendarDate": "2026-08-20", "score": 72, "level": "GOOD"}
    )

    assert result["score"] == 72
    assert result["level"] == "GOOD"


def test_normalize_training_status_reads_nested_metrics() -> None:
    result = normalize_training_status(
        {
            "mostRecentTrainingStatus": {
                "latestTrainingStatusData": {
                    "123456789": {
                        "calendarDate": "2026-08-20",
                        "trainingStatus": 7,
                        "trainingStatusFeedbackPhrase": "PRODUCTIVE_3",
                    }
                }
            },
            "mostRecentTrainingLoadBalance": {
                "metricsTrainingLoadBalanceDTOMap": {
                    "2026-08-20": {
                        "acuteTrainingLoad": 410,
                        "chronicTrainingLoad": 380,
                    }
                }
            },
            "mostRecentVO2Max": {
                "generic": {
                    "calendarDate": "2026-08-20",
                    "vo2MaxPreciseValue": 52.4,
                },
                "heatAltitudeAcclimation": {
                    "heatAcclimationPercentage": 37,
                    "altitudeAcclimation": 1200,
                },
            },
            "heatAltitudeAcclimationDTO": None,
        }
    )

    assert result["available"] is True
    assert result["status"] == 7
    assert result["feedback"] == "PRODUCTIVE_3"
    assert result["acute_training_load"] == 410
    assert result["chronic_training_load"] == 380
    assert result["vo2_max"] == 52.4
    assert result["heat_acclimation"] == 37
    assert result["altitude_acclimation"] == 1200


def test_normalize_training_status_reads_acute_load_dto() -> None:
    result = normalize_training_status(
        {
            "mostRecentTrainingStatus": {
                "latestTrainingStatusData": {
                    "123456789": {
                        "calendarDate": "2026-08-20",
                        "trainingStatus": 7,
                        "acuteTrainingLoadDTO": {
                            "dailyTrainingLoadAcute": 410,
                            "dailyTrainingLoadChronic": 380,
                            "dailyAcuteChronicWorkloadRatio": 1.08,
                        },
                    }
                }
            }
        }
    )

    assert result["acute_training_load"] == 410
    assert result["chronic_training_load"] == 380
    assert result["acute_chronic_ratio"] == 1.08
