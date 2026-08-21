from garmin_mcp.gateway import GarminGateway


class FakeClient:
    garmin_connect_activities = "/activitylist-service/activities/search/activities"

    def __init__(self) -> None:
        self.calls = []

    def connectapi(self, url, params):
        self.calls.append((url, params))
        return [
            {"activityId": 1, "activityType": {"typeKey": "running"}},
            {"activityId": 2, "activityType": {"typeKey": "running"}},
            {"activityId": 3, "activityType": {"typeKey": "running"}},
        ]


def test_list_activities_applies_limit_to_garmin_request(monkeypatch) -> None:
    gateway = GarminGateway("/unused")
    client = FakeClient()
    monkeypatch.setattr(gateway, "client", lambda: client)

    result = gateway.list_activities(
        "2026-08-01",
        "2026-08-07",
        activity_type="running",
        limit=2,
    )

    assert [activity["activity_id"] for activity in result] == [1, 2]
    assert client.calls == [
        (
            client.garmin_connect_activities,
            {
                "startDate": "2026-08-01",
                "endDate": "2026-08-07",
                "start": "0",
                "limit": "2",
                "sortOrder": "desc",
                "activityType": "running",
            },
        )
    ]
