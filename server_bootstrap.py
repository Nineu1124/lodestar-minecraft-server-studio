"""Version-pinned, resumable preparation of trusted Minecraft server packs.

No author shell scripts are evaluated by this module. Network requests are limited
to official distribution hosts; Java is portable and never changes PATH.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener
import uuid
import zipfile

MARKER = ".lodestar-pack.json"
FTB_SOURCE = ".lodestar-ftb-source.json"
CORE_PENDING = ".lodestar/core-install-pending.json"
VERSION = re.compile(r"^[0-9][0-9A-Za-z.+_-]{0,79}$")
GAME = re.compile(r"^1\.[0-9]{1,2}(?:\.[0-9]{1,2})?$")
HOSTS = {"maven.minecraftforge.net", "maven.neoforged.net", "meta.fabricmc.net",
         "api.adoptium.net", "github.com", "release-assets.githubusercontent.com",
         "objects.githubusercontent.com", "api.feed-the-beast.com", "files.feed-the-beast.com",
         "cdn.feed-the-beast.com", "edge.forgecdn.net", "mediafilez.forgecdn.net",
         "media.forgecdn.net"}
KNOWN_ARCHIVES = {
    "a29b3c0c99b41e1c7d404a78f48b7f9698f15b068141a042d5ca1108a8636c55": {
        "name": "RLCraft 2.9.3", "game_version": "1.12.2", "loader": "forge",
        "loader_version": "14.23.5.2860", "java_major": 8,
    },
}
JAVA_LOCK = threading.Lock()


class Cancelled(RuntimeError):
    pass


def java_requirement(game: str) -> int:
    parts = [int(part) for part in game.split(".")]
    minor, patch = parts[1], parts[2] if len(parts) > 2 else 0
    return 8 if minor <= 16 else 16 if minor == 17 else 21 if minor > 20 or (minor == 20 and patch >= 5) else 17


def assignments(text: str) -> dict[str, str]:
    return {m[1].upper(): m[2].strip().strip('"\'') for m in
            re.finditer(r"(?m)^\s*([A-Z_]+)\s*=\s*([^\r\n]*)", text)}


def recognize(files: list[str], texts: dict[str, str], known: dict | None = None) -> dict:
    lower = {name.casefold(): name for name in files}
    hybrid = ("luminara", "arclight", "mohist", "magma", "banner", "catserver", "youer")
    if any("/" not in name and name.lower().endswith(".jar") and any(token in name.lower() for token in hybrid) for name in files):
        return {"supported": False, "reason": "混合核心保留原有启动设置，不替换为普通 Forge"}
    plan = dict(known or {})
    variables = assignments(texts.get("variables.txt", ""))
    if MARKER in texts:
        try:
            value = json.loads(texts[MARKER])
            if isinstance(value, dict):
                plan.update({key: value[key] for key in ("name", "game_version", "loader", "loader_version", "java_major", "fabric_installer") if key in value})
        except (ValueError, TypeError):
            pass
    if "lwjgl3ify-forgepatches.jar" in lower and "java9args.txt" in lower:
        return {"supported": True, "adapter": "gtnh", "name": "GT New Horizons",
                "game_version": "1.7.10", "loader": "forge", "java_major": 17,
                "launch_mode": "jar", "launch_target": lower["lwjgl3ify-forgepatches.jar"],
                "argfiles": [lower["java9args.txt"]], "jvm_args": ["-Dfml.readTimeout=180"],
                "needs_install": False}
    if variables.get("MODLOADER", "").lower() in {"forge", "neoforge", "fabric"}:
        plan.update(game_version=variables.get("MINECRAFT_VERSION"), loader=variables["MODLOADER"].lower(),
                    loader_version=variables.get("MODLOADER_VERSION"),
                    fabric_installer=variables.get("FABRIC_INSTALLER_VERSION"))
    for name in files:
        match = re.search(r"(?:^|/)libraries/net/minecraftforge/forge/(1\.\d+(?:\.\d+)?)-([^/]+)/(?:win|unix)_args\.txt$", name, re.I)
        if match and not plan.get("loader"):
            plan.update(loader="forge", game_version=match[1], loader_version=match[2])
        match = re.fullmatch(r"forge-(1\.\d+(?:\.\d+)?)-([0-9.]+)(?:-installer|-universal)?\.jar", name, re.I)
        if match and not plan.get("loader"):
            plan.update(loader="forge", game_version=match[1], loader_version=match[2])
        match = re.search(r"(?:^neoforge-|libraries/net/neoforged/neoforge/)([0-9.]+)(?:-installer\.jar|/(?:win|unix)_args\.txt)$", name, re.I)
        if match and not plan.get("loader"):
            # NeoForge 20.x / 21.x convention; do not guess future year-numbered releases.
            bits = match[1].split(".")
            if len(bits) >= 3 and bits[0] in {"20", "21"}:
                plan.update(loader="neoforge", loader_version=match[1], game_version=f"1.{bits[0]}.{bits[1]}")
    if (plan.get("loader") not in {"forge", "neoforge", "fabric"}
            or not GAME.fullmatch(str(plan.get("game_version", "")))
            or not VERSION.fullmatch(str(plan.get("loader_version", "")))):
        return {"supported": False, "reason": "没有可靠的游戏/加载器版本元数据；请提供作者服务端包或明确启动文件。"}
    plan["java_major"] = java_requirement(plan["game_version"])
    plan.update(supported=True, adapter=plan["loader"], argfiles=[], jvm_args=[])
    loader, game, version = plan["loader"], plan["game_version"], plan["loader_version"]
    if loader == "fabric":
        installer = str(plan.get("fabric_installer") or "")
        if not VERSION.fullmatch(installer):
            # Resolved from official Fabric metadata during preparation if absent.
            plan["fabric_installer"] = None
        plan.update(launch_mode="jar", launch_target="fabric-server-launcher.jar")
    else:
        coordinate = version if loader == "neoforge" else f"{game}-{version}"
        namespace = "neoforged/neoforge" if loader == "neoforge" else "minecraftforge/forge"
        target = f"libraries/net/{namespace}/{coordinate}/{'win' if os.name == 'nt' else 'unix'}_args.txt"
        if loader == "forge" and int(game.split(".")[1]) <= 16:
            target = next((n for n in files if n.casefold() in {f"forge-{coordinate}.jar", f"forge-{coordinate}-universal.jar"}), f"forge-{coordinate}.jar")
            plan.update(launch_mode="jar", launch_target=target)
        else:
            plan.update(launch_mode="forge_args", launch_target=target)
    plan["needs_install"] = plan["launch_target"].casefold() not in lower
    return plan


def folder_plan(root: Path) -> dict:
    files = [p.name for p in root.iterdir() if p.is_file()]
    for pattern in ("libraries/net/minecraftforge/forge/*/*_args.txt", "libraries/net/neoforged/neoforge/*/*_args.txt"):
        files.extend(p.relative_to(root).as_posix() for p in root.glob(pattern))
    texts = {}
    for name in ("variables.txt", MARKER):
        path = root / name
        if path.is_file() and path.stat().st_size <= 256_000:
            texts[name] = path.read_text(encoding="utf-8-sig", errors="replace")
    plan = recognize(files, texts)
    if plan.get("supported") and plan.get("loader_version") and (root / CORE_PENDING).is_file():
        # Installers can create an args file before finishing their libraries.
        # Only our interrupted installs get retried; intact author installs stay intact.
        plan["needs_install"] = True
    return plan


def official_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("下载地址不在官方 HTTPS 分发站点名单中")
    return url


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def response(url: str):
    return build_opener(OfficialRedirect).open(Request(official_url(url), headers={"User-Agent": "Lodestar/3.1 server-bootstrap"}), timeout=45)


def remote_bytes(url: str, limit: int = 16 * 1024 * 1024) -> bytes:
    with response(url) as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("官方元数据超过大小限制")
    return value


def remote_json(url: str):
    return json.loads(remote_bytes(url))


def digest_file(path: Path, algorithm="sha256") -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def download(url: str, target: Path, progress, expected: str | None = None, size: int | None = None, algorithm="sha256") -> None:
    official_url(url)
    if target.is_file() and (size is None or target.stat().st_size == size) and expected and digest_file(target, algorithm) == expected.lower():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".lodestar-download.part")
    for attempt in range(3):
        try:
            count, started, last = 0, time.monotonic(), 0
            digest = hashlib.new(algorithm)
            with response(url) as source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > (size if size is not None else 1024**3) or time.monotonic() - started > 900:
                        raise ValueError("依赖下载超过大小或时间限制")
                    output.write(chunk)
                    digest.update(chunk)
                    if time.monotonic() - last > 2:
                        progress(f"下载 {target.name} · {count / 1024**2:.1f} MB")
                        last = time.monotonic()
            if size is not None and count != size:
                raise ValueError("文件大小校验失败")
            if expected and digest.hexdigest() != expected.lower():
                raise ValueError("官方文件哈希校验失败")
            os.replace(temporary, target)
            return
        except Cancelled:
            raise
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def java_major(executable: str) -> int | None:
    try:
        run = subprocess.run([executable, "-version"], capture_output=True, text=True, errors="replace", timeout=12,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        match = re.search(r'version\s+"(?:1\.)?(\d+)', run.stderr + run.stdout)
        return int(match[1]) if run.returncode == 0 and match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def safe_child(root: Path, name: str) -> Path:
    # Treat metadata/ZIP paths as data, including on Windows.
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or any(part == ".." or re.search(r'[<>:"|?*\x00-\x1f]', part)
            or (part not in {"", "."} and (part.endswith((".", " "))
                or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.I))) for part in parts):
        raise ValueError("依赖文件路径不安全")
    path = (root / normalized).resolve()
    path.relative_to(root.resolve())
    return path


def ensure_java(major: int, candidates: list[str], cache: Path, progress) -> str:
    with JAVA_LOCK:
        portable = cache / "java" / str(major)
        paths = [*candidates, *(str(p) for p in portable.glob("*/bin/java.exe"))]
        for value in dict.fromkeys(paths):
            if value and java_major(value.strip('"')) == major:
                return value.strip('"')
        if os.name != "nt":
            raise RuntimeError(f"需要 Java {major}；请安装对应 Java 并在配置中指定路径")
        progress(f"准备便携 Java {major}（不会修改系统 PATH）")
        assets = remote_json(f"https://api.adoptium.net/v3/assets/latest/{major}/hotspot?architecture=x64&image_type=jre&os=windows")
        package = assets[0]["binary"]["package"]
        if not re.fullmatch(r"[0-9a-f]{64}", str(package.get("checksum", ""))) or not 0 < int(package.get("size", 0)) <= 1024**3:
            raise ValueError("官方 Java 元数据校验失败")
        archive = cache / "downloads" / f"temurin-{major}-{package['checksum'][:16]}.zip"
        download(package["link"], archive, progress, package["checksum"], package["size"])
        stage = cache / "java" / f".stage-{uuid.uuid4().hex}"
        stage.mkdir(parents=True)
        with zipfile.ZipFile(archive) as zipped:
            if sum(i.file_size for i in zipped.infolist()) > 2 * 1024**3:
                raise ValueError("Java 归档超过解压限制")
            for info in zipped.infolist():
                if ((info.external_attr >> 16) & 0o170000) == 0o120000 or info.flag_bits & 1:
                    raise ValueError("Java 归档含不安全条目")
                target = safe_child(stage, info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(info) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        found = next(stage.glob("*/bin/java.exe"), None)
        if not found or java_major(str(found)) != major:
            raise RuntimeError("下载的 Java 未通过版本验证")
        portable.mkdir(parents=True, exist_ok=True)
        destination = portable / found.parent.parent.name
        if destination.exists():
            destination = portable / f"{found.parent.parent.name}-{uuid.uuid4().hex[:8]}"
        os.replace(found.parent.parent, destination)
        stage.rmdir()
        return str(destination / "bin" / "java.exe")


def install_core(root: Path, plan: dict, java: str, progress) -> None:
    target = safe_child(root, plan["launch_target"])
    pending = root / CORE_PENDING
    if target.is_file() and not pending.is_file():
        return
    loader, game, version = plan["loader"], plan["game_version"], plan["loader_version"]
    if loader == "fabric":
        installer = plan.get("fabric_installer") or remote_json("https://meta.fabricmc.net/v2/versions/installer")[0]["version"]
        if not VERSION.fullmatch(installer):
            raise ValueError("Fabric 安装器版本无效")
        download(f"https://meta.fabricmc.net/v2/versions/loader/{game}/{version}/{installer}/server/jar", target, progress)
        return
    coordinate = version if loader == "neoforge" else f"{game}-{version}"
    filename = f"{loader}-{coordinate}-installer.jar"
    base = "https://maven.neoforged.net/releases/net/neoforged/neoforge" if loader == "neoforge" else "https://maven.minecraftforge.net/net/minecraftforge/forge"
    url = f"{base}/{coordinate}/{filename}"
    algorithm = "sha256" if loader == "neoforge" else "sha1"
    checksum = remote_bytes(f"{url}.{algorithm}", 4096).decode().strip().split()[0]
    if not re.fullmatch(r"[0-9a-fA-F]{64}" if algorithm == "sha256" else r"[0-9a-fA-F]{40}", checksum):
        raise ValueError("官方安装器校验值无效")
    installer = root / filename
    download(url, installer, progress, checksum, algorithm=algorithm)
    log = root / ".lodestar" / "install.log"
    log.parent.mkdir(exist_ok=True)
    pending.write_text(json.dumps({"loader": loader, "version": version, "target": plan["launch_target"]}), encoding="utf-8")
    progress(f"安装 {loader} {version}，日志：.lodestar/install.log")
    with log.open("a", encoding="utf-8", errors="replace") as output:
        process = subprocess.Popen([java, "-Djava.awt.headless=true", "-jar", str(installer), "--installServer"],
                                   cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 1200
            while process.poll() is None:
                progress(f"正在安装 {loader} {version} · 详情见 .lodestar/install.log")
                if time.monotonic() > deadline:
                    raise TimeoutError("核心安装超过 20 分钟；文件保留，可重试")
                time.sleep(2)
            if process.returncode != 0 or not target.is_file():
                raise RuntimeError(f"核心安装未完成（退出码 {process.returncode}），请查看 {log}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()  # Only the installer process created immediately above.
                    process.wait(timeout=15)
    pending.unlink(missing_ok=True)


def prepare(root: Path, candidates: list[str], cache: Path, progress) -> dict:
    source_file = root / FTB_SOURCE
    if source_file.is_file():
        source = json.loads(source_file.read_text(encoding="utf-8"))
        if source.get("complete") is not True:
            progress("读取 FTB 官方文件清单，校验并补齐缺失文件…")
            manifest = ftb_manifest(int(source["pack_id"]), int(source["version_id"]))
            install_ftb(root, manifest, progress)
            source["complete"] = True
            source_file.write_text(json.dumps(source), encoding="utf-8")
    plan = folder_plan(root)
    if not plan.get("supported"):
        return {}
    java = ensure_java(plan["java_major"], candidates, cache, progress)
    if plan.get("needs_install"):
        install_core(root, plan, java, progress)
    if not safe_child(root, plan["launch_target"]).is_file():
        raise RuntimeError("准备后仍缺少启动目标")
    return {"java": java, "launch_mode": plan["launch_mode"], "launch_target": plan["launch_target"]}


def ftb_manifest(pack_id: int, version_id: int | None) -> dict:
    if not 1 <= pack_id <= 10_000_000 or (version_id is not None and not 1 <= version_id <= 100_000_000):
        raise ValueError("FTB 项目/版本 ID 无效")
    base = f"https://api.feed-the-beast.com/v1/modpacks/public/modpack/{pack_id}"
    if version_id is None:
        listing = remote_json(base)
        stable = [v for v in listing.get("versions", []) if str(v.get("type", "")).lower() == "release"]
        if not stable:
            raise ValueError("未找到稳定发布版，请填写明确的 FTB 版本 ID")
        version_id = max(stable, key=lambda v: int(v.get("updated", 0)))["id"]
    manifest = remote_json(f"{base}/{version_id}")
    if manifest.get("id") != int(version_id) or not isinstance(manifest.get("files"), list):
        raise ValueError("FTB 官方清单无效")
    return manifest


def install_ftb(root: Path, manifest: dict, progress) -> dict:
    targets = {t["type"]: t for t in manifest["targets"]}
    metadata = {"name": f"FTB · {manifest['name']}", "game_version": targets["game"]["version"],
                "loader": targets["modloader"]["name"].lower(), "loader_version": targets["modloader"]["version"]}
    plan = recognize([], {}, metadata)
    if not plan.get("supported"):
        raise ValueError("此 FTB 包的加载器暂不支持自动安装")
    files = [f for f in manifest["files"] if not f.get("clientonly")]
    if len(files) > 100_000 or any(not 0 <= int(f["size"]) <= 1024**3 for f in files) or sum(int(f["size"]) for f in files) > 30 * 1024**3:
        raise ValueError("FTB 清单超过下载限制")
    requests = []
    seen = set()
    for item in files:
        target = safe_child(root, item["path"].rstrip("/") + "/" + item["name"])
        key = str(target).casefold()
        if key in seen or target == root.resolve():
            raise ValueError("FTB 清单有重复或无效路径")
        seen.add(key)
        checksum = item.get("hashes", {}).get("sha256") or item.get("sha1", "")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", checksum):
            raise ValueError("FTB 文件缺少有效的官方校验值")
        official_url(item["url"])
        requests.append((item, target, checksum))
    # A downloaded partial file can coexist with an existing file until its hash is verified.
    required = sum(int(item["size"]) for item, target, checksum in requests
                   if not target.is_file() or target.stat().st_size != int(item["size"]))
    required += max((int(item["size"]) for item, _, _ in requests), default=0) * 6 + 1024**3
    if shutil.disk_usage(root).free < required:
        raise RuntimeError("磁盘空间不足，无法安全下载 FTB 服务端；已有文件保留")
    def fetch(request):
        item, target, checksum = request
        progress("校验 FTB 文件 · " + target.name)
        download(item["url"], target, progress, checksum, int(item["size"]), "sha256" if len(checksum) == 64 else "sha1")
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch, request) for request in requests]
        for index, future in enumerate(as_completed(futures), 1):
            future.result()
            if index % 20 == 0 or index == len(futures):
                progress(f"FTB 文件校验 · {index}/{len(futures)}")
    (root / MARKER).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata
