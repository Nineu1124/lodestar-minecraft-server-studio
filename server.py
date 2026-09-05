from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import shutil
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
from collections import defaultdict
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import server_bootstrap as bootstrapper


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
DATA_ROOT = APP_ROOT / "data"
SETTINGS_PATH = DATA_ROOT / "settings.json"
SCHEDULER_STATE_PATH = DATA_ROOT / "scheduler-state.json"
APP_NAME = "Lodestar"
APP_VERSION = "3.1.0"
HOST = os.environ.get("LODESTAR_PANEL_HOST", os.environ.get("RIPPLE_PANEL_HOST", "127.0.0.1"))
PORT = int(os.environ.get("LODESTAR_PANEL_PORT", os.environ.get("RIPPLE_PANEL_PORT", "8765")))
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_MOD_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILE_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
MAX_TEXT_FILE_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_FILES = 200_000
MAX_ARCHIVE_EXPANDED_BYTES = 80 * 1024 * 1024 * 1024

CONFIG_LOCK = threading.RLock()
ACTION_LOCKS: dict[str, Any] = {}
OPERATIONS: dict[str, str] = {}
RUNTIME_LOCK = threading.RLock()
RUNTIMES: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()
JOBS: dict[str, dict[str, Any]] = {}
METRICS_LOCK = threading.RLock()
CPU_SAMPLES: dict[int, tuple[float, int]] = {}
METRIC_HISTORY: dict[str, list[dict[str, Any]]] = {}
SCHEDULER_LOCK = threading.RLock()
LAST_EXITS: dict[str, dict[str, Any]] = {}
BACKUP_MANIFEST = ".lodestar-backup.json"
BACKUP_EXTRAS = {"server.properties", "whitelist.json", "ops.json", "banned-players.json", "banned-ips.json"}

ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
PLAYER_NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")
MEMORY_VALUE = re.compile(r"^[1-9][0-9]{0,3}[MG]$", re.I)
SAFE_COMMAND = re.compile(r"^[^\x00-\x1f\x7f]{1,500}$")
SAFE_FILE = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]{1,180}$")
SAFE_ARCHIVE_NAME = re.compile(r"^[^<>:\"|?*\x00-\x1f]{1,240}$")

INT_SETTINGS = {
    "max-players": (1, 500),
    "view-distance": (2, 32),
    "simulation-distance": (2, 32),
    "spawn-protection": (0, 100),
    "server-port": (1, 65535),
    "max-tick-time": (-1, 9_223_372_036_854_775_807),
}
BOOL_SETTINGS = {
    "allow-flight",
    "white-list",
    "enforce-whitelist",
    "online-mode",
    "enforce-secure-profile",
    "pvp",
    "hardcore",
    "enable-command-block",
    "spawn-animals",
    "spawn-monsters",
    "spawn-npcs",
}
ENUM_SETTINGS = {
    "difficulty": {"peaceful", "easy", "normal", "hard"},
    "gamemode": {"survival", "creative", "adventure", "spectator"},
}
TEXT_SETTINGS = {"motd": 160, "level-name": 80, "resource-pack": 500}

QUICK_COMMANDS = {
    "save": "save-all flush",
    "day": "time set day",
    "night": "time set night",
    "clear_weather": "weather clear",
    "keep_inventory_on": "gamerule keepInventory true",
    "keep_inventory_off": "gamerule keepInventory false",
}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def default_settings() -> dict[str, Any]:
    return {"version": 1, "active_server_id": None, "servers": []}


def read_settings() -> dict[str, Any]:
    with CONFIG_LOCK:
        if not SETTINGS_PATH.exists():
            return default_settings()
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default_settings()
        if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
            return default_settings()
        return data


def write_settings(data: dict[str, Any]) -> None:
    with CONFIG_LOCK:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, SETTINGS_PATH)


def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    root = Path(profile["path"])
    detected = detect_launch(root)
    return {
        "id": profile["id"],
        "name": profile.get("name") or root.name,
        "path": str(root),
        "java": profile.get("java", "java"),
        "xms": profile.get("xms", "2G"),
        "xmx": profile.get("xmx", "6G"),
        "launch_mode": profile.get("launch_mode", "auto"),
        "launch_target": profile.get("launch_target", ""),
        "auto_setup": bool(profile.get("auto_setup", True)),
        "external_address": profile.get("external_address", ""),
        "auto_backup_keep": int(profile.get("auto_backup_keep", 10)),
        "backup_schedule_enabled": bool(profile.get("backup_schedule_enabled", False)),
        "backup_interval_hours": int(profile.get("backup_interval_hours", 6)),
        "restart_schedule_enabled": bool(profile.get("restart_schedule_enabled", False)),
        "restart_time": str(profile.get("restart_time", "04:00")),
        "exists": root.is_dir(),
        "detected": detected,
    }


def get_profile(server_id: str | None = None) -> dict[str, Any]:
    settings = read_settings()
    wanted = server_id or settings.get("active_server_id")
    for profile in settings["servers"]:
        if profile.get("id") == wanted:
            return profile
    if server_id:
        raise FileNotFoundError("该实例不存在或已被移除，请刷新实例列表")
    if settings["servers"]:
        return settings["servers"][0]
    raise ValueError("还没有导入服务端，请先点击“导入服务端”")


def require_profile(server_id: Any) -> dict[str, Any]:
    if not isinstance(server_id, str) or not server_id.strip():
        raise ValueError("修改操作必须明确指定实例 ID")
    return get_profile(server_id)


def ensure_server_folder(path_text: str) -> Path:
    if not isinstance(path_text, str) or not path_text.strip():
        raise ValueError("请输入服务端文件夹路径")
    root = Path(path_text.strip().strip('"')).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"服务端文件夹不存在：{root}")
    markers = [root / "server.properties", root / "eula.txt"]
    has_launch = any(root.glob("*.jar")) or any(root.glob("*.bat")) or any(root.glob("*.sh"))
    has_launch = has_launch or bootstrapper.folder_plan(root).get("supported", False)
    if not any(path.exists() for path in markers) and not has_launch:
        raise ValueError("这个文件夹不像 Minecraft 服务端：未找到配置、JAR 或启动脚本")
    return root


def register_server(payload: dict[str, Any]) -> dict[str, Any]:
    root = ensure_server_folder(str(payload.get("path", "")))
    settings = read_settings()
    for profile in settings["servers"]:
        if Path(profile["path"]).resolve() == root:
            settings["active_server_id"] = profile["id"]
            write_settings(settings)
            return profile
    java = str(payload.get("java") or shutil.which("java") or "java")
    xms = str(payload.get("xms") or "2G").upper()
    xmx = str(payload.get("xmx") or "6G").upper()
    validate_memory(xms, xmx)
    profile = {
        "id": uuid.uuid4().hex[:12],
        "name": str(payload.get("name") or root.name).strip()[:80],
        "path": str(root),
        "java": java,
        "xms": xms,
        "xmx": xmx,
        "launch_mode": "auto",
        "launch_target": "",
        "auto_setup": bool(payload.get("auto_setup", True)),
        "external_address": str(payload.get("external_address") or "").strip()[:200],
        "auto_backup_keep": 10,
        "backup_schedule_enabled": False,
        "backup_interval_hours": 6,
        "restart_schedule_enabled": False,
        "restart_time": "04:00",
        "created_at": now_iso(),
    }
    settings["servers"].append(profile)
    settings["active_server_id"] = profile["id"]
    write_settings(settings)
    return profile


def update_profile(payload: dict[str, Any]) -> dict[str, Any]:
    server_id = str(payload.get("server_id", ""))
    updates = payload.get("profile")
    if not isinstance(updates, dict):
        raise ValueError("启动配置格式不正确")
    allowed = {
        "name",
        "java",
        "xms",
        "xmx",
        "launch_mode",
        "launch_target",
        "auto_setup",
        "external_address",
        "auto_backup_keep",
        "backup_schedule_enabled",
        "backup_interval_hours",
        "restart_schedule_enabled",
        "restart_time",
    }
    if set(updates) - allowed:
        raise ValueError("启动配置中包含不允许的字段")
    for boolean_key in ("backup_schedule_enabled", "restart_schedule_enabled", "auto_setup"):
        if boolean_key in updates and not isinstance(updates[boolean_key], bool):
            raise ValueError(f"{boolean_key} 必须是布尔值")
    settings = read_settings()
    for profile in settings["servers"]:
        if profile.get("id") != server_id:
            continue
        merged = {**profile, **updates}
        merged["name"] = str(merged.get("name") or Path(profile["path"]).name).strip()[:80]
        merged["java"] = str(merged.get("java") or "java").strip()
        merged["xms"] = str(merged.get("xms") or "2G").upper()
        merged["xmx"] = str(merged.get("xmx") or "6G").upper()
        validate_memory(merged["xms"], merged["xmx"])
        if merged.get("launch_mode") not in {"auto", "forge_args", "jar", "script"}:
            raise ValueError("启动方式无效")
        merged["launch_target"] = str(merged.get("launch_target") or "").strip()[:500]
        merged["external_address"] = str(merged.get("external_address") or "").strip()[:200]
        keep = int(merged.get("auto_backup_keep", 10))
        if not 1 <= keep <= 100:
            raise ValueError("备份保留数量必须在 1–100 之间")
        merged["auto_backup_keep"] = keep
        merged["backup_schedule_enabled"] = bool(merged.get("backup_schedule_enabled", False))
        interval = int(merged.get("backup_interval_hours", 6))
        if not 1 <= interval <= 168:
            raise ValueError("自动备份间隔必须在 1–168 小时之间")
        merged["backup_interval_hours"] = interval
        merged["restart_schedule_enabled"] = bool(merged.get("restart_schedule_enabled", False))
        restart_time = str(merged.get("restart_time", "04:00")).strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", restart_time):
            raise ValueError("定时重启时间必须是 HH:MM 格式")
        merged["restart_time"] = restart_time
        profile.clear()
        profile.update(merged)
        write_settings(settings)
        return profile
    raise ValueError("找不到该服务端")


def select_server(server_id: str) -> None:
    settings = read_settings()
    if not any(item.get("id") == server_id for item in settings["servers"]):
        raise ValueError("找不到该服务端")
    settings["active_server_id"] = server_id
    write_settings(settings)


def unregister_server(server_id: str) -> None:
    settings = read_settings()
    profile = next((item for item in settings["servers"] if item.get("id") == server_id), None)
    if not profile:
        raise ValueError("找不到该服务端")
    if server_is_running(profile)["running"]:
        raise ValueError("请先停止服务端，再从面板移除")
    settings["servers"] = [item for item in settings["servers"] if item.get("id") != server_id]
    if settings.get("active_server_id") == server_id:
        settings["active_server_id"] = settings["servers"][0]["id"] if settings["servers"] else None
    write_settings(settings)


def validate_memory(xms: str, xmx: str) -> None:
    if not MEMORY_VALUE.fullmatch(xms) or not MEMORY_VALUE.fullmatch(xmx):
        raise ValueError("内存格式应类似 2G、6144M 或 10G")

    def megabytes(value: str) -> int:
        number = int(value[:-1])
        return number * 1024 if value[-1].upper() == "G" else number

    if megabytes(xms) > megabytes(xmx):
        raise ValueError("最小内存不能大于最大内存")


def safe_target(root: Path, target_text: str) -> Path:
    if not target_text:
        raise ValueError("请选择启动文件")
    candidate = (root / target_text).resolve() if not Path(target_text).is_absolute() else Path(target_text).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("启动文件必须位于服务端目录内") from exc
    if not candidate.is_file():
        raise ValueError(f"找不到启动文件：{candidate}")
    return candidate


def detect_launch(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "loader": "unknown",
        "version": "未知",
        "mode": "none",
        "target": "",
        "candidates": [],
    }
    if not root.is_dir():
        return result

    candidates: list[dict[str, str]] = []
    forge_args = sorted(root.glob("libraries/net/minecraftforge/forge/*/win_args.txt"), reverse=True)
    neo_args = sorted(root.glob("libraries/net/neoforged/neoforge/*/win_args.txt"), reverse=True)
    if forge_args:
        relative = forge_args[0].relative_to(root).as_posix()
        version_text = forge_args[0].parent.name
        candidates.append({"mode": "forge_args", "target": relative, "label": f"Forge {version_text}"})
        result.update(loader="forge", version=version_text.split("-")[0], mode="forge_args", target=relative)
    elif neo_args:
        relative = neo_args[0].relative_to(root).as_posix()
        version_text = neo_args[0].parent.name
        candidates.append({"mode": "forge_args", "target": relative, "label": f"NeoForge {version_text}"})
        result.update(loader="neoforge", version=version_text, mode="forge_args", target=relative)

    jars = list(root.glob("*.jar"))
    jar_order = sorted(
        jars,
        key=lambda p: (
            0 if "fabric-server-launch" in p.name.lower() else
            1 if any(x in p.name.lower() for x in ("paper", "purpur", "spigot")) else
            2 if p.name.lower() == "server.jar" else 3,
            p.name.lower(),
        ),
    )
    for jar in jar_order:
        lower = jar.name.lower()
        if "installer" in lower:
            continue
        label = jar.name
        candidates.append({"mode": "jar", "target": jar.name, "label": label})
        if result["mode"] == "none":
            loader = "fabric" if "fabric" in lower else "paper" if "paper" in lower else "purpur" if "purpur" in lower else "vanilla"
            result.update(loader=loader, mode="jar", target=jar.name)

    preferred_scripts = ["run.bat", "start.bat", "serverstart.bat", "startserver.bat", "run.sh", "start.sh"]
    seen: set[str] = set()
    for name in preferred_scripts:
        script = root / name
        if script.is_file():
            seen.add(script.name.casefold())
            candidates.append({"mode": "script", "target": script.name, "label": f"启动脚本 · {script.name}"})
    for script in sorted([*root.glob("*.bat"), *root.glob("*.cmd"), *root.glob("*.sh")]):
        if script.name.casefold() not in seen:
            candidates.append({"mode": "script", "target": script.name, "label": f"启动脚本 · {script.name}"})
    if result["mode"] == "none" and candidates:
        result.update(mode=candidates[0]["mode"], target=candidates[0]["target"])
    result["candidates"] = candidates
    plan = bootstrapper.folder_plan(root)
    if plan.get("supported"):
        result.update(loader=plan["loader"], version=plan["game_version"], bootstrap=plan)
        if plan["needs_install"]:
            result.update(mode="auto", target="")
        else:
            result.update(mode=plan["launch_mode"], target=plan["launch_target"])
            if not any(c["target"] == plan["launch_target"] for c in candidates):
                candidates.insert(0, {"mode": plan["launch_mode"], "target": plan["launch_target"], "label": "整合包启动目标"})
    return result


def discover_java_runtimes() -> list[dict[str, str]]:
    """Return locally available Java executables without modifying PATH."""
    candidates: list[Path] = []
    configured = shutil.which("java")
    if configured:
        candidates.append(Path(configured))
    if os.name == "nt":
        runtime_root = Path.home() / "AppData/Roaming/.minecraft/runtime"
        if runtime_root.is_dir():
            candidates.extend(runtime_root.glob("*/windows*/**/bin/java.exe"))
            candidates.extend(runtime_root.glob("*/bin/java.exe"))
        for env_name in ("JAVA_HOME", "JDK_HOME"):
            if os.environ.get(env_name):
                candidates.append(Path(os.environ[env_name]) / "bin/java.exe")
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = str(resolved).casefold()
        if key in seen or not resolved.is_file():
            continue
        seen.add(key)
        label = resolved.parent.parent.name or "Java"
        unique.append({"path": str(resolved), "label": label})
    return unique


def recommended_java(game_version: str, runtimes: list[dict[str, str]] | None = None, major: int | None = None) -> str:
    runtimes = runtimes or discover_java_runtimes()
    if not runtimes:
        return shutil.which("java") or "java"
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", game_version or "")
    wanted = 17
    if match:
        minor = int(match.group(2))
        patch = int(match.group(3) or 0)
        if minor <= 16:
            wanted = 8
        elif minor > 20 or (minor == 20 and patch >= 5):
            wanted = 21
    wanted = major or wanted
    hints = {
        8: ("java-runtime-legacy", "jre-legacy", "jdk8", "java8"),
        16: ("java-runtime-alpha", "jdk16", "java16"),
        17: ("java-runtime-beta", "java-runtime-gamma", "jdk17", "java17"),
        21: ("java-runtime-delta", "java-runtime-gamma", "jdk21", "java21"),
    }
    for runtime in runtimes:
        lower = runtime["path"].casefold()
        if any(hint in lower for hint in hints.get(wanted, ())):
            return runtime["path"]
    return runtimes[0]["path"]


def decoded_zip_name(info: zipfile.ZipInfo) -> str:
    """Recover common GBK filenames from ZIPs created by older Chinese tools."""
    name = info.filename.replace("\\", "/")
    if info.flag_bits & 0x800:
        return name
    try:
        recovered = name.encode("cp437").decode("gbk")
        if "�" not in recovered:
            return recovered
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return name


def normalized_archive_member(info: zipfile.ZipInfo) -> str:
    name = decoded_zip_name(info).strip().replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    parts = [part for part in name.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"压缩包包含不安全路径：{name or '(空路径)'}")
    if re.match(r"^[A-Za-z]:", parts[0]) or name.startswith("/"):
        raise ValueError(f"压缩包包含绝对路径：{name}")
    if any(not SAFE_ARCHIVE_NAME.fullmatch(part) for part in parts):
        raise ValueError(f"压缩包包含 Windows 不支持的文件名：{name}")
    return "/".join(parts)


def archive_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def archive_layout(names: list[str], script_contents: dict[str, str] | None = None) -> dict[str, Any]:
    script_contents = script_contents or {}
    scores: dict[str, int] = defaultdict(int)
    evidence: dict[str, set[str]] = defaultdict(set)
    files = [name.rstrip("/") for name in names if name and not name.endswith("/")]

    def mark(root: str, score: int, item: str) -> None:
        scores[root.strip("/")] += score
        evidence[root.strip("/")].add(item)

    for name in files:
        parts = name.split("/")
        base = parts[-1].casefold()
        parent = "/".join(parts[:-1])
        if "mods" in parts[:-1]:
            mark("/".join(parts[:parts.index("mods")]), 1, "mods")
        if base == "server.properties":
            mark(parent, 60, "server.properties")
        elif base == "eula.txt":
            mark(parent, 35, "eula.txt")
        elif base in {"run.bat", "start.bat", "serverstart.bat", "startserver.bat", ".run.bat",
                      "run.sh", "start.sh", ".run.sh"}:
            mark(parent, 30, parts[-1])
        if len(parts) >= 6 and parts[-1].casefold() == "win_args.txt" and "libraries" in [p.casefold() for p in parts]:
            lib_index = [p.casefold() for p in parts].index("libraries")
            mark("/".join(parts[:lib_index]), 70, "Forge/NeoForge 参数文件")

    candidate_roots = set(scores)
    for root in list(candidate_roots):
        prefix = f"{root}/" if root else ""
        for name in files:
            if not name.startswith(prefix):
                continue
            relative = name[len(prefix):]
            if "/" not in relative and relative.casefold().endswith(".jar"):
                mark(root, 20, relative)
            elif relative.casefold().startswith("mods/"):
                mark(root, 4, "mods")
            elif relative.casefold().startswith("plugins/"):
                mark(root, 4, "plugins")
            elif relative.casefold().startswith("libraries/"):
                mark(root, 4, "libraries")

    ordered = sorted(scores, key=lambda root: (-scores[root], root.count("/"), len(root)))
    selected = ordered[0] if ordered else ""
    prefix = f"{selected}/" if selected else ""
    direct_files = [name[len(prefix):] for name in files if name.startswith(prefix) and "/" not in name[len(prefix):]]
    candidates: list[dict[str, str]] = []
    lower_names = {name.casefold(): name for name in files}
    forge_matches = [name for name in files if name.startswith(prefix) and re.search(
        r"libraries/net/(?:minecraftforge/forge|neoforged/neoforge)/[^/]+/win_args\.txt$", name, re.I)]
    if forge_matches:
        relative = forge_matches[0][len(prefix):]
        label = "NeoForge 参数文件" if "neoforged" in relative.casefold() else "Forge 参数文件"
        candidates.append({"mode": "forge_args", "target": relative, "label": label})
    jar_files = sorted([name for name in direct_files if name.casefold().endswith(".jar") and "installer" not in name.casefold()], key=str.casefold)
    for name in jar_files:
        candidates.append({"mode": "jar", "target": name, "label": name})
    preferred_scripts = ("run.bat", "start.bat", "serverstart.bat", "startserver.bat", ".run.bat",
                         "run.sh", "start.sh", ".run.sh")
    for wanted in preferred_scripts:
        match_name = next((name for name in direct_files if name.casefold() == wanted), None)
        if match_name:
            candidates.append({"mode": "script", "target": match_name, "label": f"启动脚本 · {match_name}"})

    recommended: dict[str, str] | None = None
    recommendation_reason = ""
    for script_name, content in script_contents.items():
        if prefix and not script_name.startswith(prefix):
            continue
        relative_script = script_name[len(prefix):]
        if "/" in relative_script:
            continue
        jar_match = re.search(r"(?:^|\s)-jar\s+[\"']?([^\s\"']+\.jar)", content, re.I | re.M)
        if jar_match:
            jar_name = Path(jar_match.group(1).replace("\\", "/")).name
            match_candidate = next((item for item in candidates if item["mode"] == "jar"
                                    and item["target"].casefold() == jar_name.casefold()), None)
            if match_candidate:
                recommended = match_candidate
                recommendation_reason = f"原启动脚本 {relative_script} 指向此 JAR"
                break
        args_match = re.search(r"@([^\s\"']*win_args\.txt)", content, re.I)
        if args_match:
            args_target = args_match.group(1).replace("\\", "/")
            match_candidate = next((item for item in candidates if item["mode"] == "forge_args"
                                    and item["target"].casefold() == args_target.casefold()), None)
            if match_candidate:
                recommended = match_candidate
                recommendation_reason = f"原启动脚本 {relative_script} 使用此参数文件"
                break
    if recommended is None:
        hybrid_tokens = ("luminara", "arclight", "mohist", "magma", "banner", "catserver", "youer")
        recommended = next((item for item in candidates if item["mode"] == "jar"
                            and any(token in item["target"].casefold() for token in hybrid_tokens)), None)
        if recommended:
            recommendation_reason = "识别到插件混合服务端核心"
    if recommended is None and candidates:
        recommended = candidates[0]
        recommendation_reason = "按加载器与服务端文件结构自动选择"

    loader = "unknown"
    version = "未知"
    joined = "\n".join(name.casefold() for name in files)
    version_match = re.search(r"libraries/net/minecraftforge/forge/(\d+\.\d+(?:\.\d+)?)-", joined)
    if version_match:
        loader, version = "forge", version_match.group(1)
    else:
        version_match = re.search(r"libraries/net/neoforged/neoforge/([^/]+)/", joined)
        if version_match:
            loader, version = "neoforge", version_match.group(1)
        elif any("fabric" in name.casefold() for name in direct_files):
            loader = "fabric"
        elif any("purpur" in name.casefold() for name in direct_files):
            loader = "purpur"
        elif any(token in name.casefold() for name in direct_files for token in ("paper", "spigot")):
            loader = "paper"
        elif jar_files:
            loader = "vanilla"

    relative_files = [name[len(prefix):] for name in files if name.startswith(prefix)]
    relative_texts = {name[len(prefix):]: value for name, value in script_contents.items() if name.startswith(prefix)}
    plan = bootstrapper.recognize(relative_files, relative_texts)
    if plan.get("supported"):
        loader, version = plan["loader"], plan["game_version"]
        recommended = {"mode": "auto", "target": "", "label": "一键准备并启动（推荐）"}
        candidates.insert(0, recommended)
        recommendation_reason = f"自动准备 {loader}，使用 Java {plan['java_major']}，保留整合包必要参数"
    return {
        "root": selected,
        "root_candidates": [
            {"path": root, "score": scores[root], "evidence": sorted(evidence[root])}
            for root in ordered[:8]
        ],
        "loader": loader,
        "version": version,
        "launch_candidates": candidates,
        "recommended_launch": recommended,
        "recommendation_reason": recommendation_reason,
        "bootstrap": plan,
        "has_properties": f"{prefix}server.properties".casefold() in lower_names,
        "has_eula": f"{prefix}eula.txt".casefold() in lower_names,
        "has_world": any(name.startswith(f"{prefix}world/") for name in files),
    }


def inspect_server_archive(path_text: str) -> dict[str, Any]:
    if not isinstance(path_text, str) or not path_text.strip():
        raise ValueError("请选择服务端压缩包")
    path = Path(path_text.strip().strip('"')).expanduser().resolve()
    if not path.is_file() or path.suffix.casefold() != ".zip":
        raise ValueError("目前支持 ZIP 格式的服务端压缩包")
    names: list[str] = []
    expanded = 0
    compressed = 0
    encrypted = False
    script_contents: dict[str, str] = {}
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist()) > MAX_ARCHIVE_FILES:
            raise ValueError(f"压缩包文件数量超过上限（{MAX_ARCHIVE_FILES:,}）")
        for info in archive.infolist():
            name = normalized_archive_member(info)
            if archive_member_is_symlink(info):
                raise ValueError(f"压缩包包含不支持的符号链接：{name}")
            expanded += info.file_size
            compressed += info.compress_size
            encrypted = encrypted or bool(info.flag_bits & 0x1)
            if expanded > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("压缩包解压后的体积超过 80 GB 安全上限")
            names.append(name + ("/" if info.is_dir() and not name.endswith("/") else ""))
            if (not info.is_dir() and info.file_size <= 256_000
                    and (Path(name).suffix.casefold() in {".bat", ".cmd", ".sh"}
                         or Path(name).name in {"variables.txt", bootstrapper.MARKER})):
                script_contents[name] = archive.read(info).decode("utf-8", errors="replace")
    if encrypted:
        raise ValueError("暂不支持带密码的服务端压缩包")
    layout = archive_layout(names, script_contents)
    if not layout["launch_candidates"]:
        known = bootstrapper.KNOWN_ARCHIVES.get(bootstrapper.digest_file(path))
        if known:
            prefix = layout["root"] + "/" if layout["root"] else ""
            script_contents[prefix + bootstrapper.MARKER] = json.dumps(known)
            layout = archive_layout(names, script_contents)
    if not layout["launch_candidates"]:
        raise ValueError("缺少可靠的服务端核心或加载器版本。请使用作者服务端包；纯客户端整合包暂不能保证自动转换。")
    archive_parent_name = path.parent.name.casefold()
    import_parent = path.parent.parent if ("压缩包" in archive_parent_name or archive_parent_name in {"archives", "packages"}) else path.parent
    default_destination = import_parent / path.stem
    if default_destination.exists():
        default_destination = import_parent / "面板导入" / path.stem
    runtimes = discover_java_runtimes()
    warnings: list[str] = []
    if not layout["has_properties"]:
        warnings.append("首次启动时将创建 server.properties")
    if layout["has_world"]:
        warnings.append("压缩包包含现有世界，导入后会继续使用")
    return {
        "path": str(path),
        "name": path.stem,
        "archive_size": path.stat().st_size,
        "expanded_size": expanded,
        "file_count": len(names),
        "destination": str(default_destination),
        **layout,
        "java_runtimes": runtimes,
        "recommended_java": recommended_java(layout["version"], runtimes, layout.get("bootstrap", {}).get("java_major")),
        "warnings": warnings,
    }


def pick_server_archive() -> str:
    if os.name != "nt":
        raise ValueError("当前系统不支持原生文件选择器，请直接填写压缩包路径")
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(title="选择 Minecraft 服务端压缩包",
                                              filetypes=[("ZIP 压缩包", "*.zip"), ("所有文件", "*.*")])
        root.destroy()
        return selected
    except Exception as exc:
        raise ValueError(f"无法打开文件选择器：{exc}") from exc


def find_extracted_server_root(extraction_root: Path) -> Path:
    best: tuple[int, int, Path] | None = None
    for folder in [extraction_root, *[path for path in extraction_root.rglob("*") if path.is_dir()]]:
        try:
            depth = len(folder.relative_to(extraction_root).parts)
        except ValueError:
            continue
        if depth > 5:
            continue
        score = 0
        score += 50 if (folder / "mods").is_dir() and (folder / "config").is_dir() else 0
        score += 60 if (folder / "server.properties").is_file() else 0
        score += 35 if (folder / "eula.txt").is_file() else 0
        score += 70 if any(folder.glob("libraries/net/minecraftforge/forge/*/win_args.txt")) else 0
        score += 70 if any(folder.glob("libraries/net/neoforged/neoforge/*/win_args.txt")) else 0
        score += 20 if any(folder.glob("*.jar")) else 0
        score += 30 if any((folder / name).is_file() for name in
                           ("run.bat", "start.bat", ".run.bat", "run.sh", "start.sh", ".run.sh")) else 0
        if score and (best is None or (score, -depth) > (best[0], -best[1])):
            best = (score, depth, folder)
    if not best:
        raise ValueError("解压完成，但没有找到服务端启动目录")
    return best[2]


def validate_import_request(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("start_after_import") and payload.get("accept_eula") is not True:
        raise ValueError("一键开服前需要明确同意 Minecraft EULA")
    if payload.get("start_after_import") and payload.get("trust_pack") is not True:
        raise ValueError("请确认信任此整合包，并允许下载官方核心和便携 Java")
    inspection = inspect_server_archive(str(payload.get("archive_path", "")))
    destination_text = str(payload.get("destination") or inspection["destination"]).strip().strip('"')
    destination = Path(destination_text).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"导入目录已存在，请更换目录：{destination}")
    if destination == Path(destination.anchor) or len(destination.parts) < 2:
        raise ValueError("导入目录不能是磁盘根目录")
    xms = str(payload.get("xms") or "2G").upper()
    xmx = str(payload.get("xmx") or "8G").upper()
    validate_memory(xms, xmx)
    launch_mode = str(payload.get("launch_mode") or "auto")
    launch_target = str(payload.get("launch_target") or "")
    if launch_mode not in {"auto", "forge_args", "jar", "script"}:
        raise ValueError("启动方式无效")
    if launch_target and not any(item["mode"] == launch_mode and item["target"] == launch_target
                                 for item in inspection["launch_candidates"]):
        raise ValueError("选择的启动文件不属于当前压缩包")
    properties = validate_properties(payload.get("properties") or {})
    return {
        "inspection": inspection,
        "destination": destination,
        "name": str(payload.get("name") or inspection["name"]).strip()[:80],
        "java": str(payload.get("java") or inspection["recommended_java"] or "java").strip(),
        "xms": xms,
        "xmx": xmx,
        "launch_mode": launch_mode,
        "launch_target": launch_target,
        "properties": properties,
        "accept_eula": bool(payload.get("accept_eula", False)),
        "start_after_import": bool(payload.get("start_after_import", False)),
        "auto_setup": bool(payload.get("auto_setup", True)),
    }


def extract_archive_worker(request: dict[str, Any], job_id: str) -> None:
    inspection = request["inspection"]
    archive_path = Path(inspection["path"])
    destination: Path = request["destination"]
    parent = destination.parent
    temporary = parent / f".lodestar-import-{job_id}"
    try:
        parent.mkdir(parents=True, exist_ok=True)
        if temporary.exists():
            raise FileExistsError(f"临时导入目录已存在：{temporary}")
        temporary.mkdir()
        total = max(1, int(inspection["expanded_size"]))
        if shutil.disk_usage(parent).free < total + 1024**3:
            raise RuntimeError("磁盘空间不足：解压后需至少保留 1 GB 空间")
        written = 0
        set_job(job_id, state="running", progress=1, message="正在安全解压服务端…")
        with zipfile.ZipFile(archive_path) as archive:
            for index, info in enumerate(archive.infolist(), start=1):
                check_start_cancelled(job_id)
                name = normalized_archive_member(info)
                if archive_member_is_symlink(info) or info.flag_bits & 1:
                    raise ValueError("压缩包已改变或包含不安全条目")
                target = (temporary / Path(*name.split("/"))).resolve()
                try:
                    target.relative_to(temporary.resolve())
                except ValueError as exc:
                    raise ValueError(f"压缩包路径越界：{name}") from exc
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        written += len(chunk)
                        if written > MAX_ARCHIVE_EXPANDED_BYTES:
                            raise ValueError("解压数据超过安全上限")
                if index % 50 == 0:
                    set_job(job_id, progress=min(88, max(2, int(written / total * 88))),
                            message=f"正在解压 · {index:,}/{inspection['file_count']:,} 个文件")
        os.replace(temporary, destination)
        server_root = find_extracted_server_root(destination)
        plan = inspection.get("bootstrap", {})
        if plan.get("supported") and plan.get("loader_version"):
            marker = server_root / bootstrapper.MARKER
            if not marker.exists():
                marker.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        set_job(job_id, progress=91, message="正在写入开服配置…")
        if request["properties"]:
            write_properties(server_root, request["properties"])
        if request["accept_eula"]:
            eula_path = server_root / "eula.txt"
            eula_path.write_text("eula=true\n", encoding="utf-8")
        profile = register_server({
            "path": str(server_root), "name": request["name"], "java": request["java"],
            "xms": request["xms"], "xmx": request["xmx"],
        })
        updates: dict[str, Any] = {
            "name": request["name"], "java": request["java"], "xms": request["xms"], "xmx": request["xmx"],
            "launch_mode": request["launch_mode"], "launch_target": request["launch_target"],
            "auto_setup": request["auto_setup"],
        }
        update_profile({"server_id": profile["id"], "profile": updates})
        set_job(job_id, server_id=profile["id"])
        message = "服务端已导入"
        if request["start_after_import"]:
            message = perform_one_click(get_profile(profile["id"]), job_id)
        set_job(job_id, state="done", progress=100, message=message, server_id=profile["id"],
                profile=public_profile(get_profile(profile["id"])))
    except Exception as exc:
        if temporary.is_dir():
            try:
                shutil.rmtree(temporary)
            except OSError:
                pass
        set_job(job_id, state="error", message=str(exc), error=str(exc))


def start_archive_import(payload: dict[str, Any]) -> dict[str, Any]:
    request = validate_import_request(payload)
    job_id = uuid.uuid4().hex[:16]
    job = {
        "id": job_id,
        "type": "archive_import",
        "state": "queued",
        "progress": 0,
        "message": "等待导入…",
        "created_at": now_iso(),
        "server_id": None,
        "archive": request["inspection"]["path"],
        "destination": str(request["destination"]),
    }
    with JOBS_LOCK:
        reserve_destination(request["destination"])
        JOBS[job_id] = job
    threading.Thread(target=extract_archive_worker, args=(request, job_id), daemon=True,
                     name=f"lodestar-import-{job_id}").start()
    return job


def reserve_destination(destination: Path) -> None:
    if destination.exists():
        raise FileExistsError("目标目录已存在；失败的已导入实例请直接点一键开服重试")
    if any(j.get("destination") == str(destination) and j.get("state") in {"queued", "running"} for j in JOBS.values()):
        raise RuntimeError("此目录已有导入任务，请等待完成")


def check_start_cancelled(job_id: str) -> None:
    with JOBS_LOCK:
        if JOBS.get(job_id, {}).get("cancel_requested"):
            raise bootstrapper.Cancelled("已取消一键开服；已下载文件保留，未删除世界")


def perform_one_click(profile: dict[str, Any], job_id: str) -> str:
    started_here = False
    def progress(message):
        check_start_cancelled(job_id)
        set_job(job_id, state="running", progress=93, message=message, phase="preparing")
    try:
        progress("检查端口、EULA、Java 和整合包启动方案…")
        was_running = server_is_running(profile)["running"]
        start_server(profile, job_id, progress)
        started_here = not was_running and runtime_for(profile["id"]) is not None
        set_job(job_id, phase="loading", progress=96, message="核心准备完成，等待模组和世界加载…")
        deadline, ready_count = time.monotonic() + 900, 0
        while time.monotonic() < deadline:
            check_start_cancelled(job_id)
            status = server_is_running(get_profile(profile["id"]))
            if not status["running"]:
                detail = (status.get("startup_failure") or {}).get("detail", "请查看控制台和安装日志")
                raise RuntimeError(f"服务端在就绪前退出：{detail}")
            ready_count = ready_count + 1 if status["ready"] else 0
            if ready_count >= 2:
                return "一键开服完成：已确认游戏服务就绪"
            time.sleep(2)
        raise TimeoutError("15 分钟内未确认游戏服务就绪，请查看控制台")
    except Exception as exc:
        if started_here and runtime_for(profile["id"]):
            try:
                stop_server(profile, job_id)
            except Exception as stop_error:
                raise RuntimeError(f"{exc}；正常停服未完成：{stop_error}。未强制结束，请在控制台处理。") from exc
        raise


def one_click_worker(profile: dict[str, Any], job_id: str) -> None:
    try:
        message = perform_one_click(profile, job_id)
        set_job(job_id, state="done", progress=100, message=message)
    except Exception as exc:
        set_job(job_id, state="error", message=str(exc), error=str(exc))


def start_one_click(profile: dict[str, Any]) -> dict[str, Any]:
    if not eula_accepted(Path(profile["path"])):
        raise ValueError("尚未同意 Minecraft EULA，请先在启动配置中勾选同意")
    with JOBS_LOCK:
        if active_server_job(profile["id"]):
            raise RuntimeError("此实例已有任务，请等待或取消后重试")
        job_id = uuid.uuid4().hex[:16]
        job = {"id": job_id, "type": "start", "server_id": profile["id"], "state": "queued",
               "progress": 0, "phase": "preparing", "message": "等待一键开服…", "created_at": now_iso()}
        JOBS[job_id] = job
    threading.Thread(target=one_click_worker, args=(profile.copy(), job_id), daemon=True).start()
    return job


def cancel_start_job(job_id: str) -> str:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("type") not in {"start", "archive_import", "ftb_import"}:
            raise ValueError("不是可取消的开服任务")
        if job.get("state") not in {"queued", "running"}:
            return "任务已结束"
        job["cancel_requested"] = True
        job["message"] = "正在取消；若游戏服已启动，将尝试正常保存并停服…"
    return "已请求取消；保留已下载文件供下次重试"


def start_ftb_import(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("accept_eula") is not True or payload.get("trust_pack") is not True:
        raise ValueError("请明确同意 EULA 并确认信任此整合包和官方依赖下载")
    pack_id = int(payload.get("pack_id", 0))
    version_id = int(payload["version_id"]) if str(payload.get("version_id") or "").strip() else None
    if not 1 <= pack_id <= 10_000_000 or (version_id is not None and not 1 <= version_id <= 100_000_000):
        raise ValueError("FTB 项目/版本 ID 无效")
    raw = str(payload.get("destination") or "").strip().strip('"')
    if not raw:
        raise ValueError("请选择新的服务端目录")
    destination = Path(raw).expanduser().resolve()
    if destination == Path(destination.anchor):
        raise ValueError("不能使用磁盘根目录")
    xms, xmx = str(payload.get("xms") or "1G").upper(), str(payload.get("xmx") or "6G").upper()
    validate_memory(xms, xmx)
    properties = validate_properties(payload.get("properties") or {"online-mode": True})
    with JOBS_LOCK:
        reserve_destination(destination)
        job_id = uuid.uuid4().hex[:16]
        job = {"id": job_id, "type": "ftb_import", "server_id": None, "state": "queued", "progress": 0,
               "destination": str(destination), "message": "读取 FTB 官方发布版本…", "created_at": now_iso()}
        JOBS[job_id] = job
    def worker():
        try:
            manifest = bootstrapper.ftb_manifest(pack_id, version_id)
            check_start_cancelled(job_id)
            destination.mkdir(parents=True, exist_ok=False)
            (destination / bootstrapper.FTB_SOURCE).write_text(json.dumps({"pack_id": pack_id, "version_id": manifest["id"], "complete": False}), encoding="utf-8")
            write_properties(destination, properties)
            (destination / "eula.txt").write_text("eula=true\n", encoding="utf-8")
            profile = register_server({"path": str(destination), "name": str(payload.get("name") or f"FTB {pack_id} · {manifest['name']}"), "xms": xms, "xmx": xmx, "auto_setup": True})
            set_job(job_id, server_id=profile["id"], state="running")
            message = perform_one_click(profile, job_id)
            set_job(job_id, state="done", progress=100, message=message)
        except Exception as exc:
            set_job(job_id, state="error", error=str(exc), message=str(exc))
    threading.Thread(target=worker, daemon=True, name=f"lodestar-ftb-{job_id}").start()
    return job


def read_properties(root: Path) -> dict[str, str]:
    path = root / "server.properties"
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw and not raw.startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            result[key] = value
    return result


def write_properties(root: Path, changes: dict[str, str]) -> None:
    path = root / "server.properties"
    original = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    pending = dict(changes)
    updated: list[str] = []
    for line in original.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0]
            if key in pending:
                updated.append(f"{key}={pending.pop(key)}")
                continue
        updated.append(line)
    updated.extend(f"{key}={value}" for key, value in pending.items())
    panel_dir = root / ".ripple-panel"
    panel_dir.mkdir(exist_ok=True)
    if path.exists():
        shutil.copy2(path, panel_dir / "server.properties.bak")
    temporary = panel_dir / "server.properties.tmp"
    temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_properties(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("配置格式不正确")
    result: dict[str, str] = {}
    for key, value in payload.items():
        if key in INT_SETTINGS:
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 必须是整数") from exc
            low, high = INT_SETTINGS[key]
            if not low <= number <= high:
                raise ValueError(f"{key} 必须在 {low}–{high} 之间")
            result[key] = str(number)
        elif key in BOOL_SETTINGS:
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须是布尔值")
            result[key] = "true" if value else "false"
        elif key in ENUM_SETTINGS:
            if value not in ENUM_SETTINGS[key]:
                raise ValueError(f"{key} 的值无效")
            result[key] = str(value)
        elif key in TEXT_SETTINGS:
            text = str(value).replace("\r", " ").replace("\n", " ").strip()
            if len(text) > TEXT_SETTINGS[key]:
                raise ValueError(f"{key} 内容过长")
            result[key] = text
        else:
            raise ValueError(f"不允许修改参数：{key}")
    return result


def find_listening_pid(port: int) -> int | None:
    if os.name == "nt":
        command = ["netstat", "-ano", "-p", "tcp"]
        pattern = re.compile(rf"^\s*TCP\s+\S+:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$", re.I)
    else:
        if not shutil.which("lsof"):
            return None
        command = ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]
        pattern = re.compile(r"^\s*(\d+)\s*$")
    try:
        completed = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=8,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in completed.stdout.splitlines():
        match = pattern.match(line)
        if match:
            return int(match.group(1))
    return None


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260),
    ]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


def filetime_ticks(value: FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def windows_process_snapshot(parents: bool = False) -> dict[int, Any]:
    if os.name != "nt":
        return {}
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return {}
    result: dict[int, str] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                result[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID) if parents else entry.szExeFile
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def process_identity(pid: int) -> str | None:
    """A PID alone is not an identity: include its OS creation token."""
    try:
        if os.name != "nt":
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return f"{boot}:{fields[19]}"
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(FILETIME)] * 4
        handle = kernel32.OpenProcess(0x1000, 0, pid)
        if not handle:
            return None
        try:
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
                if filetime_ticks(exited):
                    return None
                return str(filetime_ticks(created))
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, ValueError, IndexError):
        pass
    return None


def owned_process_pid(profile: dict[str, Any], runtime: dict[str, Any] | None) -> int | None:
    if runtime:
        return runtime["process"].pid
    try:
        # Store beside settings, so isolated test settings never touch real ownership records.
        path = SETTINGS_PATH.parent / "process-ownership.json"
        record = json.loads(path.read_text(encoding="utf-8")).get(profile["id"], {})
        pid = int(record.get("pid", 0))
        if (pid > 0 and record.get("root") == str(Path(profile["path"]).resolve())
                and record.get("identity") and process_identity(pid) == record["identity"]):
            return pid
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def remember_process(profile: dict[str, Any], pid: int) -> None:
    identity = process_identity(pid)
    if not identity:
        return
    with CONFIG_LOCK:
        path = SETTINGS_PATH.parent / "process-ownership.json"
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, dict):
                records = {}
        except (OSError, ValueError):
            records = {}
        records[profile["id"]] = {"pid": pid, "identity": identity, "root": str(Path(profile["path"]).resolve())}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)


def process_belongs_to(pid: int | None, owner: int | None) -> bool:
    if not pid or not owner:
        return False
    if pid == owner:
        return True
    parents = windows_process_snapshot(parents=True) if os.name == "nt" else {}
    visited = set()
    while pid and pid not in visited and len(visited) < 64:
        visited.add(pid)
        try:
            pid = parents.get(pid, 0) if os.name == "nt" else int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return False
        if pid == owner:
            return True
    return False


def process_info(pid: int | None) -> dict[str, Any] | None:
    if not pid:
        return None
    if os.name != "nt":
        status = Path(f"/proc/{pid}/status")
        memory = None
        name = "java"
        if status.exists():
            text = status.read_text(errors="replace")
            match = re.search(r"^VmRSS:\s+(\d+)", text, re.M)
            memory = round(int(match.group(1)) / 1024, 1) if match else None
            name_match = re.search(r"^Name:\s+(.+)", text, re.M)
            name = name_match.group(1) if name_match else name
        return {"pid": pid, "name": name, "memory_mb": memory, "cpu_percent": None, "uptime_seconds": None}

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
                                         ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), ctypes.c_uint32]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    process = kernel32.OpenProcess(0x0410, 0, pid)
    if not process:
        return {"pid": pid, "name": "java.exe", "memory_mb": None, "cpu_percent": None, "uptime_seconds": None}
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        memory = None
        if psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            memory = round(counters.WorkingSetSize / 1024 / 1024, 1)
        creation = FILETIME()
        exit_time = FILETIME()
        kernel_time = FILETIME()
        user_time = FILETIME()
        cpu_percent = None
        uptime_seconds = None
        if kernel32.GetProcessTimes(process, ctypes.byref(creation), ctypes.byref(exit_time),
                                    ctypes.byref(kernel_time), ctypes.byref(user_time)):
            process_ticks = filetime_ticks(kernel_time) + filetime_ticks(user_time)
            wall_now = time.perf_counter()
            with METRICS_LOCK:
                previous = CPU_SAMPLES.get(pid)
                CPU_SAMPLES[pid] = (wall_now, process_ticks)
            if previous and wall_now > previous[0]:
                used_seconds = (process_ticks - previous[1]) / 10_000_000
                cpu_percent = round(max(0.0, min(100.0, used_seconds / (wall_now - previous[0]) /
                                                       max(1, os.cpu_count() or 1) * 100)), 1)
            created_unix = filetime_ticks(creation) / 10_000_000 - 11_644_473_600
            uptime_seconds = max(0, int(time.time() - created_unix))
        return {"pid": pid, "name": "java.exe", "memory_mb": memory,
                "cpu_percent": cpu_percent, "uptime_seconds": uptime_seconds}
    finally:
        kernel32.CloseHandle(process)


def encode_varint(value: int) -> bytes:
    encoded = bytearray()
    value &= 0xFFFFFFFF
    while True:
        part = value & 0x7F
        value >>= 7
        if value:
            part |= 0x80
        encoded.append(part)
        if not value:
            return bytes(encoded)


def recv_varint(sock: socket.socket) -> int:
    value = 0
    for shift in range(0, 35, 7):
        raw = sock.recv(1)
        if not raw:
            raise ConnectionError("连接提前结束")
        current = raw[0]
        value |= (current & 0x7F) << shift
        if not current & 0x80:
            return value
    raise ValueError("无效的 VarInt")


def minecraft_ping(port: int) -> dict[str, Any] | None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.2) as sock:
            host = b"127.0.0.1"
            handshake = encode_varint(0) + encode_varint(763) + encode_varint(len(host)) + host + struct.pack(">H", port) + encode_varint(1)
            sock.sendall(encode_varint(len(handshake)) + handshake)
            sock.sendall(b"\x01\x00")
            recv_varint(sock)
            packet_id = recv_varint(sock)
            if packet_id != 0:
                return None
            length = recv_varint(sock)
            if length < 0 or length > 1024 * 1024:
                return None
            chunks = bytearray()
            while len(chunks) < length:
                chunk = sock.recv(length - len(chunks))
                if not chunk:
                    break
                chunks.extend(chunk)
            value = json.loads(chunks.decode("utf-8"))
            return value if valid_minecraft_status(value) else None
    except (OSError, ValueError, json.JSONDecodeError, ConnectionError):
        return None


def valid_minecraft_status(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    version, players = value.get("version"), value.get("players")
    return (isinstance(version, dict) and isinstance(version.get("name"), str)
            and type(version.get("protocol")) is int and isinstance(players, dict)
            and type(players.get("online")) is int and players["online"] >= 0
            and type(players.get("max")) is int and players["max"] >= 0
            and isinstance(players.get("sample", []), list))


def strip_motd(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [str(value.get("text", ""))]
        parts.extend(strip_motd(item) for item in value.get("extra", []) if item)
        return "".join(parts)
    if isinstance(value, list):
        return "".join(strip_motd(item) for item in value)
    return str(value or "")


def runtime_for(server_id: str) -> dict[str, Any] | None:
    with RUNTIME_LOCK:
        runtime = RUNTIMES.get(server_id)
        if not runtime:
            return None
        return_code = runtime["process"].poll()
        if return_code is not None:
            LAST_EXITS[server_id] = {"code": return_code, "at": now_iso()}
            try:
                runtime["log"].close()
            except OSError:
                pass
            RUNTIMES.pop(server_id, None)
            return None
        return runtime


def server_is_running(profile: dict[str, Any]) -> dict[str, Any]:
    root = Path(profile["path"])
    properties = read_properties(root)
    port = int(properties.get("server-port", "25565") or 25565)
    ping = minecraft_ping(port)
    if not valid_minecraft_status(ping):
        ping = None
    runtime = runtime_for(profile["id"])
    listener = find_listening_pid(port)
    owner = owned_process_pid(profile, runtime)
    listener_owned = process_belongs_to(listener, owner)
    conflict = bool((listener or ping is not None) and not listener_owned)
    pid = listener if listener_owned else owner
    running = owner is not None
    if not listener_owned:
        ping = None
    players = (ping or {}).get("players", {})
    version = (ping or {}).get("version", {})
    detected = detect_launch(root)
    try:
        disk = shutil.disk_usage(root)
        storage = {
            "free_gb": round(disk.free / 1024 / 1024 / 1024, 1),
            "total_gb": round(disk.total / 1024 / 1024 / 1024, 1),
            "used_percent": round((disk.used / disk.total * 100) if disk.total else 0, 1),
        }
    except OSError:
        storage = {"free_gb": None, "total_gb": None, "used_percent": None}
    last_exit = LAST_EXITS.get(profile["id"])
    startup_failure = None
    if not running and last_exit and last_exit.get("code") not in (None, 0):
        startup_failure = diagnose_startup_failure(root)
        if startup_failure is None:
            startup_failure = {
                "severity": "error",
                "code": "process_exit",
                "title": "服务端进程已退出",
                "detail": f"Java 进程在启动完成前退出，退出码为 {last_exit['code']}。",
                "suggestions": ["打开控制台查看退出前的最后日志", "检查最近新增或更新的 Mod / 插件"],
                "source": f"runtime-{profile['id']}.log",
            }
    return {
        "running": running,
        "ready": ping is not None,
        "port": port,
        "process": process_info(pid),
        "version": version.get("name") or detected.get("version") or "未知",
        "protocol": version.get("protocol"),
        "loader": detected.get("loader", "unknown"),
        "motd": strip_motd((ping or {}).get("description")) or properties.get("motd", "Minecraft Server"),
        "players": {
            "online": int(players.get("online", 0)),
            "max": int(players.get("max", properties.get("max-players", 20) or 20)),
            "names": [item.get("name", "") for item in players.get("sample", []) if isinstance(item, dict)],
        },
        "external_address": profile.get("external_address", ""),
        "managed": runtime is not None,
        "online_mode": properties.get("online-mode", "true").lower() == "true",
        "ownership": "managed" if runtime else "recovered" if owner else "none",
        "port_conflict": {"port": port, "pid": listener, "message": f"端口 {port} 被未确认归属的进程占用；不会自动接管或停止该进程。"} if conflict else None,
        "operation": OPERATIONS.get(profile["id"]),
        "active_job": active_server_job(profile["id"]),
        "last_job": last_server_job(profile["id"]),
        "eula": eula_accepted(root),
        "storage": storage,
        "last_exit": last_exit,
        "startup_failure": startup_failure,
    }


def eula_accepted(root: Path) -> bool:
    path = root / "eula.txt"
    if not path.exists():
        return False
    return bool(re.search(r"^\s*eula\s*=\s*true\s*$", path.read_text(errors="replace"), re.I | re.M))


def accept_eula(profile: dict[str, Any]) -> None:
    root = Path(profile["path"])
    path = root / "eula.txt"
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if re.search(r"^\s*eula\s*=", text, re.I | re.M):
        text = re.sub(r"^\s*eula\s*=.*$", "eula=true", text, flags=re.I | re.M)
    else:
        text += "\neula=true\n"
    path.write_text(text, encoding="utf-8")


def collect_jvm_args(root: Path, profile: dict[str, Any]) -> list[str]:
    # Let Java parse its own argument-file syntax (quotes, comments, multiple flags).
    # Explicit panel memory arguments come last and override file memory settings.
    args = []
    plan = bootstrapper.folder_plan(root)
    if plan.get("adapter") == "gtnh" and Path(profile.get("launch_target") or plan["launch_target"]).name.casefold() == "lwjgl3ify-forgepatches.jar":
        args.extend(f"@{safe_target(root, name)}" for name in plan["argfiles"])
        args.extend(plan["jvm_args"])
    path = root / "user_jvm_args.txt"
    if path.exists():
        if parsed_java_major(java_version_text(profile)) == 8:
            # Java 8 has no native @argfile support. Keep quoted Windows paths intact.
            lexer = shlex.shlex(path.read_text(encoding="utf-8-sig"), posix=True)
            lexer.whitespace_split = True
            lexer.escape = ""
            args.extend(lexer)
        else:
            args.append(f"@{path.resolve()}")
    args.extend([f"-Xms{profile.get('xms', '2G')}", f"-Xmx{profile.get('xmx', '6G')}"])
    return args


def java_executable(profile: dict[str, Any]) -> str:
    value = str(profile.get("java") or "java").strip().strip('"')
    path = Path(value)
    if path.is_file():
        return str(path)
    found = shutil.which(value)
    if found:
        return found
    raise FileNotFoundError(f"找不到 Java：{value}")


def build_launch(profile: dict[str, Any]) -> tuple[list[str], str]:
    root = Path(profile["path"])
    detected = detect_launch(root)
    mode = profile.get("launch_mode", "auto")
    target_text = str(profile.get("launch_target") or "")
    if mode == "auto":
        mode = detected["mode"]
        target_text = detected["target"]
    target = safe_target(root, target_text)
    if mode == "script":
        suffix = target.suffix.lower()
        if suffix in {".bat", ".cmd"}:
            if os.name != "nt":
                raise RuntimeError("BAT/CMD 启动脚本只能在 Windows 上运行")
            return ["cmd.exe", "/d", "/s", "/c", str(target)], f"脚本 {target.name}"
        if suffix == ".sh":
            shell = shutil.which("bash") or shutil.which("sh")
            if not shell:
                raise FileNotFoundError("找不到 bash/sh")
            return [shell, str(target)], f"脚本 {target.name}"
        raise ValueError("仅支持 BAT、CMD 或 SH 启动脚本")
    java = java_executable(profile)
    jvm_args = collect_jvm_args(root, profile)
    if mode == "forge_args":
        relative = target.relative_to(root).as_posix()
        return [java, *jvm_args, f"@{relative}", "nogui"], f"参数文件 {relative}"
    if mode == "jar":
        if "installer" in target.name.casefold():
            raise ValueError("这是核心安装器，不是服务端；请启用一键准备后启动")
        if target.suffix.lower() != ".jar":
            raise ValueError("JAR 启动方式必须选择 .jar 文件")
        return [java, *jvm_args, "-jar", str(target), "nogui"], f"JAR {target.name}"
    raise ValueError("没有检测到可用的启动文件，请在启动配置中手动选择")


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", ctypes.c_int), ("wRepeatCount", ctypes.c_ushort),
        ("wVirtualKeyCode", ctypes.c_ushort), ("wVirtualScanCode", ctypes.c_ushort),
        ("UnicodeChar", ctypes.c_wchar), ("dwControlKeyState", ctypes.c_uint),
    ]


class EVENT_UNION(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("padding", ctypes.c_byte * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", ctypes.c_ushort), ("Event", EVENT_UNION)]


def _send_console_native(pid: int, line: str) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AttachConsole.argtypes = [ctypes.c_uint32]
    kernel32.AttachConsole.restype = ctypes.c_int
    kernel32.FreeConsole.argtypes = []
    kernel32.FreeConsole.restype = ctypes.c_int
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                     ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.WriteConsoleInputW.argtypes = [ctypes.c_void_p, ctypes.POINTER(INPUT_RECORD), ctypes.c_uint32,
                                            ctypes.POINTER(ctypes.c_uint32)]
    kernel32.WriteConsoleInputW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(pid):
        raise OSError(ctypes.get_last_error(), "无法连接服务端控制台")
    handle = None
    try:
        handle = kernel32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "无法打开服务端控制台输入")
        text = line + "\r"
        records = (INPUT_RECORD * (len(text) * 2))()
        for index, char in enumerate(text):
            vk = 13 if char == "\r" else ord(char.upper())
            records[index * 2].EventType = 1
            records[index * 2].Event.KeyEvent = KEY_EVENT_RECORD(1, 1, vk, 0, char, 0)
            records[index * 2 + 1].EventType = 1
            records[index * 2 + 1].Event.KeyEvent = KEY_EVENT_RECORD(0, 1, vk, 0, char, 0)
        written = ctypes.c_uint32()
        if not kernel32.WriteConsoleInputW(handle, records, len(records), ctypes.byref(written)):
            raise OSError(ctypes.get_last_error(), "发送控制台指令失败")
    finally:
        if handle and handle != ctypes.c_void_p(-1).value:
            kernel32.CloseHandle(handle)
        kernel32.FreeConsole()


def validate_command(line: str) -> str:
    line = str(line).strip()
    if not SAFE_COMMAND.fullmatch(line):
        raise ValueError("指令不能为空、不能换行，且最多 500 个字符")
    return line


def send_command(profile: dict[str, Any], line: str, maintenance: bool = False) -> None:
    lock = action_lock(profile["id"])
    if not lock.acquire(blocking=False):
        raise RuntimeError("该实例正在执行维护操作，请等待完成")
    try:
        if active_server_job(profile["id"]) and not maintenance:
            raise RuntimeError("该实例正在备份或恢复，暂不能发送指令")
        _send_owned_command(profile, line)
    finally:
        lock.release()


def _send_owned_command(profile: dict[str, Any], line: str) -> None:
    line = validate_command(line)
    runtime = runtime_for(profile["id"])
    if runtime and runtime["process"].stdin:
        try:
            runtime["process"].stdin.write(line + "\n")
            runtime["process"].stdin.flush()
            return
        except (OSError, BrokenPipeError):
            pass
    pid = owned_process_pid(profile, runtime)
    if not pid:
        raise RuntimeError("实例未由面板托管，无法安全发送指令；请从原控制台正常停服后再由面板启动")
    if os.name != "nt":
        raise RuntimeError("此服务器不是由面板启动，无法接管它的标准输入")
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--send-console", str(pid), line],
        capture_output=True, text=True, errors="replace", timeout=12,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip() or "控制台指令发送失败")


def action_lock(server_id: str) -> Any:
    with RUNTIME_LOCK:
        return ACTION_LOCKS.setdefault(server_id, threading.RLock())


def active_server_job(server_id: str) -> dict[str, Any] | None:
    with JOBS_LOCK:
        return next((dict(job) for job in JOBS.values() if job.get("server_id") == server_id
                     and job.get("state") in {"queued", "running"}), None)


def last_server_job(server_id: str) -> dict[str, Any] | None:
    with JOBS_LOCK:
        return next((dict(job) for job in reversed(list(JOBS.values())) if job.get("server_id") == server_id
                     and job.get("type") in {"backup", "restore", "start", "archive_import", "ftb_import"}), None)


@contextmanager
def server_operation(profile: dict[str, Any], operation: str, job_id: str | None = None):
    server_id = profile["id"]
    lock = action_lock(server_id)
    if not lock.acquire(blocking=False):
        raise RuntimeError("该实例已有操作正在进行，请等待完成后重试")
    previous = OPERATIONS.get(server_id)
    try:
        job = active_server_job(server_id)
        if job and job.get("id") != job_id:
            raise RuntimeError("该实例正在备份或恢复，请等待任务完成")
        OPERATIONS[server_id] = operation
        yield
    finally:
        if previous:
            OPERATIONS[server_id] = previous
        else:
            OPERATIONS.pop(server_id, None)
        lock.release()


def ensure_server_write_access(root: Path) -> None:
    """Fail before launching Java when the panel cannot update the server files."""
    if not root.is_dir():
        raise FileNotFoundError(f"服务端目录不存在：{root}")

    probe = root / f".ripple-write-test-{os.getpid()}-{uuid.uuid4().hex}.tmp"
    try:
        probe.write_bytes(b"")
    except PermissionError as exc:
        raise RuntimeError(
            f"面板没有服务端目录的写入权限：{root}。请关闭当前面板，再双击“启动面板.bat”；"
            "如果仍然失败，请右键该文件并选择“以管理员身份运行”。"
        ) from exc
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass

    for target in (root / "logs" / "latest.log", root / "logs" / "debug.log", root / "config" / "fml.toml"):
        if not target.is_file():
            continue
        try:
            with target.open("a", encoding="utf-8", errors="replace"):
                pass
        except PermissionError as exc:
            raise RuntimeError(
                f"服务端文件正被占用或不可写：{target}。请确认旧服务端已经完全退出，"
                "并重新启动本地面板。"
            ) from exc


def start_server(profile: dict[str, Any], job_id: str | None = None, progress=None) -> str:
    with server_operation(profile, "starting", job_id):
        status = server_is_running(profile)
        if status.get("port_conflict"):
            raise RuntimeError(status["port_conflict"]["message"])
        if status["running"]:
            return "服务器已经在运行"
        root = Path(profile["path"])
        if not eula_accepted(root):
            raise RuntimeError("尚未同意 Minecraft EULA，请先在启动配置中勾选同意")
        ensure_server_write_access(root)
        if profile.get("auto_setup", True):
            candidates = [str(profile.get("java") or "java"), *[r["path"] for r in discover_java_runtimes()]]
            prepared = bootstrapper.prepare(root, candidates, DATA_ROOT / "bootstrap-cache", progress or (lambda message: None))
            if prepared:
                profile = update_profile({"server_id": profile["id"], "profile": prepared})
        command, description = build_launch(profile)
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        runtime_log = DATA_ROOT / f"runtime-{profile['id']}.log"
        log_handle = runtime_log.open("a", encoding="utf-8", errors="replace", buffering=1)
        log_handle.write(f"\n[{now_iso()}] Lodestar 启动：{description}\n")
        creationflags = 0
        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdin=subprocess.PIPE,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                **popen_kwargs,
            )
        except Exception:
            log_handle.close()
            raise
        with RUNTIME_LOCK:
            RUNTIMES[profile["id"]] = {"process": process, "log": log_handle, "started_at": now_iso()}
        try:
            remember_process(profile, process.pid)
        except OSError as exc:
            log_handle.write(f"[Lodestar] 无法持久保存进程身份：{exc}\n")
        return f"已通过{description}启动；整合包首次加载可能需要几分钟"


def stop_server(profile: dict[str, Any], job_id: str | None = None) -> str:
    with server_operation(profile, "stopping", job_id):
        status = server_is_running(profile)
        if not status["running"]:
            return "服务器当前没有运行"
        send_command(profile, "stop", maintenance=bool(job_id))
        deadline = time.time() + 90
        while time.time() < deadline:
            if not server_is_running(profile)["running"]:
                return "服务器已正常保存并停止"
            time.sleep(1)
        raise TimeoutError("等待停服超时；可查看日志，必要时使用强制停止")


def force_stop_server(profile: dict[str, Any]) -> str:
    with server_operation(profile, "stopping"):
        status = server_is_running(profile)
        if not status["running"]:
            return "服务器当前没有运行"
        pid = owned_process_pid(profile, runtime_for(profile["id"]))
        if not pid:
            raise RuntimeError("未找到服务器进程 PID")
        if os.name == "nt":
            completed = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                                       text=True, errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "强制停止失败")
        else:
            os.killpg(os.getpgid(pid), 15)
        return "已强制停止进程；本次可能没有完整保存世界"


def restart_server(profile: dict[str, Any]) -> str:
    with server_operation(profile, "restarting"):
        status = server_is_running(profile)
        if status["running"]:
            stop_server(profile)
        return start_server(profile)


def read_tail(path: Path, max_bytes: int = 300_000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        data = handle.read()
    # Most modern servers write UTF-8, while some Windows Forge packs inherit
    # the system GBK code page. Preserve useful Chinese mod names in both cases.
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("gb18030", errors="replace")
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def combined_logs(profile: dict[str, Any]) -> dict[str, str]:
    root = Path(profile["path"])
    latest = read_tail(root / "logs" / "latest.log")
    runtime = read_tail(DATA_ROOT / f"runtime-{profile['id']}.log", 120_000)
    if latest:
        return {"source": "logs/latest.log", "text": latest}
    return {"source": "面板运行日志", "text": runtime or "暂无日志。启动服务器后，这里会自动刷新。"}


def validate_content_kind(kind: str) -> str:
    if kind not in {"mods", "plugins"}:
        raise ValueError("内容类型必须是 mods 或 plugins")
    return kind


def safe_mod_name(name: str) -> str:
    name = unquote(str(name))
    if Path(name).name != name or not SAFE_FILE.fullmatch(name) or not name.lower().endswith(".jar"):
        raise ValueError("Mod 文件名无效")
    return name


def mod_metadata(path: Path) -> dict[str, str]:
    result = {"display_name": path.stem, "mod_id": "", "version": ""}
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "fabric.mod.json" in names:
                raw = archive.read("fabric.mod.json")[:1_000_000]
                data = json.loads(raw.decode("utf-8", errors="replace"))
                result.update(display_name=str(data.get("name") or path.stem), mod_id=str(data.get("id") or ""),
                              version=str(data.get("version") or ""))
            elif "plugin.yml" in names or "paper-plugin.yml" in names:
                plugin_name = "paper-plugin.yml" if "paper-plugin.yml" in names else "plugin.yml"
                text = archive.read(plugin_name)[:1_000_000].decode("utf-8", errors="replace")
                for key, output in (("name", "display_name"), ("main", "mod_id"), ("version", "version")):
                    match = re.search(rf"(?mi)^\s*{key}\s*:\s*[\"']?([^\r\n\"']+)", text)
                    if match:
                        result[output] = match.group(1).strip()
            else:
                toml_name = "META-INF/neoforge.mods.toml" if "META-INF/neoforge.mods.toml" in names else "META-INF/mods.toml"
                if toml_name in names:
                    text = archive.read(toml_name)[:1_000_000].decode("utf-8", errors="replace")
                    for key, output in (("displayName", "display_name"), ("modId", "mod_id"), ("version", "version")):
                        match = re.search(rf"(?m)^\s*{key}\s*=\s*[\"']([^\"']+)", text)
                        if match:
                            result[output] = match.group(1)
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError):
        pass
    return result


def record_metric(profile: dict[str, Any], status: dict[str, Any]) -> None:
    process = status.get("process") or {}
    point = {
        "at": now_iso(),
        "running": bool(status.get("running")),
        "ready": bool(status.get("ready")),
        "cpu": round(float(process.get("cpu_percent") or 0), 2),
        "memory_mb": round(float(process.get("memory_mb") or 0), 2),
        "players": int((status.get("players") or {}).get("online") or 0),
    }
    with METRICS_LOCK:
        history = METRIC_HISTORY.setdefault(profile["id"], [])
        if history and history[-1]["at"] == point["at"] and history[-1]["running"] == point["running"]:
            history[-1] = point
        else:
            history.append(point)
        del history[:-720]


def metric_history(profile: dict[str, Any]) -> list[dict[str, Any]]:
    with METRICS_LOCK:
        return [dict(point) for point in METRIC_HISTORY.get(profile["id"], [])]


def content_locations(root: Path, kind: str) -> list[tuple[str, Path]]:
    kind = validate_content_kind(kind)
    singular = "mod" if kind == "mods" else "plugin"
    return [
        ("enabled", root / kind),
        ("disabled", root / f"disabled_{kind}"),
        ("trash", root / ".ripple-panel" / f"{singular}-trash"),
    ]


def list_mods(profile: dict[str, Any], kind: str = "mods") -> list[dict[str, Any]]:
    root = Path(profile["path"])
    result: list[dict[str, Any]] = []
    for state, folder in content_locations(root, kind):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.jar"), key=lambda item: item.name.casefold()):
            metadata = mod_metadata(path)
            result.append({
                "name": path.name,
                "state": state,
                "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
                "modified": dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                **metadata,
            })
    return result


def mod_path(profile: dict[str, Any], state: str, name: str, kind: str = "mods") -> Path:
    root = Path(profile["path"])
    folders = dict(content_locations(root, kind))
    if state not in folders:
        raise ValueError("Mod 状态无效")
    path = folders[state] / safe_mod_name(name)
    if not path.is_file():
        raise FileNotFoundError(f"找不到 Mod：{name}")
    return path


def unique_destination(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    return folder / f"{stem}-{dt.datetime.now():%Y%m%d-%H%M%S}{suffix}"


def change_mod_state(profile: dict[str, Any], action: str, state: str, name: str, kind: str = "mods") -> str:
    kind = validate_content_kind(kind)
    source = mod_path(profile, state, name, kind)
    root = Path(profile["path"])
    disabled_folder = root / f"disabled_{kind}"
    trash_folder = root / ".ripple-panel" / ("mod-trash" if kind == "mods" else "plugin-trash")
    if action == "enable":
        destination = unique_destination(root / kind, source.name)
        label = "启用"
    elif action == "disable":
        destination = unique_destination(disabled_folder, source.name)
        label = "停用"
    elif action == "remove":
        destination = unique_destination(trash_folder, source.name)
        label = "移入回收站"
    elif action == "restore":
        destination = unique_destination(disabled_folder, source.name)
        label = "恢复为停用状态"
    else:
        raise ValueError("未知的 Mod 操作")
    shutil.move(str(source), str(destination))
    return f"已{label} {source.name}；运行中的服务器需要重启才会生效"


def save_uploaded_mod(profile: dict[str, Any], name: str, stream: Any, length: int, kind: str = "mods") -> str:
    kind = validate_content_kind(kind)
    name = safe_mod_name(name)
    if length <= 0 or length > MAX_MOD_BYTES:
        raise ValueError("Mod 文件为空或超过 2 GB 限制")
    folder = Path(profile["path"]) / kind
    folder.mkdir(exist_ok=True)
    destination = folder / name
    if destination.exists():
        raise FileExistsError(f"{kind} 中已经存在 {name}")
    temporary = folder / f".{name}.{uuid.uuid4().hex}.uploading"
    remaining = length
    try:
        with temporary.open("wb") as handle:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ConnectionError("上传连接提前结束")
                handle.write(chunk)
                remaining -= len(chunk)
        if not zipfile.is_zipfile(temporary):
            raise ValueError("上传的文件不是有效的 JAR/ZIP")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return f"已添加 {name}；请确认它与服务端版本和加载器兼容，然后重启服务器"


TEXT_FILE_NAMES = {"eula.txt", "ops.json", "whitelist.json", "banned-ips.json", "banned-players.json"}
TEXT_FILE_SUFFIXES = {
    ".txt", ".log", ".json", ".json5", ".properties", ".toml", ".yml", ".yaml", ".conf", ".cfg", ".ini",
    ".xml", ".md", ".bat", ".cmd", ".sh", ".ps1", ".js", ".ts", ".java", ".mcmeta", ".snbt", ".lang",
}


def server_file_path(profile: dict[str, Any], relative: str, *, must_exist: bool = True) -> Path:
    root = Path(profile["path"]).resolve()
    value = unquote(str(relative or "")).strip().replace("\\", "/").strip("/")
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if any(part == ".." or not SAFE_ARCHIVE_NAME.fullmatch(part) for part in parts):
        raise ValueError("文件路径无效")
    candidate = (root.joinpath(*parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("文件路径超出服务端目录") from exc
    if parts and parts[0].casefold() == ".ripple-panel":
        raise ValueError("面板内部目录不能通过文件管理器访问")
    if must_exist and not candidate.exists():
        raise FileNotFoundError("文件或目录不存在")
    return candidate


def relative_server_path(root: Path, path: Path) -> str:
    return path.relative_to(root.resolve()).as_posix()


def list_server_files(profile: dict[str, Any], relative: str = "") -> dict[str, Any]:
    root = Path(profile["path"]).resolve()
    folder = server_file_path(profile, relative)
    if not folder.is_dir():
        raise ValueError("当前路径不是目录")
    entries: list[dict[str, Any]] = []
    for path in sorted(folder.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
        if path.name == ".ripple-panel":
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append({
            "name": path.name,
            "path": relative_server_path(root, path),
            "type": "directory" if path.is_dir() else "file",
            "size": 0 if path.is_dir() else stat.st_size,
            "modified": dt.datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
            "editable": path.is_file() and stat.st_size <= MAX_TEXT_FILE_BYTES
                        and (path.name.casefold() in TEXT_FILE_NAMES or path.suffix.casefold() in TEXT_FILE_SUFFIXES),
        })
    current = relative_server_path(root, folder) if folder != root else ""
    parent = Path(current).parent.as_posix() if current else ""
    if parent == ".":
        parent = ""
    return {"path": current, "parent": parent, "entries": entries}


def read_server_text_file(profile: dict[str, Any], relative: str) -> dict[str, Any]:
    path = server_file_path(profile, relative)
    if not path.is_file():
        raise ValueError("当前路径不是文件")
    if path.stat().st_size > MAX_TEXT_FILE_BYTES:
        raise ValueError("文件超过 4 MB，请下载后使用本地编辑器")
    if path.name.casefold() not in TEXT_FILE_NAMES and path.suffix.casefold() not in TEXT_FILE_SUFFIXES:
        raise ValueError("该文件类型不支持网页编辑")
    raw = path.read_bytes()
    try:
        content = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise ValueError("文件不是 UTF-8 编码，请下载后使用本地编辑器，避免转换损坏内容") from exc
    return {"path": relative_server_path(Path(profile["path"]), path), "server_id": profile["id"],
            "content": content, "size": len(raw),
            "revision": hashlib.sha256(raw).hexdigest()}


def save_server_text_file(profile: dict[str, Any], relative: str, content: Any, revision: str | None = None) -> str:
    with server_operation(profile, "editing"):
        return _save_server_text_locked(profile, relative, content, revision)


def _save_server_text_locked(profile: dict[str, Any], relative: str, content: Any, revision: str | None) -> str:
    path = server_file_path(profile, relative)
    if not path.is_file():
        raise ValueError("当前路径不是文件")
    if path.name.casefold() not in TEXT_FILE_NAMES and path.suffix.casefold() not in TEXT_FILE_SUFFIXES:
        raise ValueError("该文件类型不支持网页编辑")
    original = path.read_bytes()
    if revision is not None and hashlib.sha256(original).hexdigest() != revision:
        raise FileExistsError("文件已被其他操作修改；请重新打开并核对内容后再保存")
    text = str(content)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if b"\r\n" in original or path.suffix.lower() in {".bat", ".cmd"}:
        text = text.replace("\n", "\r\n")
    encoded = text.encode("utf-8")
    if original.startswith(b"\xef\xbb\xbf"):
        encoded = b"\xef\xbb\xbf" + encoded
    if len(encoded) > MAX_TEXT_FILE_BYTES:
        raise ValueError("文本内容超过 4 MB")
    root = Path(profile["path"])
    history = root / ".ripple-panel" / "file-history" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    history_file = history / path.relative_to(root)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, history_file)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.saving")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return f"已保存 {path.name}；修改前版本已进入文件历史"


def file_manager_action(profile: dict[str, Any], action: str, relative: str, name: str = "") -> str:
    root = Path(profile["path"]).resolve()
    target = server_file_path(profile, relative)
    if action == "mkdir":
        folder_name = str(name).strip()
        if not SAFE_ARCHIVE_NAME.fullmatch(folder_name) or folder_name in {".", ".."}:
            raise ValueError("文件夹名称无效")
        destination = target / folder_name
        destination.mkdir(parents=False, exist_ok=False)
        return f"已创建文件夹 {folder_name}"
    if target == root:
        raise ValueError("不能操作服务端根目录")
    if action == "rename":
        new_name = str(name).strip()
        if not SAFE_ARCHIVE_NAME.fullmatch(new_name) or new_name in {".", ".."}:
            raise ValueError("新名称无效")
        destination = target.with_name(new_name)
        if destination.exists():
            raise FileExistsError("同名文件或目录已经存在")
        target.rename(destination)
        return f"已重命名为 {new_name}"
    if action == "trash":
        trash = root / ".ripple-panel" / "file-trash" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        trash.mkdir(parents=True, exist_ok=False)
        destination = trash / target.name
        shutil.move(str(target), str(destination))
        manifest = {"original_path": relative_server_path(root, target), "trashed_at": now_iso(), "stored": target.name}
        (trash / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"已将 {target.name} 移入面板回收站"
    raise ValueError("未知文件操作")


def save_uploaded_file(profile: dict[str, Any], relative: str, stream: Any, length: int) -> str:
    if length <= 0 or length > MAX_FILE_UPLOAD_BYTES:
        raise ValueError("文件为空或超过 4 GB 限制")
    destination = server_file_path(profile, relative, must_exist=False)
    if destination.exists():
        raise FileExistsError(f"目标位置已经存在 {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.uploading")
    remaining = length
    try:
        with temporary.open("wb") as handle:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ConnectionError("上传连接提前结束")
                handle.write(chunk)
                remaining -= len(chunk)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return f"已上传 {destination.name}"


def read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def player_data(profile: dict[str, Any]) -> dict[str, Any]:
    root = Path(profile["path"])
    return {
        "whitelist": read_json_list(root / "whitelist.json"),
        "operators": read_json_list(root / "ops.json"),
        "banned_players": read_json_list(root / "banned-players.json"),
        "banned_ips": read_json_list(root / "banned-ips.json"),
    }


def player_action(profile: dict[str, Any], action: str, name: str, reason: str = "") -> str:
    if not PLAYER_NAME.fullmatch(name):
        raise ValueError("玩家名只能包含字母、数字和下划线，长度为 1–16")
    commands = {
        "whitelist_add": f"whitelist add {name}",
        "whitelist_remove": f"whitelist remove {name}",
        "op": f"op {name}",
        "deop": f"deop {name}",
        "pardon": f"pardon {name}",
        "kick": f"kick {name}",
        "ban": f"ban {name}",
    }
    if action not in commands:
        raise ValueError("未知玩家操作")
    safe_reason = re.sub(r"[\r\n\x00-\x1f]", " ", str(reason)).strip()[:120]
    command = commands[action] + (f" {safe_reason}" if safe_reason and action in {"kick", "ban"} else "")
    send_command(profile, command)
    return f"已发送玩家操作：{action} {name}"


def backup_folder(profile: dict[str, Any]) -> Path:
    return Path(profile["path"]) / "panel-backups"


def list_backups(profile: dict[str, Any]) -> list[dict[str, Any]]:
    folder = backup_folder(profile)
    if not folder.is_dir():
        return []
    return [
        {
            "name": path.name,
            "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
            "modified": dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            **backup_summary(path),
        }
        for path in sorted(folder.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    ]


def backup_summary(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(BACKUP_MANIFEST)
            if info.file_size > 32 * 1024 * 1024:
                raise ValueError("清单过大")
            manifest = json.loads(archive.read(info))
            if manifest.get("format") == 1 and manifest.get("complete") is True and isinstance(manifest.get("files"), list):
                return {"verified": True, "worlds": manifest.get("worlds", []), "file_count": len(manifest["files"])}
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, AttributeError):
        pass
    return {"verified": False, "worlds": [], "file_count": None}


def backup_worlds(root: Path) -> list[Path]:
    root = root.resolve()
    name = read_properties(root).get("level-name", "world") or "world"
    primary = root / name
    candidates = [primary, root / f"{name}_nether", root / f"{name}_the_end"]
    candidates.extend(path for path in root.iterdir() if path.is_dir() and (path / "level.dat").is_file())
    worlds = []
    for path in candidates:
        resolved = path.resolve()
        relative = resolved.relative_to(root)
        if not relative.parts or relative.parts[0].casefold() in {".ripple-panel", "panel-backups"}:
            raise ValueError("世界目录必须位于服务端目录内，且不能指向面板数据")
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError(f"世界目录不支持链接：{path.name}")
        if path.is_dir() and resolved not in worlds:
            worlds.append(resolved)
    if not primary.is_dir():
        raise FileNotFoundError(f"找不到世界目录 {name}；不会创建只有配置的世界备份")
    return worlds


def hash_stream(stream: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def validate_backup_archive(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str]]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_FILES:
        raise ValueError("备份文件数量超过安全上限")
    try:
        manifest_info = archive.getinfo(BACKUP_MANIFEST)
        if manifest_info.file_size > 32 * 1024 * 1024:
            raise ValueError("备份清单超过安全上限")
        manifest = json.loads(archive.read(manifest_info))
    except KeyError as exc:
        raise ValueError("旧备份缺少完整性清单；文件已保留，可下载人工核验，但不能自动恢复") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("complete") is not True:
        raise ValueError("备份未完成或清单格式不支持")
    entries = manifest.get("files")
    worlds = manifest.get("worlds")
    if not isinstance(entries, list) or not entries or not isinstance(worlds, list) or not worlds:
        raise ValueError("备份清单缺少世界或文件信息")
    expected = {entry["path"]: entry for entry in entries}
    if len(expected) != len(entries):
        raise ValueError("备份清单包含重复文件")
    normalized = []
    seen = set()
    total = 0
    for info in infos:
        member = normalized_archive_member(info)
        if member.casefold() in seen or info.is_dir() or archive_member_is_symlink(info):
            raise ValueError("备份包含重复路径、目录项或不支持的链接")
        seen.add(member.casefold())
        if member == BACKUP_MANIFEST:
            continue
        if member.split("/", 1)[0].casefold() in {".ripple-panel", "panel-backups"}:
            raise ValueError("备份包含面板内部文件")
        if member not in BACKUP_EXTRAS and not any(isinstance(world, str) and member.startswith(world.rstrip("/") + "/") for world in worlds):
            raise ValueError(f"备份文件不在声明的世界范围内：{member}")
        entry = expected.get(member)
        if not isinstance(entry, dict) or entry.get("size") != info.file_size:
            raise ValueError(f"备份内容与清单不一致：{member}")
        total += info.file_size
        if total > MAX_ARCHIVE_EXPANDED_BYTES:
            raise ValueError("备份解压体积超过安全上限")
        normalized.append((info, member))
    if {member for _, member in normalized} != set(expected):
        raise ValueError("备份缺少清单中的文件")
    for info, member in normalized:
        with archive.open(info) as source:
            if hash_stream(source) != expected[member].get("sha256"):
                raise ValueError(f"备份校验失败：{member}")
    return normalized


def log_positions(profile: dict[str, Any]) -> dict[Path, int]:
    paths = [Path(profile["path"]) / "logs" / "latest.log", DATA_ROOT / f"runtime-{profile['id']}.log"]
    return {path: path.stat().st_size if path.exists() else 0 for path in paths}


def wait_for_save_ack(profile: dict[str, Any], positions: dict[Path, int], pattern: str, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for path, offset in positions.items():
            try:
                with path.open("rb") as handle:
                    handle.seek(offset)
                    text = handle.read(1024 * 1024).decode("utf-8", errors="replace")
                if re.search(pattern, text, re.I):
                    return
            except OSError:
                continue
        time.sleep(0.25)
    raise TimeoutError("未收到服务端保存完成确认；请检查控制台，或正常停服后创建离线备份")


def set_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(changes)


def create_backup_worker(profile: dict[str, Any], job_id: str) -> None:
    try:
        with server_operation(profile, "backup", job_id):
            _create_backup_locked(profile, job_id)
    except Exception as exc:
        set_job(job_id, state="error", message=str(exc))


def _create_backup_locked(profile: dict[str, Any], job_id: str) -> None:
    root = Path(profile["path"])
    running = False
    save_disabled = False
    temporary = None
    completed_message = None
    try:
        status = server_is_running(profile)
        if status.get("port_conflict"):
            raise RuntimeError("无法确认占用端口的服务端归属；请从原控制台停服后备份")
        running = status["running"]
        set_job(job_id, state="running", message="正在让服务器保存世界…")
        if running:
            positions = log_positions(profile)
            save_disabled = True
            send_command(profile, "save-off", maintenance=True)
            send_command(profile, "save-all flush", maintenance=True)
            wait_for_save_ack(profile, positions, r"Saved the (?:game|world)|保存了游戏|已保存世界")
        sources = backup_worlds(root)
        folder = backup_folder(profile)
        folder.mkdir(exist_ok=True)
        destination = folder / f"backup-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.zip"
        temporary = destination.with_suffix(".partial")
        set_job(job_id, message="正在压缩世界并生成校验清单…")
        files = []
        for source in sources:
            for path in source.rglob("*"):
                path.resolve().relative_to(root.resolve())
                if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                    raise ValueError(f"世界中包含不支持的链接：{path.name}")
                if path.is_file():
                    files.append(path)
        if not files:
            raise ValueError("世界目录中没有可备份文件；不会创建只有配置的世界备份")
        files.extend(root / name for name in sorted(BACKUP_EXTRAS) if (root / name).is_file())
        files = list(dict.fromkeys(files))
        if len(files) + 1 > MAX_ARCHIVE_FILES:
            raise ValueError("世界文件数量超过备份安全上限")
        expanded_size = sum(path.stat().st_size for path in files)
        if expanded_size > MAX_ARCHIVE_EXPANDED_BYTES:
            raise ValueError("世界大小超过备份安全上限")
        if shutil.disk_usage(folder).free < expanded_size + 4 * 1024 * 1024:
            raise RuntimeError("磁盘空间不足以安全创建备份，请先释放空间或将备份复制到其他磁盘")
        manifest = {"format": 1, "complete": True, "server_id": profile["id"], "created_at": now_iso(),
                    "worlds": [source.relative_to(root.resolve()).as_posix() for source in sources], "files": []}
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
            for path in files:
                member = path.resolve().relative_to(root.resolve()).as_posix()
                with path.open("rb") as source:
                    checksum = hash_stream(source)
                manifest["files"].append({"path": member, "size": path.stat().st_size, "sha256": checksum})
                archive.write(path, member)
            archive.writestr(BACKUP_MANIFEST, json.dumps(manifest, ensure_ascii=False))
        with zipfile.ZipFile(temporary) as archive:
            validate_backup_archive(archive)
        os.replace(temporary, destination)
        keep = int(profile.get("auto_backup_keep", 10))
        backups = sorted(folder.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
        trash = folder / ".trash"
        for old in backups[keep:]:
            trash.mkdir(exist_ok=True)
            shutil.move(str(old), str(unique_destination(trash, old.name)))
        completed_message = f"备份完成：{destination.name}"
        if save_disabled:
            set_job(job_id, message="归档已完成，正在确认恢复自动保存…")
    except Exception as exc:
        set_job(job_id, state="error", message=str(exc))
    finally:
        if save_disabled:
            try:
                positions = log_positions(profile)
                send_command(profile, "save-on", maintenance=True)
                wait_for_save_ack(profile, positions, r"Automatic saving is now enabled|自动保存.*(?:开启|启用)", timeout=15)
            except Exception as exc:
                completed_message = None
                set_job(job_id, state="error", message=f"需要处理：未确认恢复自动保存，请在控制台执行 save-on。{exc}")
        if temporary and temporary.exists():
            temporary.unlink()
    if completed_message:
        set_job(job_id, state="done", progress=100, message=completed_message)
        with JOBS_LOCK:
            scheduled = bool(JOBS.get(job_id, {}).get("scheduled"))
        if scheduled:
            scheduler_state = read_scheduler_state()
            scheduler_state[f"backup_success:{profile['id']}"] = now_iso()
            write_scheduler_state(scheduler_state)


def start_backup(profile: dict[str, Any], scheduled: bool = False) -> dict[str, Any]:
    with server_operation(profile, "backup"), JOBS_LOCK:
        existing = next((job for job in JOBS.values() if job.get("server_id") == profile["id"] and job.get("state") in {"queued", "running"}), None)
        if existing:
            raise ValueError("该服务端已经有备份任务正在进行")
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "server_id": profile["id"], "type": "backup", "state": "queued",
               "progress": 0, "message": "备份任务已排队", "created_at": now_iso(), "scheduled": scheduled}
        JOBS[job_id] = job
    threading.Thread(target=create_backup_worker, args=(profile.copy(), job_id), daemon=True).start()
    return job


def remove_backup(profile: dict[str, Any], name: str) -> str:
    if Path(name).name != name or not SAFE_FILE.fullmatch(name) or not name.lower().endswith(".zip"):
        raise ValueError("备份文件名无效")
    source = backup_folder(profile) / name
    if not source.is_file():
        raise FileNotFoundError("找不到备份")
    trash = backup_folder(profile) / ".trash"
    with server_operation(profile, "backup_remove"):
        shutil.move(str(source), str(unique_destination(trash, source.name)))
    return "备份已移入回收站"


def validate_backup_name(profile: dict[str, Any], name: str) -> Path:
    if Path(name).name != name or not SAFE_FILE.fullmatch(name) or not name.lower().endswith(".zip"):
        raise ValueError("备份文件名无效")
    path = backup_folder(profile) / name
    if not path.is_file():
        raise FileNotFoundError("找不到备份")
    return path


def restore_backup_worker(profile: dict[str, Any], name: str, job_id: str) -> None:
    try:
        with server_operation(profile, "restore", job_id):
            _restore_backup_locked(profile, name, job_id)
    except Exception as exc:
        set_job(job_id, state="error", message=str(exc), error=str(exc))


def _restore_backup_locked(profile: dict[str, Any], name: str, job_id: str) -> None:
    root = Path(profile["path"]).resolve()
    backup = validate_backup_name(profile, name)
    rollback = root / ".ripple-panel" / "restore-history" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    failed = root / ".ripple-panel" / "restore-failed" / job_id
    moved: list[tuple[Path, Path]] = []
    restored_top: set[str] = set()
    try:
        status = server_is_running(profile)
        if status["running"] or status.get("port_conflict"):
            raise ValueError("恢复备份前必须先停止服务器")
        set_job(job_id, state="running", progress=5, message="正在检查备份完整性…")
        with zipfile.ZipFile(backup) as archive:
            normalized = validate_backup_archive(archive)
            total = sum(info.file_size for info, _ in normalized)
            if shutil.disk_usage(root).free < total + 4 * 1024 * 1024:
                raise RuntimeError("磁盘空间不足以恢复备份；当前世界未改动")
            top_names = sorted({member.split("/", 1)[0] for _, member in normalized})
            if not top_names:
                raise ValueError("备份中没有可恢复的世界或配置")
            rollback.mkdir(parents=True, exist_ok=False)
            for top_name in top_names:
                existing = root / top_name
                if existing.exists():
                    saved = rollback / top_name
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(existing), str(saved))
                    moved.append((saved, existing))
            written = 0
            for index, (info, member) in enumerate(normalized, start=1):
                target = (root / Path(*member.split("/"))).resolve()
                target.relative_to(root)
                restored_top.add(member.split("/", 1)[0])
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                written += info.file_size
                if index % 25 == 0:
                    set_job(job_id, progress=min(94, 10 + int(written / max(1, total) * 84)),
                            message=f"正在恢复 · {index:,}/{len(normalized):,} 个文件")
        set_job(job_id, state="done", progress=100,
                message=f"已恢复 {name}；恢复前文件保存在面板历史中")
    except Exception as exc:
        try:
            failed.mkdir(parents=True, exist_ok=True)
            for top_name in restored_top:
                current = root / top_name
                if current.exists():
                    shutil.move(str(current), str(unique_destination(failed, current.name)))
            for saved, original in reversed(moved):
                if saved.exists() and not original.exists():
                    shutil.move(str(saved), str(original))
        except OSError:
            pass
        set_job(job_id, state="error", message=str(exc), error=str(exc))


def start_backup_restore(profile: dict[str, Any], name: str) -> dict[str, Any]:
    backup = validate_backup_name(profile, name)
    if not backup_summary(backup)["verified"]:
        raise ValueError("旧备份缺少完整性清单，已保留供下载核验，不能自动恢复")
    with server_operation(profile, "restore"), JOBS_LOCK:
        status = server_is_running(profile)
        if status["running"] or status.get("port_conflict"):
            raise ValueError("恢复备份前必须先停止服务器并排除端口冲突")
        existing = next((job for job in JOBS.values() if job.get("server_id") == profile["id"]
                         and job.get("state") in {"queued", "running"}), None)
        if existing:
            raise ValueError("该服务端已有任务正在进行")
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "server_id": profile["id"], "type": "restore", "state": "queued",
               "progress": 0, "message": "恢复任务已排队", "created_at": now_iso(), "backup": name}
        JOBS[job_id] = job
    threading.Thread(target=restore_backup_worker, args=(profile.copy(), name, job_id), daemon=True,
                     name=f"lodestar-restore-{job_id}").start()
    return job


def read_scheduler_state() -> dict[str, Any]:
    with SCHEDULER_LOCK:
        try:
            value = json.loads(SCHEDULER_STATE_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}


def write_scheduler_state(value: dict[str, Any]) -> None:
    with SCHEDULER_LOCK:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        temporary = SCHEDULER_STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, SCHEDULER_STATE_PATH)


def parse_iso(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    except (TypeError, ValueError):
        return None


def next_daily_time(time_text: str) -> dt.datetime:
    now = dt.datetime.now().astimezone()
    hour, minute = (int(part) for part in time_text.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + dt.timedelta(days=1)


def automation_data(profile: dict[str, Any]) -> dict[str, Any]:
    state = read_scheduler_state()
    server_id = profile["id"]
    anchor = parse_iso(state.get(f"backup_anchor:{server_id}") or state.get(f"backup:{server_id}"))
    last_backup = parse_iso(state.get(f"backup_success:{server_id}"))
    interval = int(profile.get("backup_interval_hours", 6))
    next_backup = anchor + dt.timedelta(hours=interval) if anchor else dt.datetime.now().astimezone() + dt.timedelta(hours=interval)
    return {
        "backup_schedule_enabled": bool(profile.get("backup_schedule_enabled", False)),
        "backup_interval_hours": interval,
        "restart_schedule_enabled": bool(profile.get("restart_schedule_enabled", False)),
        "restart_time": str(profile.get("restart_time", "04:00")),
        "last_backup": last_backup.isoformat(timespec="seconds") if last_backup else None,
        "next_backup": next_backup.isoformat(timespec="seconds") if profile.get("backup_schedule_enabled", False) else None,
        "last_restart_date": state.get(f"restart:{server_id}"),
        "next_restart": next_daily_time(str(profile.get("restart_time", "04:00"))).isoformat(timespec="seconds")
        if profile.get("restart_schedule_enabled", False) else None,
        "panel_must_stay_open": True,
    }


def configure_automation(payload: dict[str, Any]) -> dict[str, Any]:
    values = payload.get("automation")
    if not isinstance(values, dict):
        raise ValueError("自动化配置格式不正确")
    required = {"backup_schedule_enabled", "backup_interval_hours", "restart_schedule_enabled", "restart_time"}
    if set(values) != required:
        raise ValueError("自动化配置字段不完整")
    if not isinstance(values["backup_schedule_enabled"], bool) or not isinstance(values["restart_schedule_enabled"], bool):
        raise ValueError("自动化开关格式不正确")
    previous = get_profile(str(payload.get("server_id", "")))
    backup_just_enabled = values["backup_schedule_enabled"] and not previous.get("backup_schedule_enabled", False)
    profile = update_profile({"server_id": payload.get("server_id"), "profile": values})
    scheduler_state = read_scheduler_state()
    key = f"backup_anchor:{profile['id']}"
    if backup_just_enabled or (values["backup_schedule_enabled"] and key not in scheduler_state):
        scheduler_state[key] = now_iso()
        write_scheduler_state(scheduler_state)
    return automation_data(profile)


def scheduled_restart_worker(profile: dict[str, Any], job_id: str) -> None:
    try:
        set_job(job_id, state="running", message="正在按计划正常重启服务器…")
        message = restart_server(profile)
        set_job(job_id, state="done", progress=100, message=message)
    except Exception as exc:
        set_job(job_id, state="error", message=str(exc))


def start_scheduled_restart(profile: dict[str, Any]) -> None:
    with JOBS_LOCK:
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"id": job_id, "server_id": profile["id"], "type": "scheduled_restart",
                        "state": "queued", "progress": 0, "message": "定时重启已触发", "created_at": now_iso()}
    threading.Thread(target=scheduled_restart_worker, args=(profile.copy(), job_id), daemon=True).start()


def scheduler_tick() -> None:
    settings = read_settings()
    scheduler_state = read_scheduler_state()
    now = dt.datetime.now().astimezone()
    changed = False
    for profile in settings.get("servers", []):
        server_id = profile.get("id")
        if not server_id or not Path(profile.get("path", "")).is_dir():
            continue
        if profile.get("backup_schedule_enabled", False):
            key = f"backup_anchor:{server_id}"
            last = parse_iso(scheduler_state.get(key) or scheduler_state.get(f"backup:{server_id}"))
            interval = dt.timedelta(hours=int(profile.get("backup_interval_hours", 6)))
            if last is None:
                scheduler_state[key] = now_iso()
                changed = True
            elif now - last >= interval:
                try:
                    start_backup(profile, scheduled=True)
                    scheduler_state[key] = now_iso()
                    changed = True
                except ValueError:
                    pass
        if profile.get("restart_schedule_enabled", False):
            key = f"restart:{server_id}"
            if now.strftime("%H:%M") == str(profile.get("restart_time", "04:00")) and scheduler_state.get(key) != now.date().isoformat():
                if server_is_running(profile)["running"]:
                    start_scheduled_restart(profile)
                scheduler_state[key] = now.date().isoformat()
                changed = True
    if changed:
        write_scheduler_state(scheduler_state)


def scheduler_loop() -> None:
    while True:
        try:
            scheduler_tick()
        except Exception:
            pass
        time.sleep(30)


def sample_metrics() -> None:
    for profile in read_settings()["servers"]:
        try:
            record_metric(profile, server_is_running(profile))
        except (OSError, ValueError, RuntimeError):
            # One broken instance must not interrupt sampling the others.
            continue


def metrics_loop() -> None:
    while True:
        try:
            sample_metrics()
        except Exception:
            pass
        time.sleep(10)


def java_version_text(profile: dict[str, Any]) -> str:
    try:
        completed = subprocess.run([java_executable(profile), "-version"], capture_output=True, text=True,
                                   errors="replace", timeout=8,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        output = (completed.stderr or completed.stdout).strip().splitlines()
        return output[0].strip() if output else "无法读取"
    except (OSError, subprocess.SubprocessError):
        return "无法读取"


def parsed_java_major(version_text: str) -> int | None:
    match = re.search(r'version\s+["\'](\d+)(?:\.(\d+))?', version_text, re.I)
    if not match:
        return None
    first, second = int(match.group(1)), int(match.group(2) or 0)
    return second if first == 1 else first


def required_java_major(game_version: str) -> int | None:
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", game_version or "")
    if not match:
        return None
    minor, patch = int(match.group(2)), int(match.group(3) or 0)
    if minor <= 16:
        return 8
    if minor == 17:
        return 16
    if minor < 20 or (minor == 20 and patch <= 4):
        return 17
    return 21


def diagnose_startup_failure(root: Path, log_text: str | None = None) -> dict[str, Any] | None:
    """Turn common Forge and Java startup failures into an actionable result."""
    log_path = root / "logs" / "latest.log"
    crash_folder = root / "crash-reports"
    crash_path: Path | None = None
    if crash_folder.is_dir():
        reports = sorted(crash_folder.glob("*.txt"), key=lambda item: item.stat().st_mtime, reverse=True)
        crash_path = reports[0] if reports else None
    use_crash = bool(crash_path and (not log_path.exists() or crash_path.stat().st_mtime >= log_path.stat().st_mtime))
    source = crash_path.name if use_crash and crash_path else "logs/latest.log"
    text = read_tail(crash_path, 1_200_000) if use_crash and crash_path else (
        log_text if log_text is not None else read_tail(log_path, 500_000)
    )
    clean = re.sub(r"§.", "", text)
    if not clean:
        return None

    invalid_dist = re.search(
        r"Attempted to load class\s+([^\s]+)\s+for invalid dist\s+DEDICATED_SERVER", clean, re.I
    )
    if invalid_dist:
        before = clean[:invalid_dist.start()]
        mod_matches = list(re.finditer(
            r"(?m)^\s*([^\r\n()]+?)\s*\(([-\w.]+)\)\s+has failed to load correctly", before, re.I
        ))
        if mod_matches:
            mod_name = mod_matches[-1].group(1).strip()
            mod_id = mod_matches[-1].group(2).strip()
            return {
                "severity": "error",
                "code": "client_mod_on_server",
                "title": "发现仅客户端可用的 Mod",
                "detail": f"{mod_name}（{mod_id}）在专用服务端加载了客户端类 {invalid_dist.group(1)}。",
                "suggestions": [
                    f"在 Mod 管理中停用 {mod_id}，然后重新启动",
                    "若这是整合包必需 Mod，请换用明确支持 Dedicated Server 的版本",
                    "客户端可以保留该 Mod；只需从服务端移除",
                ],
                "source": source,
            }

    if re.search(r"(?:Address already in use|Failed to bind to port|端口.*(?:占用|绑定失败))", clean, re.I):
        return {
            "severity": "error", "code": "port_in_use", "title": "监听端口已被占用",
            "detail": "另一个程序正在使用 server-port，Minecraft 无法完成监听。",
            "suggestions": ["停止占用该端口的旧服务端", "或在配置与启动中更换监听端口"], "source": source,
        }
    if re.search(r"(?:OutOfMemoryError|Java heap space|GC overhead limit exceeded)", clean, re.I):
        return {
            "severity": "error", "code": "out_of_memory", "title": "Java 内存不足",
            "detail": "服务端在加载过程中耗尽了 JVM 堆内存。",
            "suggestions": ["提高最大内存 Xmx", "检查是否有异常占用内存的 Mod 或区块"], "source": source,
        }
    if "UnsupportedClassVersionError" in clean:
        return {
            "severity": "error", "code": "java_too_old", "title": "Java 版本不兼容",
            "detail": "当前 Java 版本低于服务端或 Mod 编译时要求的版本。",
            "suggestions": ["Minecraft 1.18–1.20.4 通常使用 Java 17", "Minecraft 1.20.5+ 通常使用 Java 21"],
            "source": source,
        }
    dependency = re.search(
        r"(?:Missing or unsupported mandatory dependencies|Mod .+ requires .+|requires version)", clean, re.I
    )
    if dependency:
        return {
            "severity": "error", "code": "missing_dependency", "title": "Mod 依赖缺失或版本不匹配",
            "detail": dependency.group(0)[:400],
            "suggestions": ["根据崩溃报告补齐依赖 Mod", "确认 Minecraft、加载器和全部 Mod 版本一致"],
            "source": source,
        }
    if re.search(r"(?:Failed to start the minecraft server|LoadingFailedException|\bFATAL\b)", clean, re.I):
        caused = re.findall(r"(?m)^\s*(?:Caused by:|[\w.]+(?:Exception|Error):)\s*(.+)$", clean)
        detail = caused[-1].strip()[:500] if caused else "日志确认服务端在启动阶段发生致命错误。"
        return {
            "severity": "error", "code": "startup_fatal", "title": "服务端启动失败", "detail": detail,
            "suggestions": ["打开下方最新崩溃报告", "检查最近新增或更新的 Mod / 插件"], "source": source,
        }
    return None


def preflight_data(profile: dict[str, Any]) -> dict[str, Any]:
    root = Path(profile["path"])
    detected = detect_launch(root)
    properties = read_properties(root)
    status = server_is_running(profile)
    plan = detected.get("bootstrap") or {}
    automatic = profile.get("auto_setup", True) and plan.get("supported")
    checks: list[dict[str, str]] = []

    def add(level: str, title: str, detail: str) -> None:
        checks.append({"level": level, "title": title, "detail": detail})

    if root.is_dir():
        add("ok", "服务端目录", str(root))
    else:
        add("error", "服务端目录不存在", str(root))
    if automatic:
        add("warning" if plan.get("needs_install") else "ok", "自动准备启动目标",
            f"{plan['loader']} {plan.get('loader_version', '')} · "
            + ("一键开服时安装或继续上次安装" if plan.get("needs_install") else "复用现有核心"))
    else:
        try:
            _, description = build_launch(profile)
            add("ok", "启动目标", description)
        except Exception as exc:
            add("error", "启动目标不可用", str(exc))
    java_text = java_version_text(profile)
    java_major = parsed_java_major(java_text)
    needed = plan.get("java_major") or required_java_major(detected.get("version", ""))
    if automatic and java_major != needed:
        add("warning", "自动匹配 Java", f"开服时验证并选择 Java {needed}；本机没有时下载便携版，不改系统 PATH")
    elif java_major is None:
        add("error", "Java 无法运行", java_text)
    elif needed and java_major < needed:
        add("error", "Java 版本过低", f"Minecraft {detected.get('version')} 建议 Java {needed}+；当前 {java_major}")
    else:
        add("ok", "Java 运行时", java_text)
    if eula_accepted(root):
        add("ok", "Minecraft EULA", "已同意")
    else:
        add("error", "Minecraft EULA", "启动前需要明确同意")
    port = int(properties.get("server-port", "25565") or 25565)
    owner = find_listening_pid(port)
    current_pid = (status.get("process") or {}).get("pid")
    if status.get("port_conflict"):
        add("error", "端口归属未确认", status["port_conflict"]["message"])
    elif owner and owner != current_pid:
        add("error", "端口被占用", f"端口 {port} 正被 PID {owner} 使用")
    else:
        add("ok", "监听端口", f"{port} 可用" if not owner else f"{port} 由当前服务端监听")
    if properties.get("online-mode", "true").casefold() == "false":
        add("warning", "正版验证已关闭", "任何人都可伪造玩家名；建议配合白名单或登录插件")
    if properties.get("white-list", "false").casefold() != "true":
        add("warning", "白名单未启用", "公网映射时建议限制可加入的玩家")
    storage = status.get("storage") or {}
    if storage.get("free_gb") is not None and float(storage["free_gb"]) < 10:
        add("warning", "磁盘空间偏低", f"剩余 {storage['free_gb']} GB")
    return {"ready": not any(item["level"] == "error" for item in checks), "checks": checks,
            "java_major": java_major, "required_java_major": needed, "port": port}


def diagnostic_data(profile: dict[str, Any]) -> dict[str, Any]:
    root = Path(profile["path"])
    crash_folder = root / "crash-reports"
    crash_reports: list[dict[str, Any]] = []
    if crash_folder.is_dir():
        for path in sorted(crash_folder.glob("*.txt"), key=lambda item: item.stat().st_mtime, reverse=True)[:30]:
            crash_reports.append({
                "name": path.name,
                "size_kb": round(path.stat().st_size / 1024, 1),
                "modified": dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            })
    log_path = root / "logs" / "latest.log"
    log_text = read_tail(log_path, 900_000)
    signal = re.compile(r"(?:\bWARN\b|\bERROR\b|\bFATAL\b|Exception|Caused by:|mismatch|failed to|crash)", re.I)
    error_lines = []
    for line in log_text.splitlines():
        cleaned = re.sub(r"§.", "", line).strip()
        if cleaned and signal.search(cleaned):
            error_lines.append(cleaned[-1200:])
    detected = detect_launch(root)
    mods = list_mods(profile)
    status = server_is_running(profile)
    return {
        "java_version": java_version_text(profile),
        "loader": detected.get("loader", "unknown"),
        "game_version": status.get("version"),
        "mod_count": sum(1 for item in mods if item["state"] == "enabled"),
        "disabled_mod_count": sum(1 for item in mods if item["state"] == "disabled"),
        "crash_reports": crash_reports,
        # Historical warnings may remain in latest.log after a later successful
        # startup. A live ping is stronger evidence than an old stack trace.
        "diagnosis": None if status.get("ready") else diagnose_startup_failure(root, log_text),
        "error_lines": error_lines[-80:],
        "latest_log_size_mb": round(log_path.stat().st_size / 1024 / 1024, 2) if log_path.exists() else 0,
        "last_exit": status.get("last_exit"),
        "storage": status.get("storage"),
        "preflight": preflight_data(profile),
        "generated_at": now_iso(),
    }


def crash_report_path(profile: dict[str, Any], name: str) -> Path:
    if Path(name).name != name or not SAFE_FILE.fullmatch(name) or not name.lower().endswith(".txt"):
        raise ValueError("崩溃报告文件名无效")
    path = Path(profile["path"]) / "crash-reports" / name
    if not path.is_file():
        raise FileNotFoundError("找不到崩溃报告")
    return path


def pick_server_folder() -> str:
    if os.name == "nt":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description='选择 Minecraft 服务端文件夹';"
            "$d.ShowNewFolderButton=$false;"
            "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){"
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;Write-Output $d.SelectedPath}"
        )
        completed = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", script], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=300,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        return completed.stdout.strip()
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        selected = filedialog.askdirectory(title="选择 Minecraft 服务端文件夹")
        root.destroy()
        return selected
    except Exception as exc:
        raise RuntimeError("无法打开文件夹选择器，请手动填写绝对路径") from exc


def frp_running() -> bool:
    if os.name == "nt":
        return any(name.casefold() == "frpc.exe" for name in windows_process_snapshot().values())
    return bool(shutil.which("pgrep") and subprocess.run(["pgrep", "-x", "frpc"], capture_output=True).returncode == 0)


class PanelHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class PanelHandler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def _send(self, body: bytes, content_type: str, status: int = 200, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def _send_file(self, path: Path, content_type: str, download_name: str) -> None:
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f"attachment; filename=download; filename*=UTF-8''{quote(download_name, safe='')}")
        self.end_headers()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _validate_host(self) -> bool:
        if self.headers.get("Host", "") not in ALLOWED_HOSTS:
            self._json({"ok": False, "error": "Host 不允许"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def _validate_post(self) -> bool:
        if not self._validate_host():
            return False
        if self.headers.get("Origin", "") not in ALLOWED_ORIGINS:
            self._json({"ok": False, "error": "Origin 不允许"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("请求必须使用 application/json")
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > MAX_JSON_BYTES:
            raise ValueError("请求内容为空或过大")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求格式不正确")
        return value

    def _profile_from_query(self, query: dict[str, list[str]]) -> dict[str, Any]:
        return get_profile((query.get("server_id") or [None])[0])

    def do_GET(self) -> None:
        if not self._validate_host():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/bootstrap":
                settings = read_settings()
                profiles = [public_profile(item) for item in settings["servers"]]
                active = settings.get("active_server_id")
                self._json({"ok": True, "data": {"servers": profiles, "active_server_id": active,
                            "platform": sys.platform, "panel_version": APP_VERSION,
                            "java_runtimes": discover_java_runtimes(), "app_name": APP_NAME}})
                return
            if path == "/api/status":
                profile = self._profile_from_query(query)
                data = server_is_running(profile)
                record_metric(profile, data)
                data["frp_running"] = frp_running()
                data["profile"] = public_profile(profile)
                self._json({"ok": True, "data": data})
                return
            if path == "/api/metrics":
                self._json({"ok": True, "data": metric_history(self._profile_from_query(query))})
                return
            if path == "/api/logs":
                self._json({"ok": True, "data": combined_logs(self._profile_from_query(query))})
                return
            if path == "/api/mods":
                kind = (query.get("kind") or ["mods"])[0]
                self._json({"ok": True, "data": list_mods(self._profile_from_query(query), kind)})
                return
            if path == "/api/files":
                profile = self._profile_from_query(query)
                relative = (query.get("path") or [""])[0]
                self._json({"ok": True, "data": list_server_files(profile, relative)})
                return
            if path == "/api/file/content":
                profile = self._profile_from_query(query)
                relative = (query.get("path") or [""])[0]
                self._json({"ok": True, "data": read_server_text_file(profile, relative)})
                return
            if path == "/api/file/download":
                profile = self._profile_from_query(query)
                relative = (query.get("path") or [""])[0]
                file_path = server_file_path(profile, relative)
                if not file_path.is_file():
                    raise ValueError("只能下载文件")
                self._send_file(file_path, mimetypes.guess_type(file_path.name)[0] or "application/octet-stream", file_path.name)
                return
            if path == "/api/config":
                profile = self._profile_from_query(query)
                props = read_properties(Path(profile["path"]))
                keys = set(INT_SETTINGS) | BOOL_SETTINGS | set(ENUM_SETTINGS) | set(TEXT_SETTINGS)
                self._json({"ok": True, "data": {key: props.get(key, "") for key in sorted(keys)}})
                return
            if path == "/api/players":
                self._json({"ok": True, "data": player_data(self._profile_from_query(query))})
                return
            if path == "/api/backups":
                self._json({"ok": True, "data": list_backups(self._profile_from_query(query))})
                return
            if path == "/api/jobs":
                with JOBS_LOCK:
                    wanted = query.get("id", [None])[0]
                    jobs = [JOBS[wanted]] if wanted in JOBS else [] if wanted else list(JOBS.values())[-20:]
                self._json({"ok": True, "data": jobs})
                return
            if path == "/api/automation":
                self._json({"ok": True, "data": automation_data(self._profile_from_query(query))})
                return
            if path == "/api/diagnostics":
                self._json({"ok": True, "data": diagnostic_data(self._profile_from_query(query))})
                return
            if path == "/api/preflight":
                self._json({"ok": True, "data": preflight_data(self._profile_from_query(query))})
                return
            if path == "/api/crash-report/download":
                profile = self._profile_from_query(query)
                name = (query.get("name") or [""])[0]
                file_path = crash_report_path(profile, name)
                self._send_file(file_path, "text/plain; charset=utf-8", name)
                return
            if path == "/api/backup/download":
                profile = self._profile_from_query(query)
                name = (query.get("name") or [""])[0]
                if Path(name).name != name or not name.lower().endswith(".zip"):
                    raise ValueError("备份文件名无效")
                file_path = backup_folder(profile) / name
                if not file_path.is_file():
                    raise FileNotFoundError("找不到备份")
                self._send_file(file_path, "application/zip", name)
                return
            self._serve_static(path)
        except (ValueError, FileNotFoundError) as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (STATIC_ROOT / relative).resolve()
        try:
            target.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self._json({"ok": False, "error": "路径无效"}, HTTPStatus.NOT_FOUND)
            return
        if not target.is_file():
            self._json({"ok": False, "error": "页面不存在"}, HTTPStatus.NOT_FOUND)
            return
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in {"application/javascript", "application/json"}:
            mime += "; charset=utf-8"
        self._send(target.read_bytes(), mime)

    def do_POST(self) -> None:
        if not self._validate_post():
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/mods/upload":
                profile = require_profile(self.headers.get("X-Server-Id"))
                length = int(self.headers.get("Content-Length", "0") or 0)
                kind = self.headers.get("X-Content-Kind", "mods")
                message = save_uploaded_mod(profile, self.headers.get("X-Filename", ""), self.rfile, length, kind)
                self._json({"ok": True, "message": message})
                return
            if path == "/api/file/upload":
                profile = require_profile(self.headers.get("X-Server-Id"))
                length = int(self.headers.get("Content-Length", "0") or 0)
                message = save_uploaded_file(profile, self.headers.get("X-Relative-Path", ""), self.rfile, length)
                self._json({"ok": True, "message": message})
                return
            payload = self._read_json()
            if path == "/api/import/archive/pick":
                result = {"path": pick_server_archive()}
            elif path == "/api/import/archive/inspect":
                result = {"data": inspect_server_archive(str(payload.get("path", "")))}
            elif path == "/api/import/archive/execute":
                result = {"message": "服务端导入任务已开始", "job": start_archive_import(payload)}
            elif path == "/api/import/ftb":
                result = {"message": "FTB 一键开服任务已开始", "job": start_ftb_import(payload)}
            elif path == "/api/start/cancel":
                result = {"message": cancel_start_job(str(payload.get("job_id", "")))}
            elif path == "/api/servers/import":
                if payload.get("start_after_import") and (payload.get("trust_pack") is not True or payload.get("accept_eula") is not True):
                    raise ValueError("请明确同意 Minecraft EULA 并确认信任整合包和官方依赖下载")
                profile = register_server(payload)
                result = {"message": "服务端已导入", "profile": public_profile(profile)}
                if payload.get("start_after_import"):
                    if payload.get("accept_eula") is True:
                        accept_eula(profile)
                    result.update(message="已导入，正在一键开服", job=start_one_click(profile))
            elif path == "/api/servers/select":
                select_server(str(payload.get("server_id", "")))
                result = {"message": "已切换服务端"}
            elif path == "/api/servers/update":
                profile = update_profile(payload)
                result = {"message": "启动配置已保存", "profile": public_profile(profile)}
            elif path == "/api/servers/remove":
                unregister_server(str(payload.get("server_id", "")))
                result = {"message": "已从面板移除；原服务端文件没有删除"}
            elif path == "/api/servers/pick-folder":
                result = {"path": pick_server_folder()}
            else:
                profile = require_profile(payload.get("server_id"))
                if path == "/api/action":
                    action = str(payload.get("action", ""))
                    if action == "start":
                        result = {"message": "一键开服任务已开始", "job": start_one_click(profile)}
                    elif action == "stop":
                        message = stop_server(profile)
                    elif action == "restart":
                        message = restart_server(profile)
                    elif action == "force_stop":
                        message = force_stop_server(profile)
                    elif action == "accept_eula":
                        accept_eula(profile)
                        message = "已写入 eula=true"
                    elif action in QUICK_COMMANDS:
                        send_command(profile, QUICK_COMMANDS[action])
                        message = "指令已发送"
                    else:
                        raise ValueError("未知服务器操作")
                    if action != "start":
                        result = {"message": message}
                elif path == "/api/command":
                    command = validate_command(str(payload.get("command", "")))
                    send_command(profile, command)
                    result = {"message": f"已发送：{command}"}
                elif path == "/api/config":
                    changes = validate_properties(payload.get("settings"))
                    with server_operation(profile, "editing"):
                        write_properties(Path(profile["path"]), changes)
                    result = {"message": "配置已保存；服务器运行中时需要重启才会生效"}
                elif path == "/api/mods/action":
                    message = change_mod_state(profile, str(payload.get("action", "")),
                                               str(payload.get("state", "")), str(payload.get("name", "")),
                                               str(payload.get("kind", "mods")))
                    result = {"message": message}
                elif path == "/api/file/save":
                    revision = payload.get("revision")
                    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
                        raise ValueError("缺少文件版本信息，请刷新面板并重新打开文件")
                    relative = str(payload.get("path", ""))
                    with server_operation(profile, "editing"):
                        message = save_server_text_file(profile, relative, payload.get("content", ""), revision)
                        saved = read_server_text_file(profile, relative)
                    result = {"message": message, "revision": saved["revision"]}
                elif path == "/api/file/action":
                    message = file_manager_action(profile, str(payload.get("action", "")),
                                                  str(payload.get("path", "")), str(payload.get("name", "")))
                    result = {"message": message}
                elif path == "/api/player":
                    message = player_action(profile, str(payload.get("action", "")), str(payload.get("name", "")),
                                            str(payload.get("reason", "")))
                    result = {"message": message}
                elif path == "/api/backups":
                    action = str(payload.get("action", ""))
                    if action == "create":
                        result = {"message": "备份已开始", "job": start_backup(profile)}
                    elif action == "restore":
                        result = {"message": "备份恢复已开始", "job": start_backup_restore(profile, str(payload.get("name", "")))}
                    elif action == "remove":
                        result = {"message": remove_backup(profile, str(payload.get("name", "")))}
                    else:
                        raise ValueError("未知备份操作")
                elif path == "/api/automation":
                    result = {"message": "自动化计划已保存", "data": configure_automation(payload)}
                else:
                    self._json({"ok": False, "error": "接口不存在"}, HTTPStatus.NOT_FOUND)
                    return
            self._json({"ok": True, **result})
        except FileExistsError as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.CONFLICT)
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} · 本地 Minecraft 服务端工作台")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    parser.add_argument("--send-console", nargs=2, metavar=("PID", "COMMAND"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.send_console:
        if os.name != "nt":
            raise SystemExit("控制台接管仅支持 Windows")
        pid_text, line = args.send_console
        _send_console_native(int(pid_text), validate_command(line))
        return
    DATA_ROOT.mkdir(exist_ok=True)
    try:
        httpd = PanelHTTPServer((HOST, PORT), PanelHandler)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or getattr(exc, "errno", None) in {48, 98, 10048}:
            if not args.no_browser:
                webbrowser.open(f"http://{HOST}:{PORT}")
            print(f"面板已经在运行：http://{HOST}:{PORT}")
            return
        raise
    threading.Thread(target=scheduler_loop, daemon=True, name="lodestar-scheduler").start()
    threading.Thread(target=metrics_loop, daemon=True, name="lodestar-metrics").start()
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    print(f"{APP_NAME} 已启动：http://{HOST}:{PORT}")
    print("关闭此窗口只会关闭管理面板，不会强制关闭 Minecraft 服务器。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
