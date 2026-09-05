"""Exercise the real HTTP routing without running Java or altering real instances."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

import server


class OneClickHTTPTests(unittest.TestCase):
    def setUp(self):
        self.http = server.PanelHTTPServer(("127.0.0.1", 0), server.PanelHandler)
        self.port = self.http.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.patches = [patch.object(server, "ALLOWED_HOSTS", {f"127.0.0.1:{self.port}"}),
                        patch.object(server, "ALLOWED_ORIGINS", {self.origin}),
                        patch.object(server, "JOBS", {})]
        for item in self.patches:
            item.start()
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        for item in reversed(self.patches):
            item.stop()

    def request(self, path, payload=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("POST" if payload is not None else "GET", path,
                json.dumps(payload) if payload is not None else None,
                {"Origin": origin or self.origin, "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_start_returns_job_without_blocking_for_game_readiness(self):
        with patch.object(server, "require_profile", return_value={"id": "p"}), \
             patch.object(server, "start_one_click", return_value={"id": "j", "state": "queued"}) as start:
            status, data = self.request("/api/action", {"server_id": "p", "action": "start"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["job"]["id"], "j")
        start.assert_called_once_with({"id": "p"})

    def test_exact_job_lookup_survives_more_than_twenty_recent_jobs(self):
        server.JOBS.update({str(n): {"id": str(n), "state": "done"} for n in range(30)})
        status, data = self.request("/api/jobs?id=0")
        self.assertEqual(status, 200)
        self.assertEqual([j["id"] for j in data["data"]], ["0"])
        self.assertEqual(self.request("/api/jobs?id=missing")[1]["data"], [])

    def test_cancel_only_marks_the_requested_start_job(self):
        server.JOBS.update(j={"id":"j", "state":"running", "type":"start"},
                           other={"id":"other", "state":"running", "type":"backup"})
        status, data = self.request("/api/start/cancel", {"job_id":"j"})
        self.assertEqual(status, 200)
        self.assertTrue(server.JOBS["j"]["cancel_requested"])
        self.assertNotIn("cancel_requested", server.JOBS["other"])
        self.assertFalse(self.request("/api/start/cancel", {"job_id":"other"})[1]["ok"])

    def test_ftb_without_consent_does_not_fetch_or_create_task(self):
        with patch.object(server.bootstrapper, "ftb_manifest") as manifest:
            status, data = self.request("/api/import/ftb", {"pack_id":125})
        self.assertFalse(data["ok"])
        self.assertIn("EULA", data["error"])
        self.assertEqual(server.JOBS, {})
        manifest.assert_not_called()

    def test_folder_start_without_consent_does_not_register(self):
        with patch.object(server, "register_server") as register:
            status, data = self.request("/api/servers/import", {"path":"not-used", "start_after_import":True})
        self.assertFalse(data["ok"])
        register.assert_not_called()

    def test_foreign_origin_cannot_queue_a_start_or_download(self):
        with patch.object(server, "start_ftb_import") as start:
            status, data = self.request("/api/import/ftb", {"pack_id":125}, "https://untrusted.example")
        self.assertEqual(status, 403)
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
