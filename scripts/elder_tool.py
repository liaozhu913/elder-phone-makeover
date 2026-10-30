#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长辈桌面 · 老人手机适老化改造助手（供 AI 调用）

设计目标：让「只会简单用 AI 的小白」也能在 AI 引导下，把自家老人的安卓手机
改造成适老化桌面。本脚本把连机检测、安装、启动、默认桌面、验收等确定性操作
封装起来，避免 AI 每次重写 adb 命令，也避开 Git Bash 的 /sdcard 路径转换坑。

输出两种模式：
  默认       —— 中文人话，适合直接念给小白听
  --json     —— 纯 JSON，适合 AI 精确解析后决定下一步与话术

统一返回结构：{"ok", "cmd", "data", "next", "error"}

子命令：
  check                 连机体检（只读，绝不改动手机）
  info                  设备型号 / 安卓版本 / 是否已装 / 默认桌面 / 无障碍（只读）
  install [--apk 路径]   安装长辈桌面（默认用 assets 自带成品 APK）
  launch                冷启动主屏（全限定组件名）
  home                  查看当前默认桌面并给出「按 HOME 设为默认」引导语
  verify                验收自检：主屏元素、天气电量、无障碍、清理能力（只读）
  rollback --confirm     卸载长辈桌面、让系统回原厂桌面（危险，必须带 --confirm）

通用参数：
  --adb PATH   指定 adb 可执行文件（找不到时用它）
  --serial SN  指定设备（多设备时）
  --json       输出纯 JSON

安全约定：
  - check / info / home / verify 全部只读，不改手机任何状态。
  - install 只覆盖安装本应用，不动其他软件与数据。
  - rollback 会卸载本应用，必须显式 --confirm，执行前 AI 需先向用户确认。
  - 不在本脚本里模拟电量、不关闭/开启无障碍等系统设置（见 references 手动验收章节）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PKG = "com.elderhome.launcher.debug"
MAIN_COMPONENT = "com.elderhome.launcher.debug/com.elderhome.launcher.MainActivity"
APP_LABEL = "长辈桌面"

SELF_DIR = Path(__file__).resolve().parent
SKILL_DIR = SELF_DIR.parent
BUNDLED_APK = SKILL_DIR / "assets" / "elder-launcher.apk"


def _enc_fix():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _subprocess_env():
    """禁用 MSYS/Git Bash 对 /sdcard 等参数的路径转换。"""
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"
    env["MSYS2_ARG_CONV_EXCL"] = "*"
    return env


def locate_adb(explicit=None):
    """按优先级定位 adb：显式 --adb > ELDER_ADB > ANDROID_HOME/SDK_ROOT > PATH > 常见安装位置。"""
    candidates = []
    if explicit:
        candidates.append(explicit)
    elder_adb = os.environ.get("ELDER_ADB")
    if elder_adb:
        candidates.append(elder_adb)
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        base = os.environ.get(var)
        if base:
            candidates.append(os.path.join(base, "platform-tools", "adb.exe"))
            candidates.append(os.path.join(base, "platform-tools", "adb"))
    which = shutil.which("adb")
    if which:
        candidates.append(which)
    home = str(Path.home())
    candidates += [
        os.path.join(home, "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
        "/usr/local/bin/adb",
        "/usr/bin/adb",
    ]
    seen = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.isfile(c):
            return c
    return None


def run(args, timeout=60):
    """执行一条命令，返回 (returncode, stdout+stderr 文本)。"""
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            env=_subprocess_env(),
        )
        out = ""
        for raw in (p.stdout, p.stderr):
            if raw:
                out += raw.decode("utf-8", errors="replace")
        return p.returncode, out.strip()
    except FileNotFoundError:
        return 127, "找不到可执行文件：%s" % (args[0] if args else "?")
    except subprocess.TimeoutExpired:
        return 124, "命令超时（%ss）" % timeout
    except Exception as exc:  # noqa: BLE001 - 上报而非静默吞掉
        return 1, "执行异常：%s" % exc


class Bridge:
    def __init__(self, adb, serial=None):
        self.adb_path = adb
        self.serial = serial

    def _prefix(self):
        return [self.adb_path, "-s", self.serial] if self.serial else [self.adb_path]

    def adb(self, *args, timeout=60):
        return run(self._prefix() + list(args), timeout=timeout)

    def shell(self, *args, timeout=60):
        # 设备端命令用独立 argv 传入，不经 bash，规避路径转换。
        return run(self._prefix() + ["shell"] + list(args), timeout=timeout)

    def getprop(self, key):
        rc, out = self.shell("getprop", key)
        return out.strip() if rc == 0 else ""

    def devices(self):
        rc, out = self.adb("devices", "-l")
        result = []
        if rc != 0:
            return result, out
        known = {"device", "unauthorized", "offline", "recovery", "sideload", "no permissions"}
        for line in out.splitlines():
            line = line.strip()
            # 过滤表头与 adb daemon 启动提示（"* daemon ..."），这些不是设备行
            if not line or line.startswith("*") or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            if state == "no" and len(parts) > 2 and parts[2] == "permissions":
                state = "no permissions"
            if state not in known:
                continue
            model = ""
            m = re.search(r"model:(\S+)", line)
            if m:
                model = m.group(1)
            result.append({"serial": serial, "state": state, "model": model})
        return result, out


def emit(result, as_json, human=None):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if human:
            print(human)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def need_bridge(args):
    adb = locate_adb(args.adb)
    if not adb:
        return None, {
            "ok": False, "cmd": args.command,
            "data": {}, "error": "NO_ADB",
            "next": "这台电脑还没找到 adb（安卓调试工具）。请让 AI 帮忙："
                    "① 若用当前工作区，传 --adb 指向 90_本地工具链/android-sdk/platform-tools/adb.exe；"
                    "② 或安装 Android SDK Platform-Tools 并加入 PATH；"
                    "③ 或在系统设置里开启手机的「USB 调试」。",
        }
    return Bridge(adb, args.serial), None


def pick_device(br, serial):
    """返回 (serial, state, 附加提示)。"""
    devices, raw = br.devices()
    if serial:
        for d in devices:
            if d["serial"] == serial:
                return serial, d["state"], ""
        return serial, "missing", "指定的设备没连上，请检查数据线或重新插拔。"
    online = [d for d in devices if d["state"] == "device"]
    unauthorized = [d for d in devices if d["state"] == "unauthorized"]
    if len(online) == 1:
        return online[0]["serial"], "device", ""
    if len(online) > 1:
        br.serial = None
        return None, "multiple", "检测到多台设备，请用 --serial 指定：\n" + "\n".join(
            "  %s  %s" % (d["serial"], d.get("model", "")) for d in online)
    if unauthorized:
        return None, "unauthorized", "手机上弹出了「是否允许 USB 调试」——请在手机上勾选「一律允许」再点确定。"
    return None, "none", (
        "还没检测到手机。请按顺序检查：\n"
        "  1) 用数据线把老人手机连到电脑（尽量用原装线，能传数据不是只能充电）；\n"
        "  2) 手机「设置 → 关于手机 → 连续点 7 次版本号」打开开发者选项；\n"
        "  3) 在「开发者选项」里打开「USB 调试」；\n"
        "  4) 手机上弹出授权框时点「允许」。"
    )


def cmd_check(args):
    br, err = need_bridge(args)
    if err:
        return emit(err, args.json)
    rc, ver = run([br.adb_path, "version"])
    adb_version = ver.splitlines()[0] if rc == 0 and ver else "未知"
    devices, raw = br.devices()
    # 若显式指定了 --serial，check 也要遵守：只在目标设备在线时算连上。
    if args.serial:
        devices = [d for d in devices if d["serial"] == args.serial]
    online = [d for d in devices if d["state"] == "device"]
    unauthorized = [d for d in devices if d["state"] == "unauthorized"]
    data = {
        "adb_path": br.adb_path,
        "adb_version": adb_version,
        "devices": devices,
        "online_count": len(online),
        "unauthorized_count": len(unauthorized),
        "bundled_apk": str(BUNDLED_APK) if BUNDLED_APK.is_file() else None,
        "bundled_apk_size": BUNDLED_APK.stat().st_size if BUNDLED_APK.is_file() else 0,
    }
    if args.serial and len(online) == 0:
        result = {"ok": False, "cmd": "check", "data": data,
                  "next": "指定的设备（%s）没连上，请检查数据线或重新插拔；也可不带 --serial 让脚本自动找。" % args.serial,
                  "error": "NO_DEVICE"}
        return emit(result, args.json, _human_check(result))
    if len(online) == 1:
        ok = True
        next_step = "手机已连上，接着可以让 AI 帮你查设备信息（info）或直接安装（install）。"
    elif len(online) > 1:
        ok = False
        next_step = "检测到多台设备，请用 --serial 指定要改造的那台老人手机。"
    elif unauthorized:
        ok = False
        next_step = "手机弹了 USB 调试授权框，请在手机上点「允许」（可勾「一律允许」）。"
    else:
        ok = False
        next_step = (
            "没检测到手机。请：用数据线连上老人手机 → 设置→关于手机→连点 7 次版本号 "
            "打开开发者选项 → 开发者选项里开「USB 调试」→ 手机上点「允许」。"
        )
    result = {"ok": ok, "cmd": "check", "data": data, "next": next_step, "error": "" if ok else "NO_DEVICE"}
    human = _human_check(result)
    return emit(result, args.json, human)


def _human_check(r):
    d = r["data"]
    lines = ["【连机体检】"]
    lines.append("  调试工具：%s（%s）" % (d["adb_version"], d["adb_path"]))
    if d["online_count"] == 0 and d["unauthorized_count"] == 0:
        lines.append("  手机：没连上")
    else:
        for dev in d["devices"]:
            lines.append("  手机：%s  状态=%s  %s" % (dev["serial"], dev["state"], dev.get("model", "")))
    lines.append("  安装包：%s" % (("%d KB" % (d["bundled_apk_size"] // 1024)) if d["bundled_apk"] else "缺失"))
    lines.append("  结论：%s" % r["next"])
    return "\n".join(lines)


def cmd_info(args):
    br, err = need_bridge(args)
    if err:
        return emit(err, args.json)
    serial, state, hint = pick_device(br, args.serial)
    if state != "device":
        result = {"ok": False, "cmd": "info", "data": {}, "next": hint, "error": state}
        return emit(result, args.json, "【设备信息】\n  " + hint)
    br.serial = serial
    # 是否已安装
    rc, pkgs = br.shell("pm", "list", "packages", PKG)
    installed = PKG in pkgs
    # 版本
    ver = ""
    if installed:
        rc, out = br.shell("dumpsys", "package", PKG)
        m = re.search(r"versionName=(\S+)", out)
        ver = m.group(1) if m else ""
    # 默认桌面
    rc, home = br.shell("cmd", "package", "resolve-activity", "--brief",
                        "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME")
    default_home = home.strip().splitlines()[-1].strip() if home.strip() else ""
    # 无障碍
    rc, acc = br.shell("settings", "get", "secure", "enabled_accessibility_services")
    acc = acc.strip()
    data = {
        "serial": serial,
        "brand": br.getprop("ro.product.brand"),
        "model": br.getprop("ro.product.model"),
        "android_release": br.getprop("ro.build.version.release"),
        "sdk": br.getprop("ro.build.version.sdk"),
        "installed": installed,
        "installed_version": ver,
        "default_home": default_home,
        "is_default_launcher": PKG in default_home or "elderhome" in default_home.lower(),
        "accessibility_services": acc,
        "elder_accessibility_on": "elderhome" in acc.lower(),
        "supports_direct_clean": _sdk_int(br.getprop("ro.build.version.sdk")) < 34,
    }
    sdk = _sdk_int(data["sdk"])
    if sdk and sdk < 26:
        next_step = "这台手机系统太老（安卓 %s，低于 8.0），装不了长辈桌面，建议换一台安卓 8.0 以上的手机。" % data["android_release"]
        ok = False
    elif not installed:
        next_step = "手机符合条件但还没装长辈桌面，下一步执行安装（install）。"
        ok = True
    elif not data["is_default_launcher"]:
        next_step = "已安装但还不是默认桌面，下一步教老人「按 HOME 键 → 选长辈桌面 → 点始终」（home 命令有引导语）。"
        ok = True
    else:
        next_step = "已经装好并设为默认桌面了，可以进入按家配置（天气城市、常用 App、联系人）与验收（verify）。"
        ok = True
    result = {"ok": ok, "cmd": "info", "data": data, "next": next_step, "error": "" if ok else "OLD_SYSTEM"}
    return emit(result, args.json, _human_info(result))


def _sdk_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _human_info(r):
    d = r["data"]
    lines = ["【设备信息】"]
    lines.append("  手机：%s %s，安卓 %s（SDK %s）" % (d["brand"], d["model"], d["android_release"], d["sdk"]))
    lines.append("  长辈桌面：%s%s" % ("已安装" if d["installed"] else "未安装",
                                    ("（版本 %s）" % d["installed_version"]) if d["installed_version"] else ""))
    lines.append("  默认桌面：%s" % ("已是长辈桌面 ✔" if d["is_default_launcher"] else (d["default_home"] or "读不到，按 HOME 键时可设置")))
    lines.append("  一键清理能力：%s" % ("支持（系统低于安卓 14）" if d["supports_direct_clean"] else "系统较新，自动清理被限制，会如实提示"))
    lines.append("  无障碍服务：%s" % ("检测到长辈桌面（注意：新版已不依赖它，可忽略）" if d["elder_accessibility_on"] else "无长辈桌面（正常，清理不依赖它）"))
    lines.append("  下一步：%s" % r["next"])
    return "\n".join(lines)


def cmd_install(args):
    br, err = need_bridge(args)
    if err:
        return emit(err, args.json)
    serial, state, hint = pick_device(br, args.serial)
    if state != "device":
        result = {"ok": False, "cmd": "install", "data": {}, "next": hint, "error": state}
        return emit(result, args.json, "【安装】\n  " + hint)
    br.serial = serial
    apk = args.apk or str(BUNDLED_APK)
    if not os.path.isfile(apk):
        result = {"ok": False, "cmd": "install", "data": {"apk": apk},
                  "next": "找不到安装包 %s。可用 --apk 指定 APK 路径；或让 AI 从成品工程离线编译一份（build_apk.py）。" % apk,
                  "error": "APK_NOT_FOUND"}
        return emit(result, args.json, "【安装】\n  " + result["next"])
    rc, out = br.adb("install", "-r", "-d", apk, timeout=180)
    success = "Success" in out
    if success:
        next_step = "装好啦！下一步教老人把它设为默认桌面：按一下手机 HOME（主页）键 → 选「长辈桌面」→ 点「始终」。"
    else:
        # 常见失败原因给出人话解释
        if "signature" in out.lower() or "incompatible" in out.lower():
            reason = "新旧版本签名不一致。需先卸载旧的长辈桌面再装（会让家人帮着确认一下，卸载不影响老人手机里的照片和软件）。"
        elif "no space" in out.lower():
            reason = "手机存储空间不足，先清出一点空间再试。"
        else:
            reason = "安装被拒绝。常见原因：手机没在「USB 安装」里允许通过电脑装应用，或系统拦截了「未知来源」安装。"
        next_step = "安装没成功：%s\n  技术信息：%s" % (reason, out[-300:])
    data = {"apk": apk, "apk_size": os.path.getsize(apk), "serial": serial, "raw": out[-500:]}
    result = {"ok": success, "cmd": "install", "data": data, "next": next_step, "error": "" if success else "INSTALL_FAILED"}
    return emit(result, args.json, "【安装】\n  %s" % next_step)


def cmd_launch(args):
    br, err = need_bridge(args)
    if err:
        return emit(err, args.json)
    serial, state, hint = pick_device(br, args.serial)
    if state != "device":
        return emit({"ok": False, "cmd": "launch", "data": {}, "next": hint, "error": state}, args.json, "【启动】\n  " + hint)
    br.serial = serial
    # 组件名必须全限定：applicationId 带 .debug，Java 包名不带。
    rc, out = br.shell("am", "start", "-n", MAIN_COMPONENT)
    success = rc == 0 and "Error" not in out
    next_step = "已把长辈桌面切到前台，可在手机上看效果。" if success else \
        "启动失败，可能没装或组件名不对。技术信息：%s" % out[-300:]
    data = {"component": MAIN_COMPONENT, "serial": serial, "raw": out[-300:]}
    return emit({"ok": success, "cmd": "launch", "data": data, "next": next_step,
                 "error": "" if success else "LAUNCH_FAILED"}, args.json,
                "【启动】\n  %s" % next_step)


def cmd_home(args):
    br, err = need_bridge(args)
    if err:
        return emit(err, args.json)
    serial, state, hint = pick_device(br, args.serial)
    if state != "device":
        return emit({"ok": False, "cmd": "home", "data": {}, "next": hint, "error": state}, args.json, "【默认桌面】\n  " + hint)
    br.serial = serial
    rc, home = br.shell("cmd", "package", "resolve-activity", "--brief",
                        "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME")
    default_home = home.strip().splitlines()[-1].strip() if home.strip() else ""
    is_default = "elderhome" in default_home.lower()
    guide = (
        "把它设为默认桌面（只需老人/家人做一次，之后一按主页键就回到大字桌面）：\n"
        "  1) 按一下手机底部的「主页 / HOME」键（或上滑回桌面的手势）；\n"
        "  2) 屏幕下方会弹出「选择要使用的应用 / 完成操作方式」；\n"
        "  3) 选「长辈桌面」；\n"
        "  4) 点「始终 / 始终使用」（不要点「仅此一次」）；\n"
        "  5) 之后无论打开什么软件，一按主页键都会回到这个大字桌面。\n"
        "  想换回原桌面：设置 → 应用 → 默认应用 → 主屏幕应用 → 选回原来的桌面即可。"
    )
    data = {"default_home": default_home, "is_default_launcher": is_default}
    next_step = "已经是默认桌面了，不用再设。" if is_default else guide
    return emit({"ok": True, "cmd": "home", "data": data, "next": next_step, "error": ""}, args.json,
                "【默认桌面】\n  当前默认：%s\n%s" % (default_home or "读不到（按 HOME 键时可设置）", next_step))


def _ui_texts(br):
    """dump 主屏 UI，返回可见文字列表。只读，读完删除设备端临时文件。"""
    tmp = "/sdcard/elder_verify.xml"
    br.shell("uiautomator", "dump", tmp)
    rc, xml = br.shell("cat", tmp)
    br.shell("rm", "-f", tmp)  # 自洁，避免残留
    if rc != 0 or not xml:
        return [], xml
    texts = re.findall(r'text="([^"]*)"', xml)
    return [t for t in texts if t.strip()], xml


def _screen_state(br):
    """返回 (唤醒状态, 是否停在锁屏)。只读。"""
    rc, power = br.shell("dumpsys", "power")
    m = re.search(r"mWakefulness=(\w+)", power or "")
    wake = m.group(1) if m else "unknown"
    rc, wpol = br.shell("dumpsys", "window", "policy")
    keyguard = "isStatusBarKeyguard=true" in (wpol or "")
    return wake, keyguard


def cmd_verify(args):
    br, err = need_bridge(args)
    if err:
        return emit(err, args.json)
    serial, state, hint = pick_device(br, args.serial)
    if state != "device":
        return emit({"ok": False, "cmd": "verify", "data": {}, "next": hint, "error": state}, args.json, "【验收】\n  " + hint)
    br.serial = serial
    rc, pkgs = br.shell("pm", "list", "packages", PKG)
    installed = PKG in pkgs
    if not installed:
        result = {"ok": False, "cmd": "verify", "data": {"installed": False},
                  "next": "还没装长辈桌面，先执行 install。", "error": "NOT_INSTALLED"}
        return emit(result, args.json, "【验收】\n  " + result["next"])

    # 前置判断：屏幕熄灭或停在锁屏时，dump 到的是锁屏界面，不能据此判失败。
    wake, keyguard = _screen_state(br)
    if wake.lower() in ("asleep", "dozing"):
        guide = ("手机屏幕现在是熄灭的。请先按一下电源键点亮屏幕并解锁，"
                 "回到大字桌面后跟我说一声「好了」，我再验收一次。")
        return emit({"ok": False, "cmd": "verify", "data": {"screen": "off", "installed": True},
                     "next": guide, "error": "SCREEN_OFF"}, args.json, "【验收】\n  " + guide)
    if keyguard:
        guide = ("手机现在停在锁屏界面，我暂时看不到大字桌面。请先解锁手机、按一下主页键"
                 "回到大字桌面，然后跟我说一声「好了」，我再验收一次。")
        return emit({"ok": False, "cmd": "verify", "data": {"screen": "locked", "installed": True},
                     "next": guide, "error": "SCREEN_LOCKED"}, args.json, "【验收】\n  " + guide)

    texts, xml = _ui_texts(br)
    joined = " ".join(texts)
    rc, acc = br.shell("settings", "get", "secure", "enabled_accessibility_services")
    acc = acc.strip()
    sdk = _sdk_int(br.getprop("ro.build.version.sdk"))
    on_home = sum(1 for k in ("打电话", "电话簿", "更多") if k in joined) >= 2
    weather_on = "°" in joined
    battery_visible = ("电量" in joined or "充电" in joined or "%" in joined)
    clean_supported = sdk < 34

    if not on_home:
        next_step = ("长辈桌面已经装好、屏幕也亮着，但现在没在大字桌面主屏上"
                     "（可能停在别的应用里）。请按一下手机的主页键回到大字桌面，"
                     "再跟我说一声「好了」，我重新验收。")
        state_key = "NOT_ON_HOME"
    else:
        state_key = ""
        tips = []
        if not weather_on:
            tips.append("主屏没显示天气——默认是关着的，可长按主屏时间进设置打开天气并填城市")
        if not clean_supported:
            tips.append("这台手机系统较新（安卓 14+），一键清理受系统限制，页面会如实提示")
        next_step = "主屏大字、三大按钮都在，验收通过。" + \
                    ("另：" + "；".join(tips) + "。" if tips else " 接着按家配置并教老人使用。")
    checks = {
        "installed": installed,
        "on_home_screen": on_home,
        "battery_visible": battery_visible,
        "weather_visible": weather_on,
        "accessibility_not_required": "elderhome" not in acc.lower(),
        "clean_supported": clean_supported,
    }
    passed = installed and on_home and not state_key
    data = {"serial": serial, "sdk": sdk, "wakefulness": wake, "keyguard": keyguard,
            "ui_texts": texts, "checks": checks, "accessibility_services": acc}
    return emit({"ok": passed, "cmd": "verify", "data": data, "next": next_step,
                 "error": state_key or ("" if passed else "VERIFY_INCOMPLETE")},
                args.json, _human_verify({"data": data, "next": next_step}))


def _human_verify(r):
    d = r["data"]
    hard_label = {"installed": "长辈桌面已安装", "on_home_screen": "当前在大字桌面主屏"}
    info_label = {"battery_visible": "主屏电量显示", "weather_visible": "主屏天气显示",
                  "accessibility_not_required": "清理无需无障碍开关", "clean_supported": "系统支持一键清理"}
    lines = ["【验收自检】  安卓 SDK %s  屏幕=%s  锁屏=%s" %
             (d["sdk"], d.get("wakefulness", "?"), "是" if d.get("keyguard") else "否")]
    lines.append("  关键项：")
    for k in ("installed", "on_home_screen"):
        lines.append("    [%s] %s" % ("✔" if d["checks"].get(k) else "✘", hard_label[k]))
    lines.append("  提示项（不影响能否使用）：")
    for k in ("battery_visible", "weather_visible", "accessibility_not_required", "clean_supported"):
        if k in d["checks"]:
            lines.append("    [%s] %s" % ("✔" if d["checks"][k] else "—", info_label[k]))
    if d.get("ui_texts"):
        lines.append("  主屏读到的文字：%s" % " / ".join(d["ui_texts"][:12]))
    lines.append("  结论：%s" % r["next"])
    return "\n".join(lines)


def cmd_rollback(args):
    if not args.confirm:
        result = {"ok": False, "cmd": "rollback", "data": {},
                  "next": "这是卸载操作。执行前必须先跟用户确认「确定要卸载长辈桌面吗」，确认后加 --confirm 再运行。"
                          "（卸载只移除本桌面 App，不删老人手机里的照片、通讯录和其他软件；系统会自动回到原厂桌面。）",
                  "error": "NEED_CONFIRM"}
        return emit(result, args.json, "【回退卸载】\n  " + result["next"])
    br, err = need_bridge(args)
    if err:
        return emit(err, args.json)
    serial, state, hint = pick_device(br, args.serial)
    if state != "device":
        return emit({"ok": False, "cmd": "rollback", "data": {}, "next": hint, "error": state},
                    args.json, "【回退卸载】\n  " + hint)
    br.serial = serial
    rc, out = br.shell("pm", "uninstall", PKG)
    success = "Success" in out
    next_step = ("已卸载长辈桌面，手机会自动回到原来的桌面。若没自动回，按一下 HOME 键选回原桌面并点「始终」。"
                 if success else "卸载没成功：%s" % out[-300:])
    return emit({"ok": success, "cmd": "rollback", "data": {"serial": serial, "raw": out[-300:]},
                 "next": next_step, "error": "" if success else "UNINSTALL_FAILED"}, args.json,
                "【回退卸载】\n  %s" % next_step)


def build_parser():
    p = argparse.ArgumentParser(description="长辈桌面 · 老人手机适老化改造助手")
    p.add_argument("--adb", help="指定 adb 路径")
    p.add_argument("--serial", help="指定设备序列号")
    p.add_argument("--json", action="store_true", help="输出纯 JSON")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("check", help="连机体检（只读）")
    sub.add_parser("info", help="设备信息与安装/默认桌面状态（只读）")
    ins = sub.add_parser("install", help="安装长辈桌面")
    ins.add_argument("--apk", help="APK 路径，默认用技能自带成品")
    sub.add_parser("launch", help="冷启动主屏")
    sub.add_parser("home", help="默认桌面状态与设置引导")
    sub.add_parser("verify", help="验收自检（只读）")
    rb = sub.add_parser("rollback", help="卸载并回退到原桌面（危险）")
    rb.add_argument("--confirm", action="store_true", help="确认卸载，必须显式带上")
    return p


def main():
    _enc_fix()
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    handlers = {
        "check": cmd_check, "info": cmd_info, "install": cmd_install,
        "launch": cmd_launch, "home": cmd_home, "verify": cmd_verify,
        "rollback": cmd_rollback,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
