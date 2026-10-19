"""Coordinate rulers for model close-ups. Saved observations stay unmodified."""

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont


@lru_cache(maxsize=1)
def guide_font():
    return ImageFont.load_default(size=18)


def coordinate_guide(png: bytes, *, units: str = 'image_pixels') -> bytes:
    if units not in {'image_pixels', 'image_normalized'}:
        raise ValueError('Unsupported coordinate guide units')
    with Image.open(io.BytesIO(png)) as source:
        image = source.convert('RGBA')
    overlay = Image.new('RGBA', image.size)
    draw = ImageDraw.Draw(overlay)
    font = guide_font()
    normalized = units == 'image_normalized'
    for label in range(100, 1000 if normalized else image.width, 100):
        x = round(label / 1000 * image.width) if normalized else label
        draw.line([(x, 0), (x, image.height)], fill=(80, 130, 210, 55))
        if x + 50 < image.width:
            draw.rectangle((x + 2, 0, x + 53, 21), fill=(255, 255, 255, 235))
            draw.text((x + 3, 1), f'x{label}', font=font, fill=(30, 75, 145, 255))
    for label in range(100, 1000 if normalized else image.height, 100):
        y = round(label / 1000 * image.height) if normalized else label
        draw.line([(0, y), (image.width, y)], fill=(80, 130, 210, 55))
        if y + 22 < image.height:
            draw.rectangle((0, y + 1, 56, y + 22), fill=(255, 255, 255, 235))
            draw.text((2, y + 2), f'y{label}', font=font, fill=(30, 75, 145, 255))
    output = io.BytesIO()
    Image.alpha_composite(image, overlay).convert('RGB').save(output, format='PNG')
    return output.getvalue()
