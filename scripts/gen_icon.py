"""Draws the FlyMotion icon (app/assets): a Microduck head in motion, orange on ink, like the app.

  python3 scripts/gen_icon.py app/assets     # needs Pillow; writes icon.png, icon.icns and icon.ico
"""
import sys
from PIL import Image, ImageDraw

S = 2048  # drawn at 2x, downscaled for antialiasing
INK, PANEL, SHELL, ACCENT, EYE = (8, 8, 12), (24, 24, 32), (240, 236, 226), (255, 106, 26), (10, 10, 14)
img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
pad = int(S * 0.06)
d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=int(S * 0.2), fill=INK)
d.rounded_rectangle([pad + 24, pad + 24, S - pad - 24, S - pad - 24], radius=int(S * 0.19), outline=PANEL, width=24)
# motion streaks, behind the head
for i, (y, length) in enumerate([(0.40, 0.30), (0.52, 0.38), (0.64, 0.26)]):
    x1 = S * 0.47
    d.rounded_rectangle([x1 - S * length, S * y - 34, x1, S * y + 34], radius=34, fill=ACCENT if i != 1 else (255, 140, 70))
# head shell: the Microduck's rounded helmet, with its orange rim
cx, cy, w, h = S * 0.58, S * 0.47, S * 0.46, S * 0.40
shell = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
d.rounded_rectangle(shell, radius=int(h * 0.48), fill=SHELL)
rim = Image.new('L', (S, S), 0)
ImageDraw.Draw(rim).rounded_rectangle(shell, radius=int(h * 0.48), fill=255)
ImageDraw.Draw(rim).rectangle([0, 0, S, cy + h * 0.18], fill=0)
img.paste(Image.new('RGBA', (S, S), ACCENT), (0, 0), rim)
# eye (the camera lens) and beak
d.ellipse([cx + w * 0.05, cy - h * 0.20, cx + w * 0.27, cy + h * 0.06], fill=EYE)
d.ellipse([cx + w * 0.12, cy - h * 0.13, cx + w * 0.19, cy - h * 0.05], fill=(90, 90, 100))
d.polygon([(cx + w * 0.42, cy + h * 0.05), (cx + w * 0.68, cy + h * 0.22), (cx + w * 0.42, cy + h * 0.38)], fill=ACCENT)
# legs, mid-stride
for x0, lean in [(cx - w * 0.18, -1), (cx + w * 0.10, 1)]:
    top = cy + h / 2
    d.line([(x0, top), (x0 + lean * S * 0.05, S * 0.82)], fill=SHELL, width=70)
    d.rounded_rectangle([x0 + lean * S * 0.05 - 80, S * 0.80, x0 + lean * S * 0.05 + 110, S * 0.85], radius=30, fill=ACCENT)
icon = img.resize((1024, 1024), Image.LANCZOS)
out = sys.argv[1]
icon.save(f'{out}/icon.png')
icon.save(f'{out}/icon.icns')
icon.save(f'{out}/icon.ico', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
