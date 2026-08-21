from garmin_mcp.normalizers import (
    normalize_activity,
    normalize_body_battery,
    normalize_hrv,
    normalize_sleep,
    normalize_splits,
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
            "startLatitude": 52.1,
            "ownerId": 99,
            "hrTimeInZone_2": 600.0,
        }
    )

    assert result["activity_id"] == 123
    assert result["average_pace_seconds_per_km"] == 250.0
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
            },
        }
    )

    assert result["sport"] == "running"
    assert result["average_pace_seconds_per_km"] == 400.0
    assert result["average_cadence"] == 170


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
