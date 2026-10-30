---
name: elder-phone-makeover
description: 把一部普通安卓手机改造成适合老人使用的「大字桌面」——大时钟与农历、天气与电量、最多 6 个大图标、打电话/电话簿/更多三大按钮、一键清理、话费流量查询，并设为默认桌面。基于项目-0041 已在真机验证的成品 APK 与离线工具链。This skill should be used when the user (often an adult child with little technical skill) wants to set up, install, configure, or roll back an elderly-friendly Android launcher for a parent or grandparent, teach the elder to use it, or troubleshoot the setup. 触发场景包括：给老人/父母/长辈弄手机、老人手机改造、适老化/老年桌面、大字大图标桌面、把手机调成老人模式、给长辈装机、一键清理后台、设置默认桌面、教老人用智能手机，以及针对该桌面的连机、安装、验收、回退。使用者是只会简单用 AI 的小白，需全程用生活语言引导、一次只让其做一个动作。
agent_created: true
---

# 长辈手机适老化改造

> 给人看的介绍、截图、快速上手与验证状态见 [README.md](README.md)。
> 本文件供 AI 读取，是技能的触发与执行入口。

## 用途

把一个只会简单用 AI 的小白，通过**对话**带完整个改造闭环：连机检测 → 安装 → 设为默认桌面
→ 按老人习惯配置 → 验收 → 教老人使用 → 回退。技术操作由脚本完成，小白只需在手机上做
「插线 / 点允许 / 按主页键 / 解锁」这类必须真人完成的动作。

底料来自项目-0041「安卓适老化桌面」，其成品 APK 与离线工具链均已真机验证（Nokia X7 / 安卓 9）。

## 何时使用

- 用户想把自家老人/父母的安卓手机改得好用（大字、少图标、好打电话）。
- 用户已有一个"长辈桌面"要安装、配置、验收、教用或卸载。
- 用户抱怨老人手机太复杂、字太小、软件太多、容易点错。

## 铁律（最先遵守）

1. **服务对象是小白，不是开发者。** 全程说生活语言，绝不把 adb、gradle、SDK、组件名、
   "未 exported"、错误码直接抛给用户。脚本 JSON 里的 `raw`/`error`/`data` 只给 AI 看，
   转述时翻成大白话。
2. **一次只让用户做一个动作**，等其回复「好了」再给下一步。
3. **主动安抚**：反复强调安装/配置**不会删**老人的照片、微信、通讯录、聊天记录，随时能换回。
4. **脚本能做的绝不让用户做**；只有解锁、按主页键、点授权框这类必须真人在手机上做的才让做。
5. **危险操作先确认**：卸载、改系统默认项，先讲清影响并取得明确同意。
6. **该选择时用 AskUserQuestion 给 2–3 个互斥选项**，不要开放式追问、不要连珠炮。

## 工作流（严格按 `references/01_改造流程.md` 执行）

先读该文件再开始。七个阶段概览：

| 阶段 | 目标 | 关键动作 |
|---|---|---|
| 0 摸底 | 了解情况、安抚 | 纯对话；问是谁的手机、有没有能传数据的数据线；给"现在开始/先了解"选项 |
| 1 连机 | 检测到手机 | `check`；按 `NO_ADB`/`NO_DEVICE`/`unauthorized`/`multiple` 分支引导 |
| 2 安装 | 装上成品桌面 | `info` → 够新且未装则 `install`（默认用自带 APK，**不要主动编译**） |
| 3 设默认 | 一按主页键回大字桌面 | `home`；未设则让用户在手机上按主页键选「长辈桌面」点「始终」 |
| 4 按家配置 | 放常用 App、填天气城市、录联系人头像、调字号 | 长按大时钟进设置；逐项引导，见 `03` |
| 5 验收 | 确认装好且能用 | `verify`（会先判锁屏/息屏）；人工点一键清理、让老人 5 秒内找到微信 |
| 6 教用交接 | 教老人、留一页纸 | 见 `06`，交 `assets/给家人的一页纸.md` |
| 7 回退 | 不想要了 | `rollback --confirm`，或手机设置里改默认桌面/卸载；须先确认 |

## 脚本用法

脚本 `scripts/elder_tool.py` 用 Python 3 运行（命令行里的 `python` 即本机的 Python 3
解释器；Windows 可用 `py -3`，或你本机/WorkBuddy 自带的 python 绝对路径）。
它自动探测 adb；`--json` 输出结构化结果供判断，不加则输出可直接念给用户的人话。

```
python scripts/elder_tool.py --json check                # 连机体检（只读）
python scripts/elder_tool.py --json info                 # 设备/系统/是否已装/默认桌面（只读）
python scripts/elder_tool.py --json install              # 安装（默认用 assets 自带成品 APK）
python scripts/elder_tool.py --json home                 # 默认桌面状态 + 设置引导语
python scripts/elder_tool.py --json verify               # 验收（只读，能识别锁屏/息屏）
python scripts/elder_tool.py --json rollback --confirm   # 卸载回退（危险，必须先确认）
python scripts/build_apk.py --json                      # 可选：有源码+工具链时离线重编译
```

统一返回结构：`{"ok", "cmd", "data", "next", "error"}`。`data.next` 已含可直接转述的人话，
按 `error` 分支处理。adb 找不到时先让脚本自动探测，仍找不到再按 `02_连机与环境.md`
定位，或用 `--adb "你的adb绝对路径"` / 环境变量 `ELDER_ADB` 指定。

脚本安全约定：`check/info/home/verify` 只读不改手机；`install` 只覆盖安装本应用；
只有 `rollback` 会卸载且必须显式 `--confirm` 并先获用户同意；脚本不模拟电量、不改无障碍设置。

## 资源索引（按需读取，不必一次读完）

- `references/01_改造流程.md` — **主剧本**，含每阶段决策门、话术、命令、成败判据。先读。
- `references/02_连机与环境.md` — USB 调试各品牌路径、数据线/授权/掉线排查、adb 定位、隐私边界。
- `references/03_按家配置.md` — 常用 App、天气城市、联系人+相册头像、字号、话费套餐、恢复默认。
- `references/04_兼容与限制.md` — 系统门槛、一键清理边界、无障碍已弃用、运营商话费现状、权限清单、回退。
- `references/05_故障排查.md` — 真机踩坑提炼（组件名全限定、非 exported、MSYS 路径、输入法吞 ASCII、电池 reset 等）。
- `references/06_教老人与交接.md` — 可直接念给老人的话术、让老人当场做的三动作、维护与回退话术。
- `assets/elder-launcher.apk` — 已验证的成品桌面 APK，`install` 默认使用它。
- `assets/给家人的一页纸.md` — 可打印、贴手机旁的留底说明，改造完成时交给用户。
- `scripts/elder_tool.py` — 核心自动化（见上）。
- `scripts/build_apk.py` — 可选离线重编译；默认不需要。

## 关键事实（避免重复踩坑）

- 应用包名 `com.elderhome.launcher.debug`，主屏组件名必须**全限定**
  `com.elderhome.launcher.debug/com.elderhome.launcher.MainActivity`（脚本已内置）。
- 一键清理**零开关**：用普通权限结束第三方后台进程，安装即授予、不会掉线，
  **不需要家人开无障碍**。旧版依赖无障碍会反复掉线，已彻底移除；系统里残留的开关保持关闭即可。
- **安卓 14+** 系统禁止第三方结束他人进程，清理页会如实提示，不是故障。
- 联通短信营业厅 2026-10 起不再返回话费/流量数字，页面如实说明；流量本机仍可算。
- 电话簿人数无上限；桌面常用 App 最多 6 个（建议别放满）。
- 验完电池模拟务必 `dumpsys battery reset`；本技能脚本不做电池模拟，手动验收见 `05`。
