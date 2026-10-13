# -*- coding: utf-8 -*-
"""后端看门狗：uvicorn 进程退出（崩溃 / 被杀 / 改码重启）后 2 秒自动拉起。
用法：python watchdog.py   （放后台常驻）
以后更新后端代码：直接杀掉 uvicorn 子进程即可，看门狗会用新代码自动重启，无需手动操作。
"""
import os
import subprocess
import sys
import time

PY = sys.executable
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    os.chdir(APP_DIR)
    n = 0
    while True:
        n += 1
        print('[watchdog] #%d 启动 uvicorn backend.main:app --port 8000 ...' % n, flush=True)
        t0 = time.time()
        try:
            p = subprocess.run([PY, '-m', 'uvicorn', 'backend.main:app',
                                '--host', '0.0.0.0', '--port', '8000'])
            code = p.returncode
        except KeyboardInterrupt:
            print('[watchdog] 手动中断，退出', flush=True)
            return
        except Exception as e:  # noqa: BLE001
            code = -1
            print('[watchdog] 启动异常：', e, flush=True)
        dt = time.time() - t0
        print('[watchdog] uvicorn 退出（code=%s，存活 %.0f 秒），2 秒后自动重启' % (code, dt), flush=True)
        # 若刚启动就崩，多等一会避免死循环刷屏
        time.sleep(2 if dt > 10 else 5)


if __name__ == '__main__':
    main()
