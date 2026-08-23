import json
import shutil
import sys
import unittest
import uuid
import zipfile
from pathlib import Path

import server

class LodestarPanelTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).resolve().parent / ".test-work" / uuid.uuid4().hex
        self.base.mkdir(parents=True)
        self.root = self.base / "TestServer"
        self.root.mkdir()
        (self.root / "server.properties").write_text(
            "server-port=25999\nmax-players=12\nlevel-name=world\nonline-mode=true\n",
            encoding="utf-8",
        )
        (self.root / "eula.txt").write_text("  eula=true\n", encoding="utf-8")
        (self.root / "server.jar").write_bytes(b"placeholder")
        self.original_settings = server.SETTINGS_PATH
        self.original_scheduler_state = server.SCHEDULER_STATE_PATH
        server.SETTINGS_PATH = self.base / "settings.json"
        server.SCHEDULER_STATE_PATH = self.base / "scheduler-state.json"

    def tearDown(self):
        server.SETTINGS_PATH = self.original_settings
        server.SCHEDULER_STATE_PATH = self.original_scheduler_state
        shutil.rmtree(self.base, ignore_errors=True)

    def profile(self):
        return {
            "id": "testserver",
            "name": "Test Server",
            "path": str(self.root),
            "java": sys.executable,
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

    def make_plugin(self, folder, name="example-plugin.jar"):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("plugin.yml", "name: ExamplePlugin\nmain: dev.example.Plugin\nversion: 2.4.0\n")
        return path

    def test_eula_allows_leading_spaces(self):
        self.assertTrue(server.eula_accepted(self.root))

    def test_write_access_probe_is_removed(self):
        server.ensure_server_write_access(self.root)
        self.assertEqual(list(self.root.glob(".ripple-write-test-*.tmp")), [])

    def test_metric_history_is_bounded(self):
        profile = self.profile()
        server.METRIC_HISTORY[profile["id"]] = []
        for index in range(725):
            server.record_metric(profile, {"running": True, "ready": True,
                                           "process": {"cpu_percent": index, "memory_mb": 512 + index},
                                           "players": {"online": index % 4}})
            server.METRIC_HISTORY[profile["id"]][-1]["at"] = f"2026-08-23T10:{index:04d}:00+08:00"
        self.assertLessEqual(len(server.metric_history(profile)), 720)

    def test_memory_validation(self):
        server.validate_memory("6G", "10G")
        with self.assertRaises(ValueError):
            server.validate_memory("12G", "10G")
        with self.assertRaises(ValueError):
            server.validate_memory("lots", "10G")

    def test_java_version_requirements(self):
        self.assertEqual(server.parsed_java_major('openjdk version "17.0.12"'), 17)
        self.assertEqual(server.parsed_java_major('java version "1.8.0_401"'), 8)
        self.assertEqual(server.required_java_major("1.16.5"), 8)
        self.assertEqual(server.required_java_major("1.20.1"), 17)
        self.assertEqual(server.required_java_major("1.21.1"), 21)

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

    def test_properties_can_be_created_before_first_start(self):
        fresh = self.base / "FreshServer"
        fresh.mkdir()
        server.write_properties(fresh, {"server-port": "25570", "online-mode": "false"})
        self.assertEqual(server.read_properties(fresh)["server-port"], "25570")
        self.assertFalse((fresh / ".ripple-panel" / "server.properties.bak").exists())

    def test_archive_inspection_finds_nested_server_root(self):
        package = self.base / "pack.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("bundle/fuwuduan/eula.txt", "eula=false\n")
            archive.writestr("bundle/fuwuduan/luminara-1.20.1.jar", b"jar")
            archive.writestr("bundle/fuwuduan/.run.bat", "java -Xmx8G -jar luminara-1.20.1.jar -nogui\n")
            archive.writestr(
                "bundle/fuwuduan/libraries/net/minecraftforge/forge/1.20.1-47.4.16/win_args.txt",
                "args",
            )
            archive.writestr("bundle/readme.txt", "read me")
        result = server.inspect_server_archive(str(package))
        self.assertEqual(result["root"], "bundle/fuwuduan")
        self.assertEqual(result["loader"], "forge")
        self.assertEqual(result["version"], "1.20.1")
        self.assertEqual(result["launch_candidates"][0]["mode"], "forge_args")
        self.assertEqual(result["recommended_launch"]["mode"], "jar")
        self.assertEqual(result["recommended_launch"]["target"], "luminara-1.20.1.jar")

    def test_archive_import_extracts_configures_and_registers(self):
        package = self.base / "ready.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("server.jar", b"jar")
            archive.writestr("eula.txt", "eula=false\n")
        destination = self.base / "Imported"
        request = server.validate_import_request({
            "archive_path": str(package),
            "destination": str(destination),
            "name": "Imported Pack",
            "xms": "2G",
            "xmx": "4G",
            "launch_mode": "jar",
            "launch_target": "server.jar",
            "accept_eula": True,
            "properties": {"server-port": 25571, "max-players": 9, "online-mode": False},
        })
        job_id = "import-test"
        server.JOBS[job_id] = {"id": job_id, "type": "archive_import", "state": "queued"}
        server.extract_archive_worker(request, job_id)
        self.assertEqual(server.JOBS[job_id]["state"], "done")
        profile = server.get_profile(server.JOBS[job_id]["server_id"])
        self.assertEqual(profile["name"], "Imported Pack")
        self.assertEqual(profile["launch_target"], "server.jar")
        self.assertTrue(server.eula_accepted(Path(profile["path"])))
        self.assertEqual(server.read_properties(Path(profile["path"]))["server-port"], "25571")

    def test_archive_rejects_path_traversal(self):
        package = self.base / "unsafe.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("../outside/server.jar", b"jar")
        with self.assertRaises(ValueError):
            server.inspect_server_archive(str(package))

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

    def test_plugin_lifecycle_and_metadata(self):
        self.make_plugin(self.root / "plugins")
        profile = self.profile()
        listed = server.list_mods(profile, "plugins")
        self.assertEqual(listed[0]["display_name"], "ExamplePlugin")
        self.assertEqual(listed[0]["version"], "2.4.0")
        server.change_mod_state(profile, "disable", "enabled", "example-plugin.jar", "plugins")
        self.assertTrue((self.root / "disabled_plugins" / "example-plugin.jar").exists())

    def test_file_manager_lists_edits_and_trashes_safely(self):
        profile = self.profile()
        listing = server.list_server_files(profile)
        names = {item["name"] for item in listing["entries"]}
        self.assertIn("server.properties", names)
        server.save_server_text_file(profile, "server.properties", "server-port=25572\n")
        self.assertEqual(server.read_server_text_file(profile, "server.properties")["content"], "server-port=25572\n")
        self.assertTrue(any((self.root / ".ripple-panel" / "file-history").rglob("server.properties")))
        server.file_manager_action(profile, "mkdir", "", "configs")
        (self.root / "configs" / "sample.yml").write_text("enabled: true\n", encoding="utf-8")
        server.file_manager_action(profile, "rename", "configs/sample.yml", "renamed.yml")
        server.file_manager_action(profile, "trash", "configs/renamed.yml")
        self.assertFalse((self.root / "configs" / "renamed.yml").exists())
        self.assertTrue(any((self.root / ".ripple-panel" / "file-trash").rglob("renamed.yml")))
        with self.assertRaises(ValueError):
            server.server_file_path(profile, "../outside.txt", must_exist=False)
        with self.assertRaises(ValueError):
            server.server_file_path(profile, ".ripple-panel/settings.json", must_exist=False)

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

    def test_backup_restore_keeps_rollback_copy(self):
        world = self.root / "world"
        world.mkdir()
        (world / "level.dat").write_text("backup-version", encoding="utf-8")
        profile = self.profile()
        backup_dir = server.backup_folder(profile)
        backup_dir.mkdir()
        backup = backup_dir / "backup-restore-test.zip"
        with zipfile.ZipFile(backup, "w") as archive:
            archive.write(world / "level.dat", "world/level.dat")
            archive.writestr("server.properties", "server-port=25573\nlevel-name=world\n")
        (world / "level.dat").write_text("current-version", encoding="utf-8")
        job_id = "restore-test"
        server.JOBS[job_id] = {"id": job_id, "server_id": profile["id"], "type": "restore", "state": "queued"}
        server.restore_backup_worker(profile, backup.name, job_id)
        self.assertEqual(server.JOBS[job_id]["state"], "done")
        self.assertEqual((world / "level.dat").read_text(encoding="utf-8"), "backup-version")
        rollback_files = list((self.root / ".ripple-panel" / "restore-history").rglob("level.dat"))
        self.assertEqual(len(rollback_files), 1)
        self.assertEqual(rollback_files[0].read_text(encoding="utf-8"), "current-version")

    def test_automation_configuration_is_persisted(self):
        profile = server.register_server({"path": str(self.root), "name": "Pack", "xms": "2G", "xmx": "4G"})
        result = server.configure_automation({
            "server_id": profile["id"],
            "automation": {
                "backup_schedule_enabled": True,
                "backup_interval_hours": 12,
                "restart_schedule_enabled": True,
                "restart_time": "04:30",
            },
        })
        self.assertTrue(result["backup_schedule_enabled"])
        self.assertEqual(result["backup_interval_hours"], 12)
        self.assertTrue(result["restart_schedule_enabled"])
        self.assertEqual(result["restart_time"], "04:30")
        self.assertIsNone(result["last_backup"])
        self.assertIsNotNone(result["next_backup"])

    def test_diagnostics_find_log_signals_and_crash_reports(self):
        (self.root / "logs").mkdir()
        (self.root / "logs" / "latest.log").write_text(
            "[Server thread/INFO] Ready\n[Server thread/ERROR] Example failure\nCaused by: missing registry\n",
            encoding="utf-8",
        )
        (self.root / "crash-reports").mkdir()
        (self.root / "crash-reports" / "crash-test.txt").write_text("test crash", encoding="utf-8")
        data = server.diagnostic_data(self.profile())
        self.assertEqual(len(data["crash_reports"]), 1)
        self.assertEqual(len(data["error_lines"]), 2)
        self.assertEqual(server.crash_report_path(self.profile(), "crash-test.txt").name, "crash-test.txt")
        with self.assertRaises(ValueError):
            server.crash_report_path(self.profile(), "../secret.txt")

    def test_diagnostics_identify_client_only_mod_on_server(self):
        (self.root / "logs").mkdir()
        (self.root / "logs" / "latest.log").write_text(
            "Loading errors encountered:\n"
            "rs工作台拓展 (rs_crafting_stations) has failed to load correctly\n"
            "java.lang.RuntimeException: Attempted to load class "
            "net/minecraft/client/gui/screens/Screen for invalid dist DEDICATED_SERVER\n",
            encoding="utf-8",
        )
        diagnosis = server.diagnose_startup_failure(self.root)
        self.assertEqual(diagnosis["code"], "client_mod_on_server")
        self.assertIn("rs_crafting_stations", diagnosis["detail"])
        self.assertIn("rs工作台拓展", diagnosis["detail"])

    def test_read_tail_falls_back_to_gb18030(self):
        path = self.root / "legacy.log"
        path.write_bytes("服务端启动失败".encode("gb18030"))
        self.assertEqual(server.read_tail(path), "服务端启动失败")


if __name__ == "__main__":
    unittest.main()
