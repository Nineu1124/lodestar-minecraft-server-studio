from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import mimetypes
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
DATA_ROOT = APP_ROOT / "data"
SETTINGS_PATH = DATA_ROOT / "settings.json"
HOST = os.environ.get("RIPPLE_PANEL_HOST", "127.0.0.1")
PORT = int(os.environ.get("RIPPLE_PANEL_PORT", "8765"))
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_MOD_BYTES = 2 * 1024 * 1024 * 1024

CONFIG_LOCK = threading.RLock()
ACTION_LOCKS: dict[str, threading.Lock] = {}
RUNTIME_LOCK = threading.RLock()
RUNTIMES: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()
JOBS: dict[str, dict[str, Any]] = {}

ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
PLAYER_NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")
MEMORY_VALUE = re.compile(r"^[1-9][0-9]{0,3}[MG]$", re.I)
SAFE_COMMAND = re.compile(r"^[^\x00-\x1f\x7f]{1,500}$")
SAFE_FILE = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]{1,180}$")

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
        "external_address": profile.get("external_address", ""),
        "auto_backup_keep": int(profile.get("auto_backup_keep", 10)),
        "exists": root.is_dir(),
        "detected": detected,
    }


def get_profile(server_id: str | None = None) -> dict[str, Any]:
    settings = read_settings()
    wanted = server_id or settings.get("active_server_id")
    for profile in settings["servers"]:
        if profile.get("id") == wanted:
            return profile
    if settings["servers"]:
        return settings["servers"][0]
    raise ValueError("还没有导入服务端，请先点击“导入服务端”")


def ensure_server_folder(path_text: str) -> Path:
    if not isinstance(path_text, str) or not path_text.strip():
        raise ValueError("请输入服务端文件夹路径")
    root = Path(path_text.strip().strip('"')).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"服务端文件夹不存在：{root}")
    markers = [root / "server.properties", root / "eula.txt"]
    has_launch = any(root.glob("*.jar")) or any(root.glob("*.bat")) or any(root.glob("*.sh"))
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
        "external_address": str(payload.get("external_address") or "").strip()[:200],
        "auto_backup_keep": 10,
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
        "external_address",
        "auto_backup_keep",
    }
    if set(updates) - allowed:
        raise ValueError("启动配置中包含不允许的字段")
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
    return result


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
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件：{path}")
    original = path.read_text(encoding="utf-8", errors="replace")
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


def windows_process_snapshot() -> dict[int, str]:
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
                result[int(entry.th32ProcessID)] = entry.szExeFile
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return result


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
        return {"pid": pid, "name": name, "memory_mb": memory}

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), ctypes.c_uint32]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    process = kernel32.OpenProcess(0x0410, 0, pid)
    if not process:
        return {"pid": pid, "name": "java.exe", "memory_mb": None}
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        memory = None
        if psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            memory = round(counters.WorkingSetSize / 1024 / 1024, 1)
        return {"pid": pid, "name": "java.exe", "memory_mb": memory}
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
            chunks = bytearray()
            while len(chunks) < length:
                chunk = sock.recv(length - len(chunks))
                if not chunk:
                    break
                chunks.extend(chunk)
            return json.loads(chunks.decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, ConnectionError):
        return None


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
        if runtime["process"].poll() is not None:
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
    runtime = runtime_for(profile["id"])
    pid = find_listening_pid(port) or (runtime["process"].pid if runtime else None)
    running = ping is not None or pid is not None or runtime is not None
    players = (ping or {}).get("players", {})
    version = (ping or {}).get("version", {})
    detected = detect_launch(root)
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
        "eula": eula_accepted(root),
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
    args = [f"-Xms{profile.get('xms', '2G')}", f"-Xmx{profile.get('xmx', '6G')}"]
    path = root / "user_jvm_args.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or re.match(r"^-Xm[sx]", line, re.I):
                continue
            args.append(line)
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


def send_command(profile: dict[str, Any], line: str) -> None:
    line = validate_command(line)
    runtime = runtime_for(profile["id"])
    if runtime and runtime["process"].stdin:
        try:
            runtime["process"].stdin.write(line + "\n")
            runtime["process"].stdin.flush()
            return
        except (OSError, BrokenPipeError):
            pass
    port = int(read_properties(Path(profile["path"])).get("server-port", "25565") or 25565)
    pid = find_listening_pid(port)
    if not pid:
        raise RuntimeError("服务器未运行")
    if os.name != "nt":
        raise RuntimeError("此服务器不是由面板启动，无法接管它的标准输入")
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--send-console", str(pid), line],
        capture_output=True, text=True, errors="replace", timeout=12,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip() or "控制台指令发送失败")


def action_lock(server_id: str) -> threading.Lock:
    with RUNTIME_LOCK:
        return ACTION_LOCKS.setdefault(server_id, threading.Lock())


def start_server(profile: dict[str, Any]) -> str:
    with action_lock(profile["id"]):
        status = server_is_running(profile)
        if status["running"]:
            return "服务器已经在运行"
        root = Path(profile["path"])
        if not eula_accepted(root):
            raise RuntimeError("尚未同意 Minecraft EULA，请先在启动配置中勾选同意")
        command, description = build_launch(profile)
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        runtime_log = DATA_ROOT / f"runtime-{profile['id']}.log"
        log_handle = runtime_log.open("a", encoding="utf-8", errors="replace", buffering=1)
        log_handle.write(f"\n[{now_iso()}] Ripple Panel 启动：{description}\n")
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
        return f"已通过{description}启动；整合包首次加载可能需要几分钟"


def stop_server(profile: dict[str, Any]) -> str:
    with action_lock(profile["id"]):
        status = server_is_running(profile)
        if not status["running"]:
            return "服务器当前没有运行"
        send_command(profile, "stop")
        deadline = time.time() + 90
        while time.time() < deadline:
            if not server_is_running(profile)["running"]:
                return "服务器已正常保存并停止"
            time.sleep(1)
        raise TimeoutError("等待停服超时；可查看日志，必要时使用强制停止")


def force_stop_server(profile: dict[str, Any]) -> str:
    with action_lock(profile["id"]):
        status = server_is_running(profile)
        if not status["running"]:
            return "服务器当前没有运行"
        pid = (status.get("process") or {}).get("pid")
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
    status = server_is_running(profile)
    if status["running"]:
        stop_server(profile)
    time.sleep(1)
    return start_server(profile)


def read_tail(path: Path, max_bytes: int = 300_000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def combined_logs(profile: dict[str, Any]) -> dict[str, str]:
    root = Path(profile["path"])
    latest = read_tail(root / "logs" / "latest.log")
    runtime = read_tail(DATA_ROOT / f"runtime-{profile['id']}.log", 120_000)
    if latest:
        return {"source": "logs/latest.log", "text": latest}
    return {"source": "面板运行日志", "text": runtime or "暂无日志。启动服务器后，这里会自动刷新。"}


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


def list_mods(profile: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(profile["path"])
    locations = [
        ("enabled", root / "mods"),
        ("disabled", root / "disabled_mods"),
        ("trash", root / ".ripple-panel" / "mod-trash"),
    ]
    result: list[dict[str, Any]] = []
    for state, folder in locations:
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


def mod_path(profile: dict[str, Any], state: str, name: str) -> Path:
    root = Path(profile["path"])
    folders = {
        "enabled": root / "mods",
        "disabled": root / "disabled_mods",
        "trash": root / ".ripple-panel" / "mod-trash",
    }
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


def change_mod_state(profile: dict[str, Any], action: str, state: str, name: str) -> str:
    source = mod_path(profile, state, name)
    root = Path(profile["path"])
    if action == "enable":
        destination = unique_destination(root / "mods", source.name)
        label = "启用"
    elif action == "disable":
        destination = unique_destination(root / "disabled_mods", source.name)
        label = "停用"
    elif action == "remove":
        destination = unique_destination(root / ".ripple-panel" / "mod-trash", source.name)
        label = "移入回收站"
    elif action == "restore":
        destination = unique_destination(root / "disabled_mods", source.name)
        label = "恢复为停用状态"
    else:
        raise ValueError("未知的 Mod 操作")
    shutil.move(str(source), str(destination))
    return f"已{label} {source.name}；运行中的服务器需要重启才会生效"


def save_uploaded_mod(profile: dict[str, Any], name: str, stream: Any, length: int) -> str:
    name = safe_mod_name(name)
    if length <= 0 or length > MAX_MOD_BYTES:
        raise ValueError("Mod 文件为空或超过 2 GB 限制")
    folder = Path(profile["path"]) / "mods"
    folder.mkdir(exist_ok=True)
    destination = folder / name
    if destination.exists():
        raise FileExistsError(f"mods 中已经存在 {name}")
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
        }
        for path in sorted(folder.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    ]


def set_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(changes)


def create_backup_worker(profile: dict[str, Any], job_id: str) -> None:
    root = Path(profile["path"])
    running = server_is_running(profile)["running"]
    try:
        set_job(job_id, state="running", message="正在让服务器保存世界…")
        if running:
            send_command(profile, "save-off")
            send_command(profile, "save-all flush")
            time.sleep(2)
        properties = read_properties(root)
        world_name = properties.get("level-name", "world") or "world"
        sources = [root / world_name]
        extras = ["server.properties", "whitelist.json", "ops.json", "banned-players.json", "banned-ips.json"]
        folder = backup_folder(profile)
        folder.mkdir(exist_ok=True)
        destination = folder / f"backup-{dt.datetime.now():%Y%m%d-%H%M%S}.zip"
        set_job(job_id, message=f"正在压缩 {world_name}…")
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
            for source in sources:
                if not source.exists():
                    continue
                for path in source.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        archive.write(path, path.relative_to(root))
            for name in extras:
                path = root / name
                if path.is_file():
                    archive.write(path, path.relative_to(root))
        keep = int(profile.get("auto_backup_keep", 10))
        backups = sorted(folder.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
        trash = folder / ".trash"
        for old in backups[keep:]:
            trash.mkdir(exist_ok=True)
            shutil.move(str(old), str(unique_destination(trash, old.name)))
        set_job(job_id, state="done", progress=100, message=f"备份完成：{destination.name}")
    except Exception as exc:
        set_job(job_id, state="error", message=str(exc))
    finally:
        if running:
            try:
                send_command(profile, "save-on")
            except Exception:
                pass


def start_backup(profile: dict[str, Any]) -> dict[str, Any]:
    with JOBS_LOCK:
        existing = next((job for job in JOBS.values() if job.get("server_id") == profile["id"] and job.get("state") in {"queued", "running"}), None)
        if existing:
            raise ValueError("该服务端已经有备份任务正在进行")
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "server_id": profile["id"], "type": "backup", "state": "queued",
               "progress": 0, "message": "备份任务已排队", "created_at": now_iso()}
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
    shutil.move(str(source), str(unique_destination(trash, source.name)))
    return "备份已移入回收站"


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
    server_version = "RippleServerPanel/2.0"

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
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
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
                            "platform": sys.platform, "panel_version": "2.0.0"}})
                return
            if path == "/api/status":
                profile = self._profile_from_query(query)
                data = server_is_running(profile)
                data["frp_running"] = frp_running()
                data["profile"] = public_profile(profile)
                self._json({"ok": True, "data": data})
                return
            if path == "/api/logs":
                self._json({"ok": True, "data": combined_logs(self._profile_from_query(query))})
                return
            if path == "/api/mods":
                self._json({"ok": True, "data": list_mods(self._profile_from_query(query))})
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
                    jobs = list(JOBS.values())[-20:]
                self._json({"ok": True, "data": jobs})
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
                profile = get_profile(self.headers.get("X-Server-Id"))
                length = int(self.headers.get("Content-Length", "0") or 0)
                message = save_uploaded_mod(profile, self.headers.get("X-Filename", ""), self.rfile, length)
                self._json({"ok": True, "message": message})
                return
            payload = self._read_json()
            if path == "/api/servers/import":
                profile = register_server(payload)
                result = {"message": "服务端已导入", "profile": public_profile(profile)}
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
                profile = get_profile(str(payload.get("server_id") or "") or None)
                if path == "/api/action":
                    action = str(payload.get("action", ""))
                    if action == "start":
                        message = start_server(profile)
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
                    result = {"message": message}
                elif path == "/api/command":
                    command = validate_command(str(payload.get("command", "")))
                    send_command(profile, command)
                    result = {"message": f"已发送：{command}"}
                elif path == "/api/config":
                    changes = validate_properties(payload.get("settings"))
                    write_properties(Path(profile["path"]), changes)
                    result = {"message": "配置已保存；服务器运行中时需要重启才会生效"}
                elif path == "/api/mods/action":
                    message = change_mod_state(profile, str(payload.get("action", "")),
                                               str(payload.get("state", "")), str(payload.get("name", "")))
                    result = {"message": message}
                elif path == "/api/player":
                    message = player_action(profile, str(payload.get("action", "")), str(payload.get("name", "")),
                                            str(payload.get("reason", "")))
                    result = {"message": message}
                elif path == "/api/backups":
                    action = str(payload.get("action", ""))
                    if action == "create":
                        result = {"message": "备份已开始", "job": start_backup(profile)}
                    elif action == "remove":
                        result = {"message": remove_backup(profile, str(payload.get("name", "")))}
                    else:
                        raise ValueError("未知备份操作")
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
    parser = argparse.ArgumentParser(description="Ripple Server Panel · 本地 Minecraft 服务端管理面板")
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
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    print(f"Ripple Server Panel 已启动：http://{HOST}:{PORT}")
    print("关闭此窗口只会关闭管理面板，不会强制关闭 Minecraft 服务器。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
