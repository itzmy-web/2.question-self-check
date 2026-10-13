#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
问题自检 · 生产启动入口（云沙箱 / uvicorn 部署用）

沙箱通过 PORT 环境变量指定唯一对外端口，并且要求监听 0.0.0.0。
这里显式读取 PORT，避免依赖 `--port $PORT` 这种 shell 变量展开
（沙箱里不会展开，会导致 uvicorn 报 "Option '--port' requires an argument"）。

用法：
    python serve.py                  # 读 PORT，默认 3000
本地开发（自动挑空闲端口 + 自动开浏览器）仍请用 python start.py
"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT") or 3000)
    print(f"[serve] listening on 0.0.0.0:{port}", flush=True)
    # 用「导入字符串」启动，配合 `python serve.py` 时 sys.path[0] = 项目根，
    # 因此 backend.main 能被正确导入（不依赖当前工作目录）。
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)
