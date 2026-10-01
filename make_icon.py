# -*- coding: utf-8 -*-
"""Іконка R-TubeUA: зелений квадрат Rozetka, кнопка «play» і стрілка вниз
над синьо-жовтою смужкою — «відео з YouTube, українською».

Кожен розмір рендериться з великого оригіналу окремо: інакше Windows
масштабує одну картинку сама, і на 16 px вона мажеться.
"""
from PIL import Image, ImageDraw

S = 1024
GREEN_TOP = (0, 178, 79)
GREEN_BOT = (0, 138, 60)
WHITE = (255, 255, 255, 255)
UA_BLUE = (0, 87, 183, 255)
UA_YELLOW = (255, 215, 0, 255)


def build(simple=False):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        k = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(
            int(a + (b - a) * k) for a, b in zip(GREEN_TOP, GREEN_BOT)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # «екран» з кнопкою play
    if simple:
        box = [int(S * 0.12), int(S * 0.14), int(S * 0.88), int(S * 0.66)]
    else:
        box = [int(S * 0.17), int(S * 0.17), int(S * 0.83), int(S * 0.60)]
    d.rounded_rectangle(box, radius=int(S * 0.09), fill=WHITE)
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    r = int((box[3] - box[1]) * 0.28)
    d.polygon([(cx - r * 0.8, cy - r), (cx - r * 0.8, cy + r), (cx + r * 1.05, cy)],
              fill=GREEN_BOT + (255,))

    # синьо-жовта смужка внизу
    if simple:
        top, bottom, left, right = int(S * 0.74), int(S * 0.90), int(S * 0.12), int(S * 0.88)
    else:
        top, bottom, left, right = int(S * 0.69), int(S * 0.83), int(S * 0.17), int(S * 0.83)
    mid = (top + bottom) // 2
    strip = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(strip)
    sd.rectangle([left, top, right, mid], fill=UA_BLUE)
    sd.rectangle([left, mid, right, bottom], fill=UA_YELLOW)
    smask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(smask).rounded_rectangle([left, top, right, bottom],
                                            radius=int((bottom - top) * 0.3), fill=255)
    img.paste(strip, (0, 0), smask)
    return img


def save_ico(path="assets/logo.ico"):
    big, small = build(), build(simple=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [(small if s <= 24 else big).resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(path, format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    big.resize((256, 256), Image.LANCZOS).save("assets/logo_preview.png")
    return path


if __name__ == "__main__":
    print(save_ico())
