import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import server


PASSCODE = "test-passcode"
HEADERS = {"X-Demo-Passcode": PASSCODE}


def parse_sse(body: str) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {}
    for frame in body.strip().split("\n\n"):
        event = "message"
        data = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if data is not None:
            events.setdefault(event, []).append(data)
    return events


class StreamingEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        server._sessions.clear()
        self.client = TestClient(server.app)
        self.passcode_patch = patch.object(server, "_configured_passcode", return_value=PASSCODE)
        self.passcode_patch.start()

    def tearDown(self) -> None:
        self.passcode_patch.stop()
        server._sessions.clear()

    def describe(self, latitude: float = 40.7812, longitude: float = -73.9665):
        return self.client.post(
            "/api/describe/stream",
            headers=HEADERS,
            json={"latitude": latitude, "longitude": longitude},
        )

    def test_post_describe_stream_requires_authentication(self) -> None:
        response = self.client.post(
            "/api/describe/stream",
            json={"latitude": 40.7812, "longitude": -73.9665},
        )
        self.assertEqual(response.status_code, 401)

    def test_post_streams_report_without_coordinates_in_url(self) -> None:
        response = self.describe()
        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        self.assertIn("report", events)
        report = events["report"][0]
        self.assertEqual(report["report"]["latitude"], 40.7812)
        self.assertTrue(report["session_id"])

    def test_post_streams_follow_up(self) -> None:
        first = self.describe()
        session_id = parse_sse(first.text)["report"][0]["session_id"]
        response = self.client.post(
            "/api/ask/stream",
            headers=HEADERS,
            json={"session_id": session_id, "question": "What is nearby?"},
        )
        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        self.assertIn("done", events)
        self.assertIn("canned follow-up", "".join(item["text"] for item in events["delta"]))

    def test_two_location_sessions_remain_independent(self) -> None:
        first = parse_sse(self.describe(40.7812, -73.9665).text)["report"][0]["session_id"]
        second = parse_sse(self.describe(48.8584, 2.2945).text)["report"][0]["session_id"]
        self.assertNotEqual(first, second)

        for session_id in (first, second):
            response = self.client.post(
                "/api/ask/stream",
                headers=HEADERS,
                json={"session_id": session_id, "question": "What is nearby?"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("event: done", response.text)

    def test_unknown_session_returns_conflict(self) -> None:
        response = self.client.post(
            "/api/ask/stream",
            headers=HEADERS,
            json={"session_id": "not-a-session", "question": "What is nearby?"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("expired", response.json()["detail"])

    def test_get_stream_transport_is_not_available(self) -> None:
        response = self.client.get(
            "/api/describe/stream",
            headers=HEADERS,
            params={"latitude": 40.7812, "longitude": -73.9665},
        )
        self.assertEqual(response.status_code, 405)


if __name__ == "__main__":
    unittest.main()
