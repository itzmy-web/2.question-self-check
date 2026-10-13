# -*- coding: utf-8 -*-
"""从用户录屏中提取暹罗猫逐帧精灵：抠掉白底 → 统一裁剪 → 量化压缩 → 存入项目 assets/cat/。"""
import os
import imageio
from PIL import Image

VID = r'C:/Users/zmy/Videos/屏幕录制/屏幕录制 2026-10-08 211643.mp4'
OUT = r'C:/Users/zmy/WorkBuddy/2026-09-28-15-29-59/self-check-quiz/assets/cat'
N_FRAMES = 16

os.makedirs(OUT, exist_ok=True)
reader = imageio.get_reader(VID)
meta = reader.get_meta_data()
n = reader.count_frames()
print('frames:', n, 'duration:', meta.get('duration'))

# 1) 均匀取帧
idxs = [round(n * i / N_FRAMES) % n for i in range(N_FRAMES)]
frames = []
for i in idxs:
    frames.append(Image.fromarray(reader.get_data(min(i, n - 1))).convert('RGBA'))

# 2) 白底转透明（高阈值去压缩噪点：只有足够"深"的像素才保留）
def unwhite(im):
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            m = min(r, g, b)
            if m > 240:
                px[x, y] = (r, g, b, 0)
            elif m > 222:
                px[x, y] = (r, g, b, int(255 * (240 - m) / 18))
    return im

frames = [unwhite(f) for f in frames]

# 3) 裁剪框：经 debug 实测猫在 x71-174 / y46-166（右侧零星噪点忽略）
pad = 6
box = (71 - pad, 46 - pad, 174 + pad, 166 + pad)
print('crop box:', box, 'size:', box[2] - box[0], 'x', box[3] - box[1])

# 4) 裁剪 + 量化压缩 + 保存
total = 0
for i, f in enumerate(frames):
    f2 = f.crop(box)
    f2 = f2.quantize(colors=64, method=Image.FASTOCTREE, dither=Image.NONE)
    p = os.path.join(OUT, 'f%02d.png' % (i + 1))
    f2.save(p, optimize=True)
    total += os.path.getsize(p)
    print('saved', os.path.basename(p), os.path.getsize(p), 'bytes', f2.size)
print('TOTAL:', total, 'bytes')
