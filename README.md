# Lodestar · Minecraft Server Studio

一个为 Minecraft 整合包服主设计的**纯本地 Web 管理面板**。不需要数据库、不需要 Node.js、不把服务端文件上传到云端；启动一个 Python 进程后，在浏览器中管理多个 Minecraft 服务端。

> 面板默认只监听 `127.0.0.1:8765`。SakuraFRP、路由器端口转发或防火墙只应放行 Minecraft 游戏端口，**不要映射 8765 面板端口**。

## 已实现功能

- ZIP 服务端压缩包导入向导：安全检查、嵌套根目录识别、解压进度与导入完成后自动启动
- 从原启动脚本中提取真正的 `-jar` / Forge 参数目标，自动识别 Luminara、Arclight、Mohist 等混合端
- 一键推荐或自定义开服：Java、Xms/Xmx、端口、人数、视距、难度、正版验证、白名单与 EULA
- 多服务端导入、切换与本地配置
- 自动检测 Forge、NeoForge、Fabric、Paper、Purpur、普通服务端 JAR 和 BAT/CMD/SH 启动脚本
- 为不同实例分别设置 Java、`Xms`、`Xmx`、启动方式和外网地址
- 启动、正常保存停服、重启与带二次确认的强制停止
- 浏览器实时查看 `logs/latest.log`，执行任意 Minecraft 服务端命令
- 常驻可搜索指令库：状态、世界、玩家、名单、规则、公告与维护共 30 余项常用指令
- Mod 和插件上传、启用、停用、移入回收站和恢复，并读取 Fabric/Forge/NeoForge/Bukkit 元数据
- 安全文件管理器：浏览、上传、下载、重命名、文本编辑、修改历史与可恢复删除
- 编辑经过校验的常用 `server.properties`
- 白名单、OP、踢出、封禁与解封玩家
- 实时调整时间、天气、难度、常用游戏规则，并发送全服公告
- 在线创建世界 ZIP 备份，自动执行 `save-off` / `save-all flush` / `save-on`；停服后可一键恢复且保留恢复前版本
- 按小时自动备份、每天定时正常重启（默认关闭，可按实例配置）
- CPU、内存、运行时间与磁盘空间监控
- Java/加载器/Mod/日志信号诊断，浏览和下载崩溃报告，并自动归类客户端 Mod、端口、内存、Java 与依赖错误
- Claude 官网式的暖色编辑排版与炭火深色主题，首次运行可跟随系统主题
- Host、Origin、路径和文件名校验；配置修改前自动备份

## 系统要求

- Windows 10/11，或带 Python 的 Linux/macOS
- Python 3.11 或更高版本
- 与整合包匹配的 Java 版本
- 现代浏览器

项目后端只使用 Python 标准库，没有第三方 Python/Node 依赖。

## 快速开始

### Windows

双击：

```text
启动面板.bat
```

浏览器会打开 <http://127.0.0.1:8765>。第一次使用点击顶部“添加”，选择服务端 ZIP 压缩包即可。

### Linux / macOS

```bash
chmod +x start.sh
./start.sh
```

也可以直接运行：

```bash
python server.py
```

## 导入服务端压缩包

选择 ZIP 后，Lodestar 会先读取中央目录，不会直接解压。检查通过后会显示游戏版本、加载器、真正的服务端根目录、解压体积、Java 建议和启动文件。最后一步可以选择：

- **一键推荐**：沿用原服务端启动脚本表达的核心，自动匹配本机 Java，并使用常用参数。
- **自定义**：在解压前确认内存、端口、人数、视距、难度、正版验证、白名单和启动文件。

导入器拒绝绝对路径、`..` 路径穿越、符号链接、加密包、超量文件和超过安全体积上限的压缩包。解压先写入独立临时目录，成功后才原子移动到最终目录。支持压缩包根目录就是服务端，也支持 `整合包/fuwuduan/...` 这类嵌套结构。

## 导入已解压服务端

导入的是**服务端根目录**，通常能看到以下文件中的至少一种：

- `server.properties`
- `eula.txt`
- `run.bat` / `start.bat`
- `server.jar` / `paper-*.jar` / `fabric-server-launch.jar`
- `libraries/net/minecraftforge/.../win_args.txt`

导入已解压文件夹时，面板只记录路径，不复制文件。实例配置保存在 `data/settings.json`，该文件已被 `.gitignore` 排除，不会意外提交个人路径。

## 启动方式与内存

自动检测优先级大致为：Forge/NeoForge 参数文件、适配的 JAR、启动脚本。如果自动结果不正确，可以在“配置与启动”中手动选择。

Forge/NeoForge 参数文件启动时，面板会读取 `user_jvm_args.txt` 中除 `-Xms`、`-Xmx` 外的 JVM 参数，并使用实例中配置的内存值。通过 BAT/SH 启动脚本时，内存由脚本自身控制。

## Mod、插件与文件管理的安全策略

- “停用”会把 JAR 移到服务端的 `disabled_mods` 文件夹。
- “移除”会把 JAR 移到 `.ripple-panel/mod-trash`，不会永久删除。
- 从回收站恢复时先恢复为停用状态，确认无误后再启用。
- 面板只能读取常见 Mod 元数据，不能保证依赖完整或客户端/服务端兼容。
- 插件使用独立的 `plugins`、`disabled_plugins` 和插件回收目录，不会与 Mod 混用。
- 文件编辑前自动保存历史版本；网页删除会移动到面板内部回收站。
- 文件 API 会把所有路径解析并限制在当前服务端根目录，面板内部数据不会暴露给文件管理器。

更换 Mod 前请先停止服务器并创建世界备份。

## 备份

备份默认保存在每个服务端的：

```text
panel-backups/
```

备份包含 `level-name` 指定的世界文件夹及白名单、OP、封禁和 `server.properties`。超出实例“保留备份数”的旧备份会移入 `panel-backups/.trash`。恢复操作要求服务器先停服；被替换的现有世界和配置会保留在 `.ripple-panel/restore-history`，避免恢复选错后无法回退。

“自动化”页可以为每个实例单独设置备份间隔和每天重启时间。计划由本地面板进程执行，因此面板窗口必须保持运行；电脑关机、睡眠或面板关闭期间不会执行任务。所有计划默认关闭，启用自动备份时从当前时间开始计算第一次间隔。

## 游戏规则

“游戏规则”页通过当前服务端控制台实时执行原版命令，可以调整时间、天气、难度、死亡保留物品、生物破坏、昼夜与天气循环、火焰蔓延和入睡比例，也可以向所有在线玩家发送公告。使用这些操作时服务端必须处于运行状态。

## 运行诊断

“运行诊断”页会读取当前 Java 版本、加载器、Mod 数量、磁盘空间、上次退出状态、`logs/latest.log` 中最近的高信号错误行，以及 `crash-reports` 目录中的报告。面板会优先给出一个可执行的自动结论，目前可识别仅客户端 Mod、端口占用、内存不足、Java 版本和 Mod 依赖问题。诊断摘要可以复制，崩溃报告可以逐个下载；内容只在本机处理，不会上传。

市场面板功能对照与本项目的取舍见 [`docs/MARKET_RESEARCH.md`](docs/MARKET_RESEARCH.md)。

## 安全说明

网页控制台等同于 Minecraft 服务端管理员权限。项目采取了以下本地安全限制：

- 默认仅绑定 `127.0.0.1`
- POST 请求验证 `Host` 与 `Origin`
- 不启用 CORS
- 严格限制可访问路径、Mod 文件名和启动目标
- CSP、`X-Frame-Options: DENY` 与禁止浏览器敏感权限
- 删除类功能优先移动到回收目录

如果主动把面板监听地址改为 `0.0.0.0`，必须自行增加身份验证、HTTPS 与反向代理访问控制。本项目当前不建议这样使用。

## 项目结构

```text
Lodestar/
├─ server.py                 # 标准库后端、进程与文件管理
├─ static/
│  ├─ index.html             # 面板页面
│  ├─ app.css                # 基础组件与响应式布局
│  ├─ editorial.css          # 暖色编辑风格与深色主题
│  └─ app.js                 # 前端状态和 API 调用
│  └─ assets/                # 原创 PNG 品牌图、首页主视觉与整站背景
├─ data/
│  └─ settings.example.json  # 空白配置示例
├─ 启动面板.bat
├─ start.sh
└─ README.md
```

## 后续方向

- RCON 模式，远程管理非本机服务端
- Modrinth/CurseForge 元数据与依赖检查（需要网络 API）
- TPS/MSPT 图表与卡顿告警
- 崩溃堆栈指纹库与按 Mod ID 聚合的已知问题资料
- 可选登录与局域网管理模式
- S3/SFTP 异地备份与 Discord/Webhook 通知

## 开源协议

[MIT](LICENSE)
