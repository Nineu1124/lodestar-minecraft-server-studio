import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import server

TEST_TEMP_ROOT = Path(__file__).resolve().parent / ".tmp"
TEST_TEMP_ROOT.mkdir(exist_ok=True)


class RipplePanelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.base = Path(self.temporary.name)
        self.root = self.base / "TestServer"
        self.root.mkdir()
        (self.root / "server.properties").write_text(
            "server-port=25999\nmax-players=12\nlevel-name=world\nonline-mode=true\n",
            encoding="utf-8",
        )
        (self.root / "eula.txt").write_text("  eula=true\n", encoding="utf-8")
        (self.root / "server.jar").write_bytes(b"placeholder")
        self.original_settings = server.SETTINGS_PATH
        server.SETTINGS_PATH = self.base / "settings.json"

    def tearDown(self):
        server.SETTINGS_PATH = self.original_settings
        self.temporary.cleanup()

    def profile(self):
        return {
            "id": "testserver",
            "name": "Test Server",
            "path": str(self.root),
            "java": "java",
            "xms": "1G",
            "xmx": "2G",
            "launch_mode": "auto",
            "launch_target": "",
            "external_address": "",
            "auto_backup_keep": 3,
        }

    def make_fabric_mod(self, folder, name="example.jar"):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("fabric.mod.json", json.dumps({"id": "example", "name": "Example Mod", "version": "1.2.3"}))
        return path

    def test_eula_allows_leading_spaces(self):
        self.assertTrue(server.eula_accepted(self.root))

    def test_memory_validation(self):
        server.validate_memory("6G", "10G")
        with self.assertRaises(ValueError):
            server.validate_memory("12G", "10G")
        with self.assertRaises(ValueError):
            server.validate_memory("lots", "10G")

    def test_register_and_select_server(self):
        profile = server.register_server({"path": str(self.root), "name": "Pack", "xms": "2G", "xmx": "4G"})
        self.assertEqual(profile["name"], "Pack")
        self.assertEqual(server.get_profile()["id"], profile["id"])
        public = server.public_profile(profile)
        self.assertEqual(public["detected"]["mode"], "jar")

    def test_properties_allowlist(self):
        changes = server.validate_properties({"max-players": 8, "online-mode": False, "difficulty": "hard"})
        server.write_properties(self.root, changes)
        props = server.read_properties(self.root)
        self.assertEqual(props["max-players"], "8")
        self.assertEqual(props["online-mode"], "false")
        self.assertTrue((self.root / ".ripple-panel" / "server.properties.bak").exists())
        with self.assertRaises(ValueError):
            server.validate_properties({"server-ip": "0.0.0.0"})

    def test_mod_lifecycle_is_recoverable(self):
        self.make_fabric_mod(self.root / "mods")
        profile = self.profile()
        listed = server.list_mods(profile)
        self.assertEqual(listed[0]["mod_id"], "example")
        self.assertEqual(listed[0]["state"], "enabled")
        server.change_mod_state(profile, "disable", "enabled", "example.jar")
        server.change_mod_state(profile, "remove", "disabled", "example.jar")
        self.assertEqual(server.list_mods(profile)[0]["state"], "trash")
        server.change_mod_state(profile, "restore", "trash", "example.jar")
        self.assertTrue((self.root / "disabled_mods" / "example.jar").exists())

    def test_offline_backup_contains_world_and_config(self):
        world = self.root / "world" / "region"
        world.mkdir(parents=True)
        (world / "r.0.0.mca").write_bytes(b"world-data")
        profile = self.profile()
        job_id = "backup-test"
        server.JOBS[job_id] = {"id": job_id, "server_id": profile["id"], "state": "queued"}
        server.create_backup_worker(profile, job_id)
        backups = list((self.root / "panel-backups").glob("*.zip"))
        self.assertEqual(len(backups), 1)
        with zipfile.ZipFile(backups[0]) as archive:
            self.assertIn("world/region/r.0.0.mca", archive.namelist())
            self.assertIn("server.properties", archive.namelist())
        self.assertEqual(server.JOBS[job_id]["state"], "done")


if __name__ == "__main__":
    unittest.main()
