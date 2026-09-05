"""Regression tests: never operate a real Java process or a user's server files."""
import hashlib
import io
import json
import shutil
import threading
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import server


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).resolve().parent / ".test-work" / uuid.uuid4().hex
        self.root = self.base / "server"
        self.root.mkdir(parents=True)
        (self.root / "world").mkdir()
        (self.root / "world" / "level.dat").write_bytes(b"original-world")
        (self.root / "server.properties").write_text("server-port=25565\nlevel-name=world\n", encoding="utf-8")
        (self.root / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        self.profile = {"id": "fixture", "path": str(self.root), "xms": "1G", "xmx": "2G", "auto_backup_keep": 3}
        self.patches = [patch.object(server, "DATA_ROOT", self.base / "panel"),
                        patch.object(server, "SETTINGS_PATH", self.base / "panel" / "settings.json"),
                        patch.object(server, "SCHEDULER_STATE_PATH", self.base / "scheduler.json"),
                        patch.object(server, "minecraft_ping", return_value=None),
                        patch.object(server, "find_listening_pid", return_value=None)]
        for name in ("JOBS", "RUNTIMES", "ACTION_LOCKS", "OPERATIONS", "LAST_EXITS"):
            self.patches.append(patch.object(server, name, {}))
        for item in self.patches:
            item.start()

    def tearDown(self):
        for runtime in server.RUNTIMES.values():
            runtime["log"].close()
        for item in reversed(self.patches):
            item.stop()
        self.assertEqual(self.base.resolve().parent.name, ".test-work")
        shutil.rmtree(self.base)

    def job(self, kind="backup", state="queued"):
        job_id = uuid.uuid4().hex
        server.JOBS[job_id] = {"id": job_id, "server_id": self.profile["id"], "type": kind, "state": state}
        return job_id

    def make_backup(self):
        job_id = self.job()
        server.create_backup_worker(self.profile, job_id)
        self.assertEqual(server.JOBS[job_id]["state"], "done", server.JOBS[job_id])
        return next(server.backup_folder(self.profile).glob("*.zip"))

    def test_unknown_explicit_id_is_not_first_instance(self):
        with patch.object(server, "read_settings", return_value={"servers": [self.profile], "active_server_id": "fixture"}):
            with self.assertRaises(FileNotFoundError):
                server.get_profile("deleted")
            self.assertEqual(server.get_profile()["id"], "fixture")

    def test_writes_require_explicit_id(self):
        for value in (None, "", " ", 12):
            with self.assertRaises(ValueError):
                server.require_profile(value)

    def test_foreign_port_is_conflict_not_running(self):
        with patch.object(server, "find_listening_pid", return_value=4242), patch.object(server, "minecraft_ping", return_value={"players": {"online": 8}}):
            result = server.server_is_running(self.profile)
        self.assertFalse(result["running"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["players"]["online"], 0)
        self.assertIsNone(result["process"])
        self.assertEqual(result["port_conflict"]["pid"], 4242)

    def test_cannot_start_on_foreign_port(self):
        with patch.object(server, "find_listening_pid", return_value=4242), patch.object(server.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "端口"):
                server.start_server(self.profile)
        popen.assert_not_called()

    def test_cannot_force_stop_foreign_port(self):
        with patch.object(server, "find_listening_pid", return_value=4242), patch.object(server.subprocess, "run") as command:
            server.force_stop_server(self.profile)
        command.assert_not_called()

    def test_cannot_send_command_to_foreign_port(self):
        with patch.object(server, "find_listening_pid", return_value=4242), patch.object(server.subprocess, "run") as command:
            with self.assertRaisesRegex(RuntimeError, "托管"):
                server.send_command(self.profile, "stop")
        command.assert_not_called()

    def test_managed_listener_is_ready(self):
        runtime = {"process": Mock(pid=4242)}
        status_packet = {"version": {"name": "1.20.1", "protocol": 763}, "players": {"online": 0, "max": 20}}
        with patch.object(server, "runtime_for", return_value=runtime), patch.object(server, "find_listening_pid", return_value=4242), patch.object(server, "minecraft_ping", return_value=status_packet), patch.object(server, "process_info", return_value={"pid": 4242}):
            result = server.server_is_running(self.profile)
        self.assertTrue(result["ready"])
        self.assertIsNone(result["port_conflict"])

    def test_persisted_identity_rejects_reused_pid_and_changed_root(self):
        with patch.object(server, "process_identity", return_value="created-token"):
            server.remember_process(self.profile, 4242)
            self.assertEqual(server.owned_process_pid(self.profile, None), 4242)
            self.assertIsNone(server.owned_process_pid({**self.profile, "path": str(self.base)}, None))
        with patch.object(server, "process_identity", return_value="reused-pid-token"):
            self.assertIsNone(server.owned_process_pid(self.profile, None))

    def test_pending_restore_blocks_start_and_restart(self):
        self.job("restore", "running")
        with patch.object(server.subprocess, "Popen") as popen:
            for action in (server.start_server, server.restart_server):
                with self.assertRaisesRegex(RuntimeError, "备份或恢复"):
                    action(self.profile)
        popen.assert_not_called()

    def test_concurrent_restore_blocks_start(self):
        backup = self.make_backup()
        job_id = self.job("restore")
        reached, release = threading.Event(), threading.Event()
        validate = server.validate_backup_archive

        def paused(archive):
            reached.set()
            if not release.wait(5):
                raise TimeoutError("test barrier timed out")
            return validate(archive)

        with patch.object(server, "validate_backup_archive", side_effect=paused), patch.object(server.subprocess, "Popen") as popen:
            worker = threading.Thread(target=server.restore_backup_worker, args=(self.profile, backup.name, job_id))
            worker.start()
            try:
                self.assertTrue(reached.wait(5))
                with self.assertRaisesRegex(RuntimeError, "操作正在进行"):
                    server.start_server(self.profile)
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
        popen.assert_not_called()
        self.assertEqual(server.JOBS[job_id]["state"], "done")
        self.assertFalse(server.OPERATIONS)

    def test_backup_includes_dimensions_and_additional_worlds(self):
        for folder in ("world_nether", "world_the_end", "adventure"):
            (self.root / folder).mkdir()
            (self.root / folder / "level.dat").write_bytes(folder.encode())
        backup = self.make_backup()
        with zipfile.ZipFile(backup) as archive:
            self.assertEqual(len(server.validate_backup_archive(archive)), 5)
            for folder in ("world", "world_nether", "world_the_end", "adventure"):
                self.assertIn(folder + "/level.dat", archive.namelist())
        self.assertTrue(server.backup_summary(backup)["verified"])

    def test_missing_world_does_not_create_config_only_backup(self):
        (self.root / "server.properties").write_text("level-name=missing\n", encoding="utf-8")
        job_id = self.job()
        server.create_backup_worker(self.profile, job_id)
        self.assertEqual(server.JOBS[job_id]["state"], "error")
        self.assertEqual(server.list_backups(self.profile), [])

    def test_interrupted_archive_is_never_published(self):
        original = zipfile.ZipFile.write
        calls = 0

        def interrupted(archive, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected I/O failure")
            return original(archive, *args, **kwargs)

        job_id = self.job()
        with patch.object(zipfile.ZipFile, "write", new=interrupted):
            server.create_backup_worker(self.profile, job_id)
        self.assertEqual(server.JOBS[job_id]["state"], "error")
        self.assertEqual(server.list_backups(self.profile), [])
        self.assertEqual(list(server.backup_folder(self.profile).glob("*.partial")), [])

    def test_legacy_zip_is_retained_but_not_restored(self):
        folder = server.backup_folder(self.profile)
        folder.mkdir()
        legacy = folder / "legacy.zip"
        with zipfile.ZipFile(legacy, "w") as archive:
            archive.writestr("world/level.dat", b"partial-world")
        self.assertFalse(server.list_backups(self.profile)[0]["verified"])
        with self.assertRaisesRegex(ValueError, "旧备份"):
            server.start_backup_restore(self.profile, legacy.name)
        self.assertTrue(legacy.exists())
        self.assertEqual((self.root / "world" / "level.dat").read_bytes(), b"original-world")

    def test_checksum_failure_never_moves_current_world(self):
        backup = self.make_backup()
        with zipfile.ZipFile(backup) as archive:
            manifest = json.loads(archive.read(server.BACKUP_MANIFEST))
            entries = {name: archive.read(name) for name in archive.namelist()}
        manifest["files"][0]["sha256"] = "0" * 64
        entries[server.BACKUP_MANIFEST] = json.dumps(manifest).encode()
        with zipfile.ZipFile(backup, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        job_id = self.job("restore")
        server.restore_backup_worker(self.profile, backup.name, job_id)
        self.assertEqual(server.JOBS[job_id]["state"], "error")
        self.assertEqual((self.root / "world" / "level.dat").read_bytes(), b"original-world")
        self.assertFalse((self.root / ".ripple-panel" / "restore-history").exists())

    def test_online_backup_requires_save_ack_and_restores_saving_on_timeout(self):
        job_id = self.job()
        with patch.object(server, "server_is_running", return_value={"running": True}), patch.object(server, "send_command") as send, patch.object(server, "wait_for_save_ack", side_effect=[TimeoutError("no acknowledgement"), None]):
            server.create_backup_worker(self.profile, job_id)
        self.assertEqual(server.JOBS[job_id]["state"], "error")
        self.assertEqual([call.args[1] for call in send.call_args_list], ["save-off", "save-all flush", "save-on"])
        self.assertEqual(server.list_backups(self.profile), [])

    def test_save_on_failure_is_visible(self):
        job_id = self.job()
        server.JOBS[job_id]["scheduled"] = True
        with patch.object(server, "server_is_running", return_value={"running": True}), patch.object(server, "send_command"), patch.object(server, "wait_for_save_ack", side_effect=[None, TimeoutError("no save-on acknowledgement")]):
            server.create_backup_worker(self.profile, job_id)
        self.assertEqual(server.JOBS[job_id]["state"], "error")
        self.assertIn("save-on", server.JOBS[job_id]["message"])
        self.assertNotIn("backup_success:fixture", server.read_scheduler_state())

    def test_backup_stays_running_until_save_on_acknowledged(self):
        job_id = self.job()
        def acknowledge(*args, **kwargs):
            self.assertEqual(server.JOBS[job_id]["state"], "running")
        with patch.object(server, "server_is_running", return_value={"running": True}), patch.object(server, "send_command"), patch.object(server, "wait_for_save_ack", side_effect=acknowledge):
            server.create_backup_worker(self.profile, job_id)
        self.assertEqual(server.JOBS[job_id]["state"], "done")

    def test_chinese_download_has_encoded_header(self):
        path = self.root / "中文配置.txt"
        path.write_bytes(b"fixture")
        handler = server.PanelHandler.__new__(server.PanelHandler)
        handler.request_version = "HTTP/1.1"
        handler.requestline = "GET /fixture HTTP/1.1"
        handler.command = "GET"
        handler.wfile = io.BytesIO()
        handler._send_file(path, "text/plain", path.name)
        self.assertIn(b"filename*=UTF-8''%E4%B8%AD", handler.wfile.getvalue())
        self.assertTrue(handler.wfile.getvalue().endswith(b"fixture"))

    def test_jvm_native_argfile_preserves_multi_flag_lines(self):
        path = self.root / "user_jvm_args.txt"
        path.write_text('-XX:+UseG1GC -XX:+ParallelRefProcEnabled\n-Dname="hello world"\n', encoding="utf-8")
        with patch.object(server, "java_version_text", return_value='openjdk version "17.0.15"'):
            args = server.collect_jvm_args(self.root, self.profile)
        self.assertEqual(args[0], "@" + str(path.resolve()))
        self.assertEqual(args[-2:], ["-Xms1G", "-Xmx2G"])

    def test_java8_multi_flags_and_quoted_windows_paths(self):
        path = self.root / "user_jvm_args.txt"
        path.write_text('-XX:+UseG1GC -XX:+ParallelRefProcEnabled\n-Dpath="C:\\Program Files\\example"\n# comment\n', encoding="utf-8")
        with patch.object(server, "java_version_text", return_value='java version "1.8.0_401"'):
            args = server.collect_jvm_args(self.root, self.profile)
        self.assertEqual(args, ['-XX:+UseG1GC', '-XX:+ParallelRefProcEnabled', '-Dpath=C:\\Program Files\\example', '-Xms1G', '-Xmx2G'])

    def test_background_sampling_does_not_require_browser_requests(self):
        with patch.object(server, "read_settings", return_value={"servers": [self.profile]}), patch.object(server, "server_is_running", return_value={"running": False}), patch.object(server, "record_metric") as record:
            server.sample_metrics()
        record.assert_called_once()

    def test_low_space_backup_fails_before_publication(self):
        job_id = self.job()
        with patch.object(server, "server_is_running", return_value={"running": False}), patch.object(server.shutil, "disk_usage", return_value=Mock(free=0)):
            server.create_backup_worker(self.profile, job_id)
        self.assertEqual(server.JOBS[job_id]["state"], "error")
        self.assertIn("空间不足", server.JOBS[job_id]["message"])
        self.assertEqual(server.list_backups(self.profile), [])

    def test_stale_editor_revision_cannot_overwrite_new_content(self):
        opened = server.read_server_text_file(self.profile, "server.properties")
        (self.root / "server.properties").write_text("motd=changed-elsewhere\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            server.save_server_text_file(self.profile, "server.properties", "motd=stale\n", opened["revision"])
        self.assertIn("changed-elsewhere", (self.root / "server.properties").read_text())

    def test_windows_script_line_endings_are_preserved(self):
        path = self.root / "start.bat"
        path.write_bytes(b"@echo off\r\necho old\r\n")
        opened = server.read_server_text_file(self.profile, "start.bat")
        server.save_server_text_file(self.profile, "start.bat", "@echo off\necho updated\n", opened["revision"])
        self.assertEqual(path.read_bytes(), b"@echo off\r\necho updated\r\n")

    def test_utf8_bom_is_preserved(self):
        path = self.root / "example.txt"
        path.write_bytes(b"\xef\xbb\xbfold")
        opened = server.read_server_text_file(self.profile, "example.txt")
        self.assertEqual(opened["content"], "old")
        server.save_server_text_file(self.profile, "example.txt", "updated", opened["revision"])
        self.assertEqual(path.read_bytes(), b"\xef\xbb\xbfupdated")

    def test_current_editor_revision_saves_and_retains_history(self):
        opened = server.read_server_text_file(self.profile, "server.properties")
        server.save_server_text_file(self.profile, "server.properties", "motd=updated\n", opened["revision"])
        updated = server.read_server_text_file(self.profile, "server.properties")
        self.assertEqual(updated["content"], "motd=updated\n")
        self.assertEqual(updated["revision"], hashlib.sha256((self.root / "server.properties").read_bytes()).hexdigest())
        self.assertTrue(list((self.root / ".ripple-panel" / "file-history").rglob("server.properties")))


if __name__ == "__main__":
    unittest.main()
