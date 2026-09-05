import hashlib
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile

import server
import server_bootstrap as boot


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / ".test-work" / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.patches = [patch.object(server, "DATA_ROOT", self.root / "panel"),
                        patch.object(server, "SETTINGS_PATH", self.root / "panel/settings.json")]
        for key in ("JOBS", "RUNTIMES", "ACTION_LOCKS", "OPERATIONS", "LAST_EXITS"):
            self.patches.append(patch.object(server, key, {}))
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.assertEqual(self.root.resolve().parent.name, ".test-work")
        shutil.rmtree(self.root)

    def test_neoforge_installer_is_setup_not_game_core(self):
        plan = boot.recognize(["neoforge-21.1.249-installer.jar"], {})
        self.assertEqual((plan["game_version"], plan["java_major"]), ("1.21.1", 21))
        self.assertTrue(plan["needs_install"])
        self.assertTrue(plan["launch_target"].endswith("_args.txt"))

    def test_variables_pin_fabric_version_and_installer(self):
        plan = boot.recognize(["start.bat", "variables.txt"], {"variables.txt":
            "MINECRAFT_VERSION=1.20.1\nMODLOADER=Fabric\nMODLOADER_VERSION=0.19.3\nFABRIC_INSTALLER_VERSION=1.1.2"})
        self.assertEqual((plan["loader"], plan["loader_version"], plan["fabric_installer"]), ("fabric", "0.19.3", "1.1.2"))
        self.assertEqual(plan["java_major"], 17)

    def test_installed_forge_keeps_exact_version(self):
        platform = "win" if boot.os.name == "nt" else "unix"
        target = f"libraries/net/minecraftforge/forge/1.20.1-47.4.20/{platform}_args.txt"
        plan = boot.recognize([target], {})
        self.assertFalse(plan["needs_install"])
        self.assertEqual(plan["launch_target"], target)
        self.assertEqual(plan["loader_version"], "47.4.20")

    def test_gtnh_uses_17_not_legacy_8_and_preserves_argfile(self):
        plan = boot.recognize(["lwjgl3ify-forgePatches.jar", "java9args.txt"], {})
        self.assertEqual(plan["java_major"], 17)
        self.assertEqual(plan["argfiles"], ["java9args.txt"])
        (self.root / "lwjgl3ify-forgePatches.jar").write_bytes(b"fixture")
        (self.root / "java9args.txt").write_text("-Djava.system.class.loader=RfbSystemClassLoader")
        with patch.object(server, "java_executable", return_value="java"):
            command, _ = server.build_launch({"path": str(self.root), "java": "java", "xms": "1G", "xmx": "5G",
                                              "launch_mode": "jar", "launch_target": "lwjgl3ify-forgePatches.jar"})
        self.assertIn(f"@{(self.root / 'java9args.txt').resolve()}", command)
        self.assertIn("-Dfml.readTimeout=180", command)

    def test_hybrid_core_is_not_replaced_by_plain_forge(self):
        plan = boot.recognize(["luminara-1.20.1.jar", "libraries/net/minecraftforge/forge/1.20.1-47.4.16/win_args.txt"], {})
        self.assertFalse(plan["supported"])

    def test_unknown_mods_only_pack_does_not_guess_versions(self):
        plan = boot.recognize(["mods/something-1.20.1.jar", "config/test.json"], {})
        self.assertFalse(plan["supported"])

    def test_invalid_metadata_cannot_become_download_path(self):
        for version in ("../../oops", "21.1.1;calc", "", "${VERSION}"):
            with self.subTest(version=version):
                plan=boot.recognize([], {}, {"loader":"neoforge","game_version":"1.21.1","loader_version":version})
                self.assertFalse(plan["supported"])

    def test_unknown_future_neoforge_version_is_not_guessed(self):
        self.assertFalse(boot.recognize(["neoforge-99.1.2-installer.jar"], {})["supported"])

    def test_actual_java_version_not_filename_selects_runtime(self):
        with patch.object(boot, "java_major", side_effect=lambda p: {"called-java21":17,"correct-java":21}.get(p)), patch.object(boot,"remote_json") as remote:
            result=boot.ensure_java(21,["called-java21","correct-java"],self.root,lambda _:None)
        self.assertEqual(result,"correct-java")
        remote.assert_not_called()

    def test_portable_java_reused_without_download(self):
        java=self.root / "java/8/temurin/bin/java.exe"
        java.parent.mkdir(parents=True); java.write_bytes(b"fixture")
        with patch.object(boot,"java_major",return_value=8), patch.object(boot,"remote_json") as remote:
            result=boot.ensure_java(8,[],self.root,lambda _:None)
        self.assertEqual(result,str(java))
        remote.assert_not_called()

    def test_official_urls_reject_private_hosts_auth_and_http(self):
        for url in ("http://maven.neoforged.net/file", "https://localhost/a", "https://127.0.0.1/a",
                    "https://github.com@evil.example/a", "https://github.com:444/a", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaises(ValueError): boot.official_url(url)
        self.assertEqual(boot.official_url("https://maven.neoforged.net/a"),"https://maven.neoforged.net/a")

    def test_redirect_to_nonofficial_host_is_rejected(self):
        with self.assertRaises(ValueError):
            boot.OfficialRedirect().redirect_request(None,None,302,"",{},"https://127.0.0.1/secret")

    def test_metadata_paths_cannot_escape(self):
        for name in ("../outside", "C:/outside", "\\\\host\\share", "mods/../../outside", "mods/x:stream", "/abs", "mods/.. /outside", "mods/CON.jar", "mods/file. "):
            with self.subTest(name=name), self.assertRaises(ValueError): boot.safe_child(self.root,name)

    def test_half_installed_core_retries_even_when_args_file_exists(self):
        plan=boot.recognize(["neoforge-21.1.249-installer.jar"], {})
        target=self.root / plan["launch_target"]; target.parent.mkdir(parents=True); target.write_text("partial args")
        pending=self.root / boot.CORE_PENDING; pending.parent.mkdir(); pending.write_text("{}")
        self.assertTrue(boot.folder_plan(self.root)["needs_install"])
        with patch.object(boot,"ensure_java",return_value="java21"), patch.object(boot,"install_core") as install:
            boot.prepare(self.root,[],self.root / "cache",lambda _:None)
        install.assert_called_once()

    def test_failed_install_retains_retry_marker(self):
        plan=boot.recognize(["neoforge-21.1.249-installer.jar"], {})
        process=Mock(returncode=1); process.poll.return_value=1
        with patch.object(boot,"remote_bytes",return_value=b"a"*64), patch.object(boot,"download"), \
             patch.object(boot.subprocess,"Popen",return_value=process), self.assertRaises(RuntimeError):
            boot.install_core(self.root,plan,"java",lambda _:None)
        self.assertTrue((self.root / boot.CORE_PENDING).is_file())

    def test_successful_retry_clears_only_its_install_marker(self):
        plan=boot.recognize(["neoforge-21.1.249-installer.jar"], {})
        target=self.root / plan["launch_target"]; target.parent.mkdir(parents=True); target.write_text("args")
        pending=self.root / boot.CORE_PENDING; pending.parent.mkdir(); pending.write_text("{}")
        process=Mock(returncode=0); process.poll.return_value=0
        with patch.object(boot,"remote_bytes",return_value=b"a"*64), patch.object(boot,"download"), \
             patch.object(boot.subprocess,"Popen",return_value=process):
            boot.install_core(self.root,plan,"java",lambda _:None)
        self.assertFalse(pending.exists())
        self.assertEqual(target.read_text(),"args")

    def test_preflight_reports_automatic_setup_not_false_failure(self):
        (self.root / "neoforge-21.1.249-installer.jar").write_bytes(b"fixture")
        profile={"id":"p","path":str(self.root),"java":"missing-java","auto_setup":True}
        with patch.object(server,"server_is_running",return_value={}), patch.object(server,"java_version_text",return_value="missing"), \
             patch.object(server,"find_listening_pid",return_value=None), patch.object(server,"eula_accepted",return_value=True):
            data=server.preflight_data(profile)
        self.assertTrue(data["ready"])
        self.assertEqual(data["required_java_major"],21)
        self.assertIn("自动匹配 Java", [item["title"] for item in data["checks"]])

    def test_verified_download_is_reused(self):
        file=self.root / "download.jar"; file.write_bytes(b"ok")
        with patch.object(boot,"response") as network:
            boot.download("https://meta.fabricmc.net/file",file,lambda _:None,hashlib.sha256(b"ok").hexdigest(),2)
        network.assert_not_called()

    def test_bad_hash_never_replaces_existing_file(self):
        file=self.root / "download.jar"; file.write_bytes(b"original")
        with patch.object(boot,"response",side_effect=lambda _:io.BytesIO(b"bad")), patch.object(boot.time,"sleep"), self.assertRaises(ValueError):
            boot.download("https://meta.fabricmc.net/file",file,lambda _:None,hashlib.sha256(b"good").hexdigest())
        self.assertEqual(file.read_bytes(),b"original")

    def test_download_cancel_does_not_retry(self):
        with patch.object(boot,"response",side_effect=lambda _:io.BytesIO(b"data")) as response, self.assertRaises(boot.Cancelled):
            boot.download("https://meta.fabricmc.net/file",self.root / "a",Mock(side_effect=boot.Cancelled("stop")))
        self.assertEqual(response.call_count,1)

    def test_installer_can_never_be_selected_as_game_jar(self):
        target=self.root / "neoforge-21.1.249-installer.jar"; target.write_bytes(b"fake")
        with patch.object(server,"java_executable",return_value="java"), self.assertRaisesRegex(ValueError,"安装器"):
            server.build_launch({"path":str(self.root),"launch_mode":"jar","launch_target":target.name})

    def test_status_schema_rejects_starting_messages(self):
        values=[None,"Server is still starting",{}, {"text":"still starting"},
                {"version":"1.20.1","players":{}},
                {"version":{"name":"1.20.1","protocol":763},"players":{"online":"0","max":20}},
                {"version":{"name":"1.20.1","protocol":True},"players":{"online":0,"max":20}}]
        for value in values:
            with self.subTest(value=value): self.assertFalse(server.valid_minecraft_status(value))
        self.assertTrue(server.valid_minecraft_status({"version":{"name":"1.7.10","protocol":5},"players":{"online":0,"max":2}}))

    def test_cancel_before_preparation_never_launches(self):
        server.JOBS["j"]={"cancel_requested":True}
        with patch.object(server,"start_server") as start, self.assertRaises(boot.Cancelled):
            server.perform_one_click({"id":"p","path":str(self.root)},"j")
        start.assert_not_called()

    def test_cancel_owned_start_uses_normal_stop(self):
        profile={"id":"p","path":str(self.root)}
        with patch.object(server,"check_start_cancelled",side_effect=[None,boot.Cancelled("stop")]), \
             patch.object(server,"server_is_running",return_value={"running":False}), \
             patch.object(server,"start_server"), patch.object(server,"runtime_for",return_value={"process":Mock()}), \
             patch.object(server,"stop_server") as stop, self.assertRaises(boot.Cancelled):
            server.perform_one_click(profile,"j")
        stop.assert_called_once_with(profile,"j")

    def test_cancel_does_not_stop_preexisting_server(self):
        profile={"id":"p","path":str(self.root)}
        with patch.object(server,"check_start_cancelled",side_effect=[None,boot.Cancelled("stop")]), \
             patch.object(server,"server_is_running",return_value={"running":True}), \
             patch.object(server,"start_server"), patch.object(server,"stop_server") as stop, self.assertRaises(boot.Cancelled):
            server.perform_one_click(profile,"j")
        stop.assert_not_called()

    def test_duplicate_start_job_is_rejected(self):
        server.JOBS["j"]={"id":"j","server_id":"p","state":"queued","type":"start"}
        with patch.object(server,"eula_accepted",return_value=True), self.assertRaises(RuntimeError):
            server.start_one_click({"id":"p","path":str(self.root)})

    def test_new_import_requires_explicit_eula_before_inspection(self):
        with patch.object(server,"inspect_server_archive") as inspect, self.assertRaisesRegex(ValueError,"EULA"):
            server.validate_import_request({"start_after_import":True,"accept_eula":False})
        inspect.assert_not_called()

    def test_ftb_requires_explicit_consent_and_new_directory(self):
        with self.assertRaises(ValueError): server.start_ftb_import({"pack_id":125})
        with self.assertRaises(FileExistsError): server.start_ftb_import({"pack_id":125,"destination":str(self.root),"accept_eula":True,"trust_pack":True})

    def test_partial_ftb_resume_uses_pinned_version(self):
        (self.root / boot.FTB_SOURCE).write_text(json.dumps({"pack_id":125,"version_id":100487,"complete":False}))
        with patch.object(boot,"ftb_manifest",return_value={}) as manifest, patch.object(boot,"install_ftb") as install, \
             patch.object(boot,"folder_plan",return_value={"supported":False}):
            boot.prepare(self.root,[],self.root / "cache",lambda _:None)
        manifest.assert_called_once_with(125,100487)
        install.assert_called_once()
        self.assertTrue(json.loads((self.root / boot.FTB_SOURCE).read_text())["complete"])

    def test_ftb_rejects_bad_paths_before_downloading(self):
        manifest={"name":"test","targets":[{"type":"game","version":"1.21.1"},{"type":"modloader","name":"neoforge","version":"21.1.248"}],
                  "files":[{"path":"../","name":"bad","size":1,"url":"https://files.feed-the-beast.com/a","sha1":"a"*40}]}
        with patch.object(boot,"download") as download, self.assertRaises(ValueError):
            boot.install_ftb(self.root,manifest,lambda _:None)
        download.assert_not_called()

    def test_prep_failure_keeps_world_and_partial_files(self):
        world=self.root / "world"; world.mkdir(); (world / "level.dat").write_bytes(b"world")
        (self.root / boot.FTB_SOURCE).write_text(json.dumps({"pack_id":125,"version_id":100487,"complete":False}))
        with patch.object(boot,"ftb_manifest",side_effect=OSError("offline")), self.assertRaises(OSError):
            boot.prepare(self.root,[],self.root / "cache",lambda _:None)
        self.assertEqual((world / "level.dat").read_bytes(),b"world")
        self.assertFalse(json.loads((self.root / boot.FTB_SOURCE).read_text())["complete"])

    def test_ftb_low_disk_fails_before_network(self):
        manifest={"name":"test","targets":[{"type":"game","version":"1.21.1"},{"type":"modloader","name":"neoforge","version":"21.1.248"}],
                  "files":[{"path":"./mods","name":"test.jar","size":1,"url":"https://files.feed-the-beast.com/a","sha1":"a"*40}]}
        with patch.object(boot.shutil,"disk_usage",return_value=Mock(free=0)), patch.object(boot,"download") as download, self.assertRaisesRegex(RuntimeError,"空间不足"):
            boot.install_ftb(self.root,manifest,lambda _:None)
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
