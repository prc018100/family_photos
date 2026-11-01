import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.icloud_library import (
    ImmichLibraryError,
    ensure_icloud_library,
    reauth_message,
    session_needs_reauth,
)

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LIB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PATH = "/mnt/media/icloud"


class Fake:
    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        status, payload = self.routes.pop(0)
        if payload is None:
            raw = b""
        elif isinstance(payload, bytes):
            raw = payload
        else:
            raw = json.dumps(payload).encode()
        return status, raw


class LibraryTests(unittest.TestCase):
    def test_creates_library_then_scans(self):
        fake = Fake(
            [
                (200, {"id": OWNER, "isAdmin": True}),
                (200, []),
                (201, {"id": LIB, "importPaths": [PATH]}),
                (204, None),
            ]
        )
        result = ensure_icloud_library("http://immich.test:2283", "secret-key", transport=fake)
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["id"], LIB)
        self.assertEqual(fake.calls[2][0], "POST")
        self.assertEqual(json.loads(json.dumps(fake.calls[2][2]))["importPaths"], [PATH])
        self.assertEqual(fake.calls[3][0], "POST")
        self.assertTrue(fake.calls[3][1].endswith("/scan"))
        self.assertNotIn("secret-key", json.dumps(fake.calls))

    def test_existing_path_is_only_scanned(self):
        fake = Fake(
            [
                (200, {"id": OWNER, "isAdmin": True}),
                (200, [{"id": LIB, "name": "iCloud", "importPaths": [PATH]}]),
                (204, None),
            ]
        )
        result = ensure_icloud_library("http://immich.test", "secret-key", transport=fake)
        self.assertEqual(result["action"], "scanned")
        self.assertEqual([call[0] for call in fake.calls], ["GET", "GET", "POST"])

    def test_named_library_gains_the_icloud_path(self):
        fake = Fake(
            [
                (200, {"id": OWNER, "isAdmin": True}),
                (200, [{"id": LIB, "name": "iCloud", "importPaths": ["/mnt/media/other"]}]),
                (200, {"id": LIB, "importPaths": ["/mnt/media/other", PATH]}),
                (204, None),
            ]
        )
        result = ensure_icloud_library("http://immich.test", "secret-key", transport=fake)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(fake.calls[2][2]["importPaths"], ["/mnt/media/other", PATH])

    def test_missing_permission_names_the_key_requirements(self):
        fake = Fake([(403, {"message": "forbidden"})])
        with self.assertRaises(ImmichLibraryError) as caught:
            ensure_icloud_library("http://immich.test", "secret-key", transport=fake)
        self.assertIn("library.create", str(caught.exception))
        self.assertNotIn("secret-key", str(caught.exception))

    def test_non_admin_is_rejected(self):
        fake = Fake([(200, {"id": OWNER, "isAdmin": False})])
        with self.assertRaises(ImmichLibraryError):
            ensure_icloud_library("http://immich.test", "secret-key", transport=fake)

    def test_reauth_message_is_the_one_command(self):
        text = reauth_message("ada@icloud.com")
        self.assertIn("./import-icloud.sh ada@icloud.com", text)
        self.assertIn("session expired", text.lower())

    def test_sync_libraries_are_idempotent_and_enable_nightly_scan(self):
        from scripts.icloud_library import ensure_sync_libraries

        pictures = "11111111-1111-4111-8111-111111111111"
        video = "22222222-2222-4222-8222-222222222222"
        config = {"library": {"scan": {"enabled": False}, "watch": {"enabled": False}}, "machineLearning": {"urls": ["http://immich-machine-learning:3003"]}}
        libraries = []

        def send(method, path, body):
            if path == "/api/users/me":
                return 200, json.dumps({"id": OWNER, "isAdmin": True}).encode()
            if path == "/api/libraries" and method == "GET":
                return 200, json.dumps(libraries).encode()
            if path == "/api/libraries" and method == "POST":
                lib_id = pictures if body["importPaths"] == ["/mnt/media/sync-pictures"] else video
                created = {"id": lib_id, "name": body["name"], "importPaths": body["importPaths"]}
                libraries.append(created)
                return 201, json.dumps(created).encode()
            if path.endswith("/scan"):
                return 204, b""
            if path == "/api/system-config" and method == "GET":
                return 200, json.dumps(config).encode()
            if path == "/api/system-config" and method == "PUT":
                self.assertTrue(body["library"]["scan"]["enabled"])
                self.assertEqual(body["library"]["watch"]["enabled"], False)
                self.assertEqual(body["machineLearning"]["urls"], ["http://immich-machine-learning:3003"])
                config.update(body)
                return 200, json.dumps(body).encode()
            raise AssertionError((method, path))

        results, schedule = ensure_sync_libraries("http://immich.test", "secret-key", transport=send)
        self.assertEqual(schedule, "enabled")
        self.assertEqual([item["importPath"] for item in results], ["/mnt/media/sync-pictures", "/mnt/media/sync-video"])
        self.assertEqual([item["action"] for item in results], ["created", "created"])

        again, schedule_again = ensure_sync_libraries("http://immich.test", "secret-key", transport=send)
        self.assertEqual(schedule_again, "already")
        self.assertEqual([item["action"] for item in again], ["scanned", "scanned"])

    def test_custom_scan_cron_is_left_alone(self):
        from scripts.icloud_library import ensure_nightly_library_scan

        calls = []

        def send(method, path, body):
            calls.append(method)
            self.assertEqual(path, "/api/system-config")
            return 200, json.dumps({"library": {"scan": {"enabled": True, "cronExpression": "15 3 * * *"}}}).encode()

        self.assertEqual(ensure_nightly_library_scan(send), "already")
        self.assertEqual(calls, ["GET"])

    def test_missing_config_permission_does_not_raise(self):
        from scripts.icloud_library import ensure_nightly_library_scan

        def send(method, path, body):
            return 403, b'{"message":"forbidden"}'

        self.assertEqual(ensure_nightly_library_scan(send), "forbidden")

    def test_session_classifier(self):
        self.assertFalse(session_needs_reauth("Authentication completed successfully", 0))
        self.assertTrue(session_needs_reauth("Invalid email/password combination.", 1))
        self.assertTrue(session_needs_reauth("EOFError: EOF when reading a line", 1))
        self.assertFalse(session_needs_reauth("Connection reset by peer", 1))


if __name__ == "__main__":
    unittest.main()
