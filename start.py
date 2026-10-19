#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
巡检台项目 · 本地全栈启动器
用法：
    1) 终端进入本文件夹（巡检台项目）
    2) 首次运行先装依赖：  pip install -r requirements.txt
    3) 运行：  python start.py
    4) 浏览器自动打开登录页；未自动打开就访问终端提示的地址
按 Ctrl+C 停止服务。

说明：本启动器直接拉起 FastAPI 后端（backend/main.py），
后端同时托管 login.html / index.html 并提供 /api/auth/* 接口。
"""
import os
import sys
import json
import socket
import urllib.request
import webbrowser
import threading


ROOT = os.path.dirname(os.path.abspath(__file__))


def _instance_ours(port, timeout=0.8):
    """该端口上是否已经有「我们自己的实例」在应答（用于避免重复启动）。"""
    try:
        r = urllib.request.urlopen(
            "http://127.0.0.1:%d/api/llm-config" % port, timeout=timeout)
        d = json.loads(r.read().decode("utf-8", "ignore"))
        return isinstance(d, dict) and ("text_model" in d)
    except Exception:
        return False


def port_usable(port):
    """端口是否真的能被浏览器（走 127.0.0.1）访问，且我们还能绑住 0.0.0.0。

    关键坑：在 Windows 上 127.0.0.1:PORT 和 0.0.0.0:PORT 可以由不同进程同时占用
    （例如 Docker Desktop 会把容器端口发布在 127.0.0.1 上）。
    只做 bind("0.0.0.0") 检测会把这种端口误判成“空闲”，于是我们绑上 0.0.0.0，
    浏览器却打到 127.0.0.1 上的另一个服务 —— 表现为「无法连接后端」。
    所以必须先做一次 connect 探测：只要 127.0.0.1 上有人应答，这个端口就不能用。
    """
    # 1) 127.0.0.1:port 上已经有人应答 → 浏览器会打到它，不能用
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.35)
    busy = False
    try:
        s.connect(("127.0.0.1", port))
        busy = True
    except Exception:
        busy = False
    finally:
        try:
            s.close()
        except Exception:
            pass
    if busy:
        return False

    # 2) 我们能否绑住 0.0.0.0:port
    b = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        b.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        try:
            b.close()
        except Exception:
            pass


def find_free_port(start=8000, end=8110):
    port = start
    while port < end:
        if port_usable(port):
            return port
        port += 1
    raise RuntimeError("在 %d-%d 范围内找不到可用端口" % (start, end))


def _reuse_existing():
    """若已有我们的实例在跑，返回它的端口（避免重复启动导致端口漂移/多实例打架）。"""
    for p in range(8000, 8010):
        if _instance_ours(p):
            return p
    return None


def main():
    os.chdir(ROOT)

    # 依赖检查
    try:
        import fastapi  # noqa
        import uvicorn  # noqa
    except ImportError:
        print("⚠️  缺少依赖 fastapi / uvicorn。")
        print("请先安装：  pip install -r requirements.txt")
        print("（若是虚拟环境，请先激活再运行本脚本）")
        try:
            input("按回车退出…")
        except EOFError:
            pass
        sys.exit(1)

    if not os.path.exists(os.path.join(ROOT, "backend", "main.py")):
        print("⚠️  未找到 backend/main.py，请确认目录完整。")
        sys.exit(1)

    # 0) 已经有一个我们的实例在跑？直接复用它，别再起一个
    exist = _reuse_existing()
    if exist:
        url = "http://127.0.0.1:%d/login.html" % exist
        print("=" * 50)
        print("  检测到「巡检台」已在运行，直接复用：")
        print("  访问地址：", url)
        print("  （要用最新代码，请先在原终端按 Ctrl+C 停止旧实例，再重新运行本脚本）")
        print("=" * 50)
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        return

    port = find_free_port(8000)
    url = "http://127.0.0.1:%d/login.html" % port

    print("=" * 50)
    print("  巡检台 · 全栈服务启动中…")
    print("  访问地址：", url)
    print("  停止服务：Ctrl+C")
    print("=" * 50)

    # 1.5 秒后自动打开浏览器
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    try:
        import uvicorn
        # 通过导入字符串启动，等价于：uvicorn backend.main:app
        uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)
    except KeyboardInterrupt:
        print("\n已停止服务，再见 👋")


if __name__ == "__main__":
    main()
