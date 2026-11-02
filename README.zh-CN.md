# ios-backup-station

[English](README.md) · **简体中文**

更新记录见 [CHANGELOG.md](CHANGELOG.md)。

把 iPhone 和 iPad **无线或用 USB 备份到你自己的 NAS**，用浏览器管理。备份是整机加密备份，文件夹格式和 iTunes、Finder 做的一样。

<p align="center"><img src="docs/images/hero.png" alt="电脑版和手机版界面" width="900"></p>

<p align="center"><a href="docs/video/ios-backup-station-demo.mp4"><img src="docs/images/video-poster.png" alt="观看 3 分钟演示视频（无声，中英文字幕）" width="560"></a><br><sub>▶ 观看 3 分钟演示视频（无声，中英文字幕）</sub></p>

> 与苹果公司无关。使用前请先读[免责声明](#5-免责声明和测试配置)，**尤其是“还没有验证过什么”那一段**。

## 目录

1. [简介和出发点](#1-简介和出发点)
2. [怎么安装](#2-怎么安装)
3. [功能一览（截图）](#3-功能一览截图)
4. [需要哪些权限](#4-需要哪些权限)
5. [免责声明和测试配置](#5-免责声明和测试配置)
6. [已知限制](#6-已知限制)
7. [常见问题和排错](#7-常见问题和排错)
8. [怎么恢复备份](#8-怎么恢复备份)
9. [升级、卸载、备份配置](#9-升级卸载备份配置)
10. [安全和隐私](#10-安全和隐私)
11. [和同类方案的区别](#11-和同类方案的区别)
12. [工作原理](#12-工作原理)
13. [开发和测试](#13-开发和测试)
14. [致谢和许可证](#14-致谢和许可证)
15. [请我喝杯咖啡](#15-请我喝杯咖啡)

---

## 1. 简介和出发点

**出发点很简单：我不想把整机备份交给 iCloud，也不想每次都开着电脑、插着线。**

- iCloud 备份只能存到苹果的服务器，容量要另外付费。
- iTunes 或 Finder 的“无线同步”要有一台电脑一直开着。
- 家里已经有 NAS，空间大，一直开机。

所以这个项目让 NAS 自己去备份手机：

- NAS 通过 USB 或 Wi-Fi 连上 iPhone 或 iPad，用的是 [libimobiledevice](https://libimobiledevice.org) 和 usbmuxd2，也就是 iTunes 同一套备份协议。
- 备份可以**加密**，文件夹按手机的 UDID 命名，和 iTunes 或 Finder 的备份目录格式一致，所以需要时可以拷到 Windows 或 Mac 上恢复（见[怎么恢复备份](#8-怎么恢复备份)）。
- 有**网页界面**（电脑版和手机版，iPad 也能用），可以设置备份位置、加密、**定时备份**，看进度和记录。
- 整个东西是**一个 Docker 容器**，用 `docker compose up` 启动。

适合你，如果：家里有一台能跑 Docker 的 NAS 或小主机，有 USB 口，手机和 NAS 在同一个局域网。

## 2. 怎么安装

### 你需要

| 要求 | 说明 |
|---|---|
| 一台 Linux 的 NAS 或小主机 | 测试过 x86_64 的飞牛 fnOS，其他系统见[测试配置](#5-免责声明和测试配置) |
| Docker 和 Docker Compose v2 | `docker compose version` 能运行 |
| 一个 USB 口 | 第一次配对和第一次完整备份建议用 USB |
| 主机上的 avahi（可选） | 只有无线备份需要；没有它仍可用 USB 备份 |
| 一个文件夹 | 放备份用，空间要够（整机备份可能是几十到上百 GB） |

### 最简单：安装脚本

```bash
git clone https://github.com/JerryGHT04/ios-backup-station.git
cd ios-backup-station
./install.sh
```

脚本会：检查 Docker，**推荐剩余空间最大的位置**，确认文件夹存在并且能写，写好 `.env`，启动容器，打印访问地址。第一次需要在 NAS 上从源码构建，大约几分钟。

没有 `.env` 时直接 `docker compose up` 会停下来，并告诉你该填什么：

```text
required variable BACKUP_LOCATION is missing a value: 请先在 .env 里设置 BACKUP_LOCATION，填备份要保存到的 NAS 文件夹的完整路径……
```

### 手动安装

```bash
cp .env.example .env
# 编辑 .env，只有 BACKUP_LOCATION 必须填：备份保存到的、已经存在的文件夹的完整路径
docker compose up -d --build
```

`.env` 里的设置：

| 变量 | 默认 | 作用 |
|---|---|---|
| `BACKUP_LOCATION` | 必填 | 备份保存到哪个 NAS 文件夹，例如 `/vol1/1000/ios-backups` |
| `PORT` | `8899` | 网页端口 |
| `CONFIG_LOCATION` | `./config` | 设置、访问密码、历史记录，**不要删** |
| `LOCKDOWN_LOCATION` | `./lockdown` | 手机的配对记录，**不要删**，删了要重新配对 |
| `APT_MIRROR` | 空 | 构建时下载太慢可以填镜像，例如 `mirrors.ustc.edu.cn` |
| `IBS_IMAGE` | 空 | 可选的预编译镜像，例如 `ghcr.io/jerryght04/ios-backup-station:v0.2.0`；填了就不用在 NAS 上构建 |

### 在 NAS 的 Docker 界面里使用

飞牛 fnOS 上，用上面的命令启动后，项目会自动出现在 Docker 应用的 Compose 列表里，也可以在那里查看和停止。如果要在图形界面里修改 compose，先停止项目；改动的是同一个文件。

### 安装之后（第一次使用）

1. 在同一个局域网的浏览器里打开 `http://NAS的IP:8899`，**设置访问密码**（输两遍，提示词可选）。
2. 用 USB 线把 iPhone 或 iPad 接到 NAS，在设备上点“信任”并输入锁屏密码。
3. 页面会让你**选备份文件夹**（会推荐剩余空间最大的磁盘）。
4. 第一次备份请**用 USB 做完**，并且在这时选择是否加密。

如果你所在地区下载 GitHub 仓库或在 NAS 上构建不稳定，可以从 [v0.2.0 发布页](https://github.com/JerryGHT04/ios-backup-station/releases/tag/v0.2.0)下载源码压缩包，并在 `.env` 里设置 `IBS_IMAGE=ghcr.io/jerryght04/ios-backup-station:v0.2.0`。之后运行 `docker compose pull && docker compose up -d`，使用已发布镜像而不在 NAS 上构建。镜像支持 amd64 和 arm64。
5. 要用无线备份：连着 USB 时在“连接与配对”里点“开启 Wi-Fi 同步”；在手机“设置 → Wi-Fi → 家里的网络 → ⓘ”里**关闭“私有无线局域网地址”**（原因见[常见问题](#7-常见问题和排错)）；拔掉 USB，点“检测连接”。
6. 想要夜里自动备份，到“定时备份”里设置。

页面上的**“环境自检”**会告诉你还有什么没配好（比如备份文件夹没有真正挂载进来）。

## 3. 功能一览（截图）

### 访问密码和语言

第一次打开要设置密码，之后每次进来输入一次。输错两次会显示你设的提示词。手机版的密码页右上角有一个小图标，可以切换简体中文或 English。

<table>
<tr>
<td><img src="docs/images/desktop-password.png" alt="设置访问密码" width="460"></td>
<td><img src="docs/images/mobile-password.png" alt="手机版设置密码" width="190"></td>
<td><img src="docs/images/mobile-language.png" alt="语言菜单" width="190"></td>
</tr>
</table>

### 摘要和备份

状态条显示连接方式和上次备份。点“立即备份”开始，进度条和手机版的进度环显示**总进度**（不是单个文件的进度），并给出已用时和剩余时间的估计。取消要点两次确认，取消后上一次完好的备份不受影响。

**删除备份**会把所选设备在 NAS 上的整个备份删掉（要先输入确认的字，备份进行中会被拒绝）。电脑版在同时看到不止一台 iPhone 或 iPad 时，设备名右边会出现**切换设备**；有任务在运行时不能切换。

<table>
<tr>
<td><img src="docs/images/desktop-summary.png" alt="摘要" width="440"></td>
<td><img src="docs/images/desktop-backup-running.png" alt="备份进行中" width="440"></td>
</tr>
</table>

### 备份记录、定时备份、连接与配对

- **备份记录**：每次备份的时间、方式（USB 或无线）、触发（手动或定时）、大小、耗时、是否加密、失败原因。
- **定时备份**：由 NAS 在设定的时间主动连接手机；手机不在线时在你设的窗口内每 10 分钟重试一次；手机锁屏等密码超过 5 分钟就放弃这次。用 NAS 自己的时区。
- **连接与配对**：配对、开启 Wi-Fi 同步、检测连接的步骤，以及“为什么要关闭私有无线局域网地址”的说明。

<table>
<tr>
<td><img src="docs/images/desktop-history.png" alt="备份记录" width="300"></td>
<td><img src="docs/images/desktop-schedule.png" alt="定时备份" width="300"></td>
<td><img src="docs/images/desktop-connection.png" alt="连接与配对" width="300"></td>
</tr>
</table>

### 选择备份位置和环境自检

文件夹选择器只显示你**共享给容器的文件夹**，可以新建文件夹；每台设备可以各选一个（比如 iPhone 一个、iPad 一个）。自检会在备份文件夹没有真正挂载、配对记录没保存、找不到 USB 或 avahi 时给出提示和修法。

<table>
<tr>
<td><img src="docs/images/desktop-folder-picker.png" alt="选择备份位置" width="440"></td>
<td><img src="docs/images/desktop-setup-check.png" alt="环境自检" width="440"></td>
</tr>
</table>

### 手机版和 iPad

手机和 iPad 访问会自动用为触屏设计的界面，布局参考苹果诊断工具：一屏一个重点，进度环，卡片，详情从底部滑出。手机上点“请求桌面网站”则会显示电脑版。手机版有简体中文和 English。

<table>
<tr>
<td><img src="docs/images/mobile-main-en.png" alt="手机版主页" width="190"></td>
<td><img src="docs/images/mobile-running-en.png" alt="进度环" width="190"></td>
<td><img src="docs/images/mobile-backup-en.png" alt="立即备份和备份记录" width="190"></td>
<td><img src="docs/images/mobile-connection-en.png" alt="连接与配对" width="190"></td>
</tr>
</table>

<p align="center"><img src="docs/images/ipad.png" alt="iPad 上的界面" width="420"></p>

### 没有手机也想先看看界面

```bash
python3 tests/run_demo_server.py     # 打开 http://127.0.0.1:8899 ，密码 demo
```

这是真实的服务加一个假手机和示例数据，退出时全部删除，不会碰其他东西。

## 4. 需要哪些权限

容器要和 USB 设备、局域网、系统的 avahi 打交道，所以权限比普通应用大。**请理解这一点再安装。**

| 设置 | 为什么需要 | 意味着什么 |
|---|---|---|
| `privileged: true` | 读写 USB 设备、让 usbmuxd 工作 | 容器里的程序基本等于 NAS 上的 root，只在可信的局域网里运行，不要暴露到公网 |
| `network_mode: host` | 在局域网里发现手机（mDNS），并复用主机的 avahi | 容器直接使用主机的网络，网页端口（默认 8899）会在主机上打开 |
| `/dev/bus/usb` | USB 配对和备份 | 容器能看到所有 USB 设备 |
| `/run/dbus`、`/run/avahi-daemon` | 通过主机的 avahi 发现 Wi-Fi 上的手机 | 容器能连上主机的 avahi 服务；主机没有 avahi 时只影响无线备份 |
| `/etc/localtime`（只读） | 定时备份用 NAS 自己的时区 | 无 |
| `BACKUP_LOCATION`（读写） | 保存备份 | 容器可以在这个文件夹里读写，别的文件夹看不到 |
| `./config`、`./lockdown`（读写） | 保存设置、访问密码的哈希、配对记录 | `lockdown` 里有手机的信任密钥，当作密码对待，不要拷到别处 |

容器以 root 运行。运行时**不需要访问互联网**（构建时需要从 GitHub 和 Debian 下载）。程序**不发送任何统计信息**。

## 5. 免责声明和测试配置

**免责声明**

- 这是个人业余项目，按“现状”提供，**不提供任何担保**（见 [LICENSE](LICENSE)）。
- 与苹果公司无关，也没有得到苹果的授权或认可。iPhone、iPad、iOS、iTunes、Finder、macOS 是苹果公司的商标，这里只是用来说明软件支持哪些设备和程序。
- 备份里有你最私密的数据（照片、消息、健康数据、密码）。**请开启加密，保管好备份密码，并定期做一次恢复测试。** 忘记备份密码就无法恢复。
- 它调用的是第三方库实现的备份协议。苹果更新 iOS 后，协议可能有变化，导致备份失败或备份不完整。
- 出现数据丢失，作者不负责。

**已经测试过的配置（作者的环境）**

| 项目 | 内容 |
|---|---|
| NAS | 飞牛 fnOS（内核 6.18），x86_64，12 核，15.5 GiB 内存，btrfs 存储 |
| Docker | Docker 28.5.2，Compose v2.40.3，基础镜像 Debian 13（trixie） |
| 设备 | iPhone Air（`iPhone18,4`），iOS 27.0.1，USB-C；另用 iPad 打开网页界面 |
| 网络 | 路由器同时提供 2.4G 和 5G，NAS 有线接入 |
| 浏览器 | iPhone 和 iPad 的 Safari，另用 Linux 上的 Chrome 做自动化测试 |
| 资源占用 | 待机时约占一个核心的 1%，内存约 19 MiB |

**在真机上已经验证的**：USB 识别和配对；开启 Wi-Fi 同步；无线发现手机，并能在 USB 和无线之间切换；开启备份加密；用手机和 iPad 打开网页界面；开始和取消备份；**在 Wi-Fi 和 USB 上各完整做完一次加密备份**（iPhone 约 140.6 GB，iPad 约 35.4 GB）；删除备份；以及**从 NAS 将约 132.7 GB（123.6 GiB）加密备份完整恢复到同一台 iPhone 15（iOS 27.0），并进入主屏幕**。

**还没有验证的（请不要假设它们一定可用）**

- 夜里无人值守的定时备份（尤其手机锁屏时是否需要密码）。
- 恢复 iPad。（备份 iPad 是可用的；目前只在 iPhone 15 上完成过 NAS 直连恢复。）
- 手机上点“请求桌面网站”后自动显示电脑版、iPad 自动用手机版布局：判断逻辑在模拟环境里测过，真机上的表现作者还没有逐项确认。
- 其他品牌的 NAS（群晖、威联通、Unraid、TrueNAS……）、arm64、其他 iOS 版本。

**自动化测试**（不需要真机，用一套假的 libimobiledevice 命令跑真实的服务代码）：接口层 39 项，浏览器层 13 项（登录、各个界面、取消、XSS、各种屏幕宽度不溢出、控件有名字），纯逻辑 35 项（定时、看门狗、进度解析、取消后的冷却、自检）。详见[开发和测试](#13-开发和测试)。这些测试能抓到我们自己的逻辑错误，但**不能代替真机**。

## 6. 已知限制

- **没有断点续传。** 备份中途断开（手机离开 Wi-Fi、拔线、出门），已写入的文件还在，但下次备份时手机会重新对比并补传，被打断的那一部分要重新传。第一次完整备份请在家里用 USB 一次做完。
- **同一时间只运行一个备份或恢复任务。** 设备列表、连接操作和定时设置都按设备分别保存；任务会串行执行，避免多个 MobileBackup2 会话互相影响。电脑版和手机版都可以用设备选择器切换当前设备。每台设备的备份文件夹和定时设置分开保存。
- **取消增量备份后，上一份完整快照是否保持不变还未用真机验证。** 备份工具会使用快照文件夹，但这个具体的中途取消场景还没有真机测试；在只保留这一份副本前，请先检查备份。
- **备份可能比 iOS 显示的已用空间大。** APFS 克隆和文件系统压缩不会带入备份，请在 NAS 上预留额外空间。
- **容量使用十进制单位显示**（1 GB = 1,000,000,000 字节），与 iOS 和 NAS 磁盘的标法一致。
- **锁屏时可能需要锁屏密码。** 手机重启后第一次解锁之前，备份一定会要求密码；其他情况下是否需要，取决于手机，作者还没在夜间实测过。
- **不能浏览单个文件。** 这是整机备份，只能整个恢复。
- NAS 网页支持通过 USB 直连恢复；也可以按下面的说明用 Windows 的 Apple 设备/iTunes 或 Mac 的 Finder 恢复。
- **电脑版界面目前只有中文。** 手机版有简体中文和 English；电脑版的英文在计划中。
- **网页是 HTTP，没有 TLS。** 只适合局域网，见[安全和隐私](#10-安全和隐私)。
- **无线备份比 USB 慢，而且占用 Wi-Fi 带宽。** 建议夜里做。
- iOS 26.4 起有手机侧的局域网连接问题，需要关闭“私有无线局域网地址”。

## 7. 常见问题和排错

**页面上“环境自检”提示备份文件夹没有挂载**
容器里看不到你的文件夹，备份会写进容器内部，容器一删就丢。检查 `.env` 里的 `BACKUP_LOCATION` 是不是真实存在的路径，然后 `docker compose up -d` 重新创建容器。

**NAS 找不到手机（无线）**
1. 手机和 NAS 在同一个网段吗？访客网络和有些“IoT 网络”会隔离设备。
2. 手机的 Wi-Fi 同步开了吗？（连着 USB 时点“开启 Wi-Fi 同步”。）
3. 关闭家里 Wi-Fi 的**“私有无线局域网地址”**（设置 → Wi-Fi → 家里的网络右边的 ⓘ）。iOS 26.4 起，打开它时手机在局域网里的连接容易出问题，表现是 NAS 一会儿找得到、一会儿找不到。2.4G 和 5G 如果名字不同，两个都要关。
4. 手机熄屏休眠时可能不应答。让它充电，或先点亮屏幕再点“检测连接”。
5. 主机上有 avahi 吗？“环境自检”会提示。

**换了 Wi-Fi 频段，或者插拔 USB 之后，网页显示的连接方式不对**
usbmuxd2 有时不会重新发现设备，所以服务里有两个“看门狗”：USB 上有设备但没被列出，3 秒后重启 usbmuxd2；手机在局域网里但没被列出，约 8 秒后重启（刚拔掉 USB 的 20 秒内只等 1 秒）。真机上重启 usbmuxd2 要二十秒左右，所以整个过程可能要半分钟。也可以点“检测连接”主动触发。

**备份一开始就卡在“请在手机上输入锁屏密码”**
请解锁手机并输入锁屏密码。如果状态安静超过 15 秒，页面会改为提示“如果已输入密码，手机正在准备备份”；如果还没输入，请先解锁。定时备份等待密码超过 5 分钟会自动放弃，之后重试。

**取消备份后马上开始新操作失败**
手机可能还在结束上一次备份会话。应用会在取消后的最多两分钟内暂停这台设备的新备份、恢复、配对、Wi-Fi 同步和加密设置，并显示剩余等待时间。请保持手机解锁；等待后仍失败，再重新插拔 USB。

**忘记访问密码**
在 NAS 上删除项目文件夹里的 `config/auth.json`，重启容器，再打开网页重新设置。这**不影响**备份和手机上的备份密码。

**忘记备份密码**
没有办法找回，这是 iOS 的设计。请把备份密码存进密码管理器。

**构建很慢或失败**
下载 deb.debian.org 慢的话，在 `.env` 里设置 `APT_MIRROR`（例如 `mirrors.ustc.edu.cn`）。Docker 镜像加速站偶尔会返回 401，重试一次通常就好。

**时间不对**
定时备份用 NAS 的时区，compose 里挂载了 `/etc/localtime`。在 NAS 上运行 `date` 看看对不对。

## 8. 怎么恢复备份

**实验功能：从 NAS 直接恢复。** 我们已在一台 iPhone 15（iOS 27.0）上实测恢复约 132.7 GB（123.6 GiB）加密备份，并确认手机进入主屏幕。其他机型、iOS 版本和备份情况还需要更多实测。欢迎大家测试，并反馈设备型号、iOS 版本，以及是否正常完成恢复。恢复只能用于创建该备份的同一台设备，目标 iOS 版本不能低于备份版本；恢复期间请保持 USB 连接。手机接收完数据后会重启；NAS 无法代替手机确认这一步是否成功。等手机重启进入系统、解锁并检查数据后，再在备份记录中手动点“确认恢复成功”。

恢复原理：恢复程序运行在 NAS 上，但真正解密并写入数据的是 iPhone 上的 Apple BackupAgent2。手机通过 Apple MobileBackup2 协议分批向 NAS 请求清单和文件；NAS 从原备份读取对应内容，通过 USB/usbmuxd 传给手机。开始前 NAS 会校验备份密码并读取加密清单；照片、应用数据等备份内容不会先整体解密到 NAS。手机收到并处理完数据后会继续完成设置并重启。由于手机重启期间 NAS 无法判断系统是否已恢复完成，**“传输完成”只代表协议传输成功，不代表恢复成功**。请等手机重启后进入锁屏或主屏幕、解锁并检查数据，再在备份记录中点“确认恢复成功”；记录此时才会变成绿色的“恢复成功”。

直连恢复使用独立的 MobileBackup2 协议进程；页面显示发送字节数、预计总时长和剩余时长。时长会根据实际传输速度估算，属于近似值；手机处理或重启时可能暂时没有传输活动，估算也可能变化。每次尝试的诊断保存在配置目录的 `restore-logs/`，实现细节见 [恢复引擎](docs/restore-engine.md)。

平时服务会保持 `usbmuxd2` 运行，以便通过 Wi-Fi 发现设备。只有 NAS 正在执行直连恢复时，才会临时切换到标准的 USB 专用 `usbmuxd`；恢复结束后（包括取消或失败）会自动切回 `usbmuxd2`，恢复无线发现。

如果直连恢复不成功，可以按下面的方法用 Apple 设备、iTunes 或 Finder 恢复：

**Windows（“Apple 设备”应用，或 Microsoft Store 版 iTunes）**

1. 把 NAS 上的备份文件夹（名字是手机的 UDID）拷到 Windows。
2. 放进 `%USERPROFILE%\Apple\MobileSync\Backup\`（比如 `C:\Users\你的用户名\Apple\MobileSync\Backup\`），**文件夹名不要改**。官网安装包版的老 iTunes 用的是 `%APPDATA%\Apple Computer\MobileSync\Backup\`。
3. 先在手机上关闭“查找我的 iPhone”，再用线连接。iTunes：点左上角手机图标，在“摘要”里点“恢复备份”；“Apple 设备”应用：选中设备，在“常规”里点“恢复备份…”。选这个备份，备份加密了就输入备份密码。

**macOS（Finder）**

1. 把备份文件夹拷到 Mac（在 Finder 里“前往 → 连接服务器”连上 NAS）。
2. 在 Finder 里按 ⇧⌘G，粘贴 `~/Library/Application Support/MobileSync/Backup/`，把文件夹拖进去，名字不要改。
3. 用线连接 iPhone，在 Finder 左侧点这台 iPhone，在“通用”里点“恢复备份…”，选这个备份。
4. macOS Mojave 及更早的系统用 iTunes，步骤和 Windows 一样。

页面的“备份记录”里有这些路径的一键复制。**请在真的需要之前，先用一台备用机或一次不重要的恢复试一遍。**

## 9. 升级、卸载、备份配置

**升级**

```bash
git pull
docker compose up -d --build     # 或者设了 IBS_IMAGE 就用：docker compose pull && docker compose up -d
```

`config` 和 `lockdown` 文件夹不受影响，密码和配对都在。

**卸载**

```bash
docker compose down              # 删除容器；备份文件夹和 config、lockdown 都还在
docker rmi ios-backup-station    # 删除镜像（可选）
```

要彻底删除，再手动删除备份文件夹、`config` 和 `lockdown`。

**备份这个项目自己的配置**
把 `config` 和 `lockdown` 两个文件夹拷走就够了：里面有设置、访问密码的哈希、历史记录和手机的配对记录。**`lockdown` 含有信任密钥，请当作密码保管。**

## 10. 安全和隐私

- **所有数据只在你的局域网和 NAS 里**，程序不联网、不上报。
- **访问密码**：用 PBKDF2-SHA256（20 万次）加盐存哈希，不存明文；登录状态用签名的 cookie（HttpOnly，SameSite=Lax），保持 30 天；同一个地址连续输错 10 次会被锁一分钟；提示词只在输错两次后才显示。
- **备份密码**不在 NAS 上保存：它只在你开启或关闭加密的那一刻经过服务（通过环境变量传给备份工具），之后保存在手机里。日志里不会出现它（有测试检查）。
- **网页是明文 HTTP**，局域网里的其他设备理论上能截获密码。不要把端口映射到公网。需要的话，放在带 HTTPS 的反向代理后面。
- 网页界面里来自设备或文件夹的文字都按纯文本显示，不会被当成 HTML 执行（有测试检查）。
- 这个容器是特权容器（见[需要哪些权限](#4-需要哪些权限)）。

发现安全问题请通过 Issue 联系，如果涉及严重漏洞，请先私下联系维护者。

## 11. 和同类方案的区别

下面按各项目当前公开的说明整理，**没有对其他项目逐项安装和实测**。功能和授权可能更新，请以各自项目页为准。这里把“无线同步”和“备份”分开说明：Apple 文档里的 Wi-Fi 同步主要指电脑与设备同步内容，不等同于 NAS 定时备份。

| 对比项 | ios-backup-station | iTunes / Finder / Apple Devices | [AirVault](https://github.com/wizier/airvault) | [iosbackup](https://github.com/razeencheng/iosbackup) |
|---|---|---|---|---|
| 备份存放 | 你选择的 NAS 目录；按设备保存 iTunes 兼容的整机备份 | 电脑本地 | NAS 本地目录；面向 Unraid 的应用模板也可用 | Linux 主机或 NAS 上的指定目录 |
| 日常是否需要电脑 | 不需要；NAS 运行服务即可 | 需要由电脑端应用管理；无线连接时电脑必须开机 | 不需要 | 不需要 |
| 连接方式 | USB 或 Wi-Fi 备份；从 NAS 直连恢复目前走 USB | USB；也可先用 USB 开启 Wi-Fi 同步 | USB 或 Wi-Fi | USB 或 Wi-Fi |
| 备份触发 | 页面手动发起；也可设置 NAS 定时主动连接，并在时间窗口内重试 | 在电脑端发起备份；Wi-Fi 同步可在设备接电、电脑开机且同一 Wi-Fi 时自动开始同步内容 | 支持自动备份窗口；详见项目说明 | 手动或按设备设置计划、时间段、电量和充电条件 |
| 历史版本 | 页面记录任务；不提供可选择的历史恢复点。需要多版本时可配合 NAS 快照 | 由电脑端的设备备份管理 | 增量、版本化备份，可选恢复点 | 公开说明提供备份状态和文件列表；版本保留策略请查项目文档 |
| 加密 | 支持 iOS 加密备份；备份密码不保存在 NAS | 支持加密本地备份 | 支持可选备份加密 | 支持备份加密及密码管理 |
| 恢复 | 可从 NAS 直接恢复到原设备，**实验功能**；也可复制备份到电脑用 Apple 软件恢复 | 从电脑上的备份恢复 | 网页界面提供恢复和恢复点选择 | 整机恢复仍标为实验功能，默认关闭；也可使用 Apple Devices、iTunes 或 Finder |
| 网页与管理 | 桌面、手机和 iPad 网页界面；环境自检、进度和任务记录 | 没有 NAS 网页管理界面 | 网页界面；Unraid 模板部署 | 网页管理；备份状态、阶段和文件列表，并可选配置通知 |
| 开源授权 | MIT | Apple 专有软件 | GPL-3.0-or-later | AGPL-3.0-only |
| 兼容与验证 | Docker；目前 NAS 直连恢复实测为 iPhone 15、iOS 27.0 | Windows 上的 Apple Devices / iTunes，或 macOS Finder | Docker，面向 Unraid；项目列出 amd64、arm64 | Linux / NAS Docker，项目列出 amd64、arm64 |

### 怎么选

- **想让 NAS 自己保存和定时创建备份，并通过浏览器管理**：ios-backup-station 或 iosbackup 都支持 USB / Wi-Fi 和 NAS 定时任务。ios-backup-station 的定时任务由 NAS 按设定的时间主动连接设备；iosbackup 还公开列出了按电量、充电状态和时间段控制任务，以及可选通知。
- **更看重多版本留存和从不同时间点恢复**：AirVault 的公开说明列出增量、版本化备份和恢复点选择。ios-backup-station 目前的网页记录的是任务结果，不是可切换的历史备份快照；可以在 NAS 文件系统层另行配置快照。
- **习惯用苹果官方界面恢复，或者要从电脑备份**：使用 Apple Devices、iTunes 或 Finder。备份保存在电脑；无线同步需要电脑开机并在同一 Wi-Fi。Apple 文档要求先在电脑端为设备启用 Wi-Fi 同步；NAS 直连恢复不能用这条无线同步链路替代。
- **希望从 NAS 网页直接恢复**：ios-backup-station 已加入 USB 直连恢复，但仍是实验功能。目前仅有 iPhone 15（iOS 27.0）完整恢复约 123.6 GiB 加密备份并进入主屏幕的实测；手机重启后还需要手动检查并确认结果。iosbackup 也把整机恢复列为默认关闭的实验功能；AirVault 的公开说明列出网页恢复和版本点选择。
- **部署平台是 Unraid**：AirVault 提供 Unraid 社区应用模板；其他 NAS/Linux 也可用 Docker，但安装方式要按对应文档配置。

本项目的取舍是：提供 MIT 开源的 NAS 网页管理、iTunes 兼容备份、计划任务、自检，以及刚开始实测的 NAS 直连恢复。它还没有 AirVault 所列的版本化恢复点；恢复支持的设备和系统版本也需要社区继续测试。竞品功能依据其[项目说明](https://github.com/wizier/airvault)、[iosbackup 文档](https://github.com/razeencheng/iosbackup)和 [Apple Wi-Fi 同步指南](https://support.apple.com/en-sg/guide/itunes/itns3751d862/windows)整理，未进行同条件性能或可靠性对测。

## 12. 工作原理

```mermaid
flowchart LR
  B[浏览器 / 手机 / iPad] -- HTTP 8899 --> S[server.py 网页和接口]
  S -- 调用 --> T[libimobiledevice 工具<br/>idevicebackup2 等]
  T -- USB 或 Wi-Fi --> D[(iPhone / iPad)]
  U[usbmuxd2] <-- 连接管理 --> T
  U -- USB --> D
  A[主机的 avahi / mDNS] -- 发现 --> U
  S -- 看门狗 --> U
  T -- 写入 --> F[(备份文件夹)]
```

- `app/server.py`：用 Python 标准库写的网页服务，没有第三方依赖。它**调用命令行工具**，解析输出里的总进度，管理任务、定时和看门狗。
- `app/static/index.html`、`mobile.html`：两套界面，都是单个文件，没有构建步骤。
- `tools/wifi_sync.c`：一个很小的工具，用来打开手机的“Wi-Fi 同步”（标准工具里没有）。
- `Dockerfile`：从上游源码构建 libimobiledevice 一套库和 usbmuxd2（见[致谢](#14-致谢和许可证)）。

## 13. 开发和测试

不需要真机。`tests/fakephone/` 是一组假的 libimobiledevice 命令，由一个状态文件控制（拔插 USB、掉线、锁屏、备份失败……），真实的服务代码在上面运行。

```bash
python3 -I tests/test_logic.py                # 纯逻辑，不到 1 秒
python3 -I tests/test_backend.py              # 接口层，约 4 分钟（其中一项要等定时器触发）
pip install playwright pillow                 # 浏览器测试用本机的 Chrome
python3 -I tests/test_ui.py                   # 浏览器层，约 2 分钟
python3 tests/run_demo_server.py              # 带示例数据的演示
python3 tests/make_screenshots.py             # 重新生成 docs/images 里的截图
```

测试已经抓到过的真实问题包括：备份结束时的一行日志写错导致任务状态清不掉、参数没传导致所有备份失败、GET 请求带空字符会让处理线程崩溃、对话框按 Esc 关不掉、进度条把单个文件的进度当成总进度。

## 14. 致谢和许可证

本项目的代码使用 [MIT 许可证](LICENSE)。

它依赖下面这些项目，镜像里包含它们，各自有自己的许可证（详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）：

- [libimobiledevice](https://github.com/libimobiledevice/libimobiledevice)（含 `idevicebackup2`）、libplist、libusbmuxd、libimobiledevice-glue、libtatsu：LGPL-2.1
- [usbmuxd2](https://github.com/tihmstar/usbmuxd2) 和 libgeneral（tihmstar）：LGPL-3.0 和 LGPL-2.1
- Debian 基础镜像和软件包

感谢这些项目的作者，没有他们就没有这个项目。

## 15. 请我喝杯咖啡

这个项目免费，用 MIT 协议开源，不付费也能完整使用。如果它帮你省了些麻烦，想请我喝杯咖啡，可以用微信扫下面的码。完全自愿，不会有额外的功能或支持。

<p align="center"><img src="docs/images/wechat-pay.png" alt="微信收款码" width="220"></p>
