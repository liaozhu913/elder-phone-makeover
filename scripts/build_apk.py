#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可选：离线重编译长辈桌面 APK。

绝大多数情况【不需要用它】——技能自带成品 APK（assets/elder-launcher.apk），
直接 install 即可。只有需要改源码（换默认城市、预置联系人、改配色字号等）时，
在有源码 + 本地工具链的电脑上用本脚本离线编译，产出仍写回 assets/。

用法：
  python build_apk.py                      # 自动探测源码与工具链
  python build_apk.py --source <源码根目录> --jdk <JDK目录> --gradle <gradle可执行文件>
  python build_apk.py --json               # 输出 JSON 供 AI 解析

源码根目录的特征：含 settings.gradle 与 app/build.gradle。
工具链探测：环境变量 ELDER_SOURCE（源码）/ JAVA_HOME（JDK）/ ELDER_GRADLE（Gradle），
或 --source/--jdk/--gradle 参数，或 PATH 中可直接调用的 gradle。

注意：绝大多数情况不需要编译——直接用技能自带的成品 APK（assets/elder-launcher.apk）安装。
本脚本只在需要改源码重新打包时使用。

安全：只做离线 assembleDebug，不联网下载依赖，不上传任何东西。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SELF_DIR = Path(__file__).resolve().parent
SKILL_DIR = SELF_DIR.parent
BUNDLED_APK = SKILL_DIR / "assets" / "elder-launcher.apk"

# 本机路径不硬编码进仓库（避免泄露个人目录结构）。若要把源码/JDK/Gradle 放在固定位置，
# 用 --source/--jdk/--gradle 参数，或设环境变量 ELDER_SOURCE / JAVA_HOME / ELDER_GRADLE。
# 下方常量只读环境变量，默认空 = 完全依赖参数与自动探测。
KNOWN_SOURCE = os.environ.get("ELDER_SOURCE", "")
KNOWN_JDK = ""
KNOWN_GRADLE = ""


def _enc_fix():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _is_source(p):
    return p and os.path.isfile(os.path.join(p, "settings.gradle")) and \
        os.path.isfile(os.path.join(p, "app", "build.gradle"))


def _find_source(explicit):
    if explicit:
        return explicit if _is_source(explicit) else None
    env = os.environ.get("ELDER_SOURCE")
    if env and _is_source(env):
        return env
    if _is_source(KNOWN_SOURCE):
        return KNOWN_SOURCE
    # 常见位置兜底
    for base in (str(Path.home()),):
        for cand in (os.path.join(base, "elder-launcher-src"),):
            if _is_source(cand):
                return cand
    return None


def _find_jdk(explicit):
    if explicit:
        return explicit
    env = os.environ.get("JAVA_HOME")
    if env and os.path.isfile(os.path.join(env, "bin", "java.exe" if os.name == "nt" else "java")):
        return env
    if os.path.isdir(KNOWN_JDK):
        return KNOWN_JDK
    return None


def _find_gradle(explicit):
    # Python 的 subprocess 在 Windows 上不能直接执行无扩展名的 bash 脚本（WinError 193），
    # 因此本机优先取 gradle.bat。
    if explicit:
        return explicit
    env = os.environ.get("ELDER_GRADLE")
    if env and os.path.isfile(env):
        return env
    if os.name == "nt":
        for c in (KNOWN_GRADLE + ".bat", KNOWN_GRADLE):
            if os.path.isfile(c) and c.endswith(".bat"):
                return c
        which_bat = shutil.which("gradle.bat")
        if which_bat:
            return which_bat
    which = shutil.which("gradle")
    if which:
        return which
    return None


def emit(result, as_json, human=None):
    if as_json or human is None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(human)
    return 0 if result.get("ok") else 1


def main():
    _enc_fix()
    p = argparse.ArgumentParser(description="离线重编译长辈桌面 APK（可选）")
    p.add_argument("--source", help="安卓源码根目录")
    p.add_argument("--jdk", help="JDK 目录（JAVA_HOME）")
    p.add_argument("--gradle", help="gradle 可执行文件")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    source = _find_source(args.source)
    if not source:
        r = {"ok": False, "cmd": "build", "data": {}, "error": "NO_SOURCE",
             "next": "没找到长辈桌面源码，无法重编译。绝大多数情况不需要编译——直接用技能自带的成品 APK 安装即可。"
                     "若确实要改源码，请把源码目录通过 --source 传入（该目录应含 settings.gradle 与 app/build.gradle）。"}
        return emit(r, args.json, "【重编译】\n  " + r["next"])
    jdk = _find_jdk(args.jdk)
    gradle = _find_gradle(args.gradle)
    if not jdk or not gradle:
        miss = []
        if not jdk:
            miss.append("JDK 17（用 --jdk 或 JAVA_HOME 指定）")
        if not gradle:
            miss.append("Gradle 8.9（用 --gradle 指定）")
        r = {"ok": False, "cmd": "build", "data": {"source": source}, "error": "NO_TOOLCHAIN",
             "next": "找到源码但缺构建工具：" + "、".join(miss) +
                     "。可改用技能自带成品 APK 直接安装，无需编译。"}
        return emit(r, args.json, "【重编译】\n  " + r["next"])

    env = os.environ.copy()
    env["JAVA_HOME"] = jdk
    env["PATH"] = os.path.join(jdk, "bin") + os.pathsep + env.get("PATH", "")
    # 禁用 MSYS 路径转换
    env["MSYS_NO_PATHCONV"] = "1"
    env["MSYS2_ARG_CONV_EXCL"] = "*"
    cmd = [gradle, "-p", source, "assembleDebug", "--offline",
           "-Dorg.gradle.java.home=" + jdk]
    # Windows 下 .bat 需经 shell 执行；非 Windows 用列表参数直接跑。
    use_shell = os.name == "nt" and str(gradle).lower().endswith(".bat")
    try:
        if use_shell:
            # 拼成命令行字符串，路径用引号包裹以兼容中文/空格路径
            cmdline = ' '.join('"%s"' % str(a).replace('"', '\\"') if (' ' in str(a) or '\\' in str(a)) else str(a)
                               for a in cmd)
            proc = subprocess.run(cmdline, shell=True, capture_output=True, timeout=600, env=env)
        else:
            proc = subprocess.run(cmd, capture_output=True, timeout=600, env=env)
        out = ""
        for raw in (proc.stdout, proc.stderr):
            if raw:
                out += raw.decode("utf-8", errors="replace")
        rc = proc.returncode
    except (FileNotFoundError, OSError) as exc:
        r = {"ok": False, "cmd": "build", "data": {"source": source}, "error": "GRADLE_NOT_RUNNABLE",
             "next": "gradle 无法执行（%s）：%s。可改用技能自带成品 APK 直接安装。" % (gradle, exc)}
        return emit(r, args.json, "【重编译】\n  " + r["next"])
    except subprocess.TimeoutExpired:
        r = {"ok": False, "cmd": "build", "data": {"source": source}, "error": "TIMEOUT",
             "next": "编译超时（10 分钟）。可改用技能自带成品 APK 直接安装。"}
        return emit(r, args.json, "【重编译】\n  " + r["next"])

    apk = os.path.join(source, "app", "build", "outputs", "apk", "debug", "app-debug.apk")
    if rc == 0 and os.path.isfile(apk):
        try:
            shutil.copyfile(apk, BUNDLED_APK)
            wrote = str(BUNDLED_APK)
        except Exception:
            wrote = apk + "（复制回 assets 失败，直接用此产物安装）"
        r = {"ok": True, "cmd": "build", "data": {"apk": wrote, "size": os.path.getsize(apk)},
             "next": "编译成功，产物 %s。接下来执行 install 安装到老人手机。" % wrote, "error": ""}
        return emit(r, args.json, "【重编译】\n  BUILD SUCCESSFUL\n  " + r["next"])
    tail = out.strip()[-600:]
    r = {"ok": False, "cmd": "build", "data": {"source": source, "log_tail": tail}, "error": "BUILD_FAILED",
         "next": "编译失败（exit %s）。可改用技能自带成品 APK 直接安装。\n编译日志末尾：\n%s" % (rc, tail)}
    return emit(r, args.json, "【重编译】\n  " + r["next"])


if __name__ == "__main__":
    sys.exit(main())
