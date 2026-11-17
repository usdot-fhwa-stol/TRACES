"""Build the Windows icon, traces.ico, from traces_icon.svg.

Run by the build workflow before PyInstaller; the .ico becomes the icon of
the application's .exe (PyInstaller --icon) and of the installer and
uninstaller (NSIS MUI_ICON / MUI_UNICON). The running application doesn't
use it -- its window and taskbar icon are drawn from the SVG by
ui_kit.brand.app_icon.

    python src/assets/make_icons.py [output.ico]

Written to traces.ico next to this script unless an output path is given.

The icon is drawn with PyQt6's SVG renderer (already a dependency), so the
build needs nothing extra such as Pillow or Inkscape. Every Windows icon
size is drawn directly from the vector art rather than scaled from one
large bitmap, which keeps the small sizes sharp. Sizes below 256 px are
stored as 32-bit bitmaps and 256 px as PNG -- the standard layout, which
every Windows version, Explorer and NSIS read.
"""

import os
import struct
import sys

# Draw without a display (build machines may have none).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "traces_icon.svg")
TARGET = os.path.join(HERE, "traces.ico")

# Windows shell sizes at 100-200% display scaling, plus 128 for large views.
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
PNG_FROM = 256   # sizes at or above this are stored as PNG


# ── ICO format (pure Python, no Qt) ─────────────────────────────────────────

def dib_image(size: int, bgra: bytes) -> bytes:
    """An icon image stored as a 32-bit bitmap: BITMAPINFOHEADER, the
    pixels bottom row first, and an all-zero AND mask (transparency comes
    from the alpha channel). *bgra* is the image top row first, 4 bytes
    per pixel in B, G, R, A order with straight (not premultiplied) alpha."""
    row = size * 4
    if len(bgra) != row * size:
        raise ValueError(f"{size} px image needs {row * size} bytes, got {len(bgra)}")
    pixels = b"".join(bgra[y * row:(y + 1) * row] for y in reversed(range(size)))
    mask_row = ((size + 31) // 32) * 4          # 1 bit per pixel, 32-bit aligned
    mask = bytes(mask_row * size)
    header = struct.pack("<IiiHHIIiiII",
                         40,                    # header size
                         size, size * 2,        # height counts image + mask
                         1, 32,                 # planes, bits per pixel
                         0,                     # BI_RGB, uncompressed
                         len(pixels) + len(mask),
                         0, 0, 0, 0)
    return header + pixels + mask


def ico_file(images) -> bytes:
    """A multi-size .ico from (size, image data) pairs, image data being
    dib_image() output or PNG file bytes."""
    images = sorted(images)
    offset = 6 + 16 * len(images)
    directory, data = [], []
    for size, blob in images:
        dim = 0 if size >= 256 else size        # 0 means 256 in the directory
        directory.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset))
        data.append(blob)
        offset += len(blob)
    return struct.pack("<HHH", 0, 1, len(images)) + b"".join(directory) + b"".join(data)


# ── Drawing (PyQt6) ─────────────────────────────────────────────────────────

def _render(renderer, size: int):
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QImage, QPainter

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    # Straight alpha for the bitmap entries. Format_ARGB32 is stored as
    # B, G, R, A bytes on little-endian machines (every Windows PC).
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _bgra(image) -> bytes:
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    data = bytes(ptr)
    row, stride = image.width() * 4, image.bytesPerLine()
    if stride == row:
        return data
    return b"".join(data[y * stride:y * stride + row] for y in range(image.height()))


def _png(image) -> bytes:
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice

    buffer_bytes = QByteArray()
    buffer = QBuffer(buffer_bytes)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Could not encode PNG")
    buffer.close()
    return bytes(buffer_bytes)


def main(argv) -> int:
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtSvg import QSvgRenderer

    target = argv[1] if len(argv) > 1 else TARGET
    app = QGuiApplication(argv[:1])   # noqa: F841 -- Qt drawing needs one
    renderer = QSvgRenderer(SOURCE)
    if not renderer.isValid():
        print(f"Cannot read {SOURCE}", file=sys.stderr)
        return 1
    if abs(renderer.viewBoxF().width() - renderer.viewBoxF().height()) > 0.01:
        print(f"Warning: {os.path.basename(SOURCE)} is not square; the icon will be stretched",
              file=sys.stderr)

    images = []
    for size in SIZES:
        image = _render(renderer, size)
        blob = _png(image) if size >= PNG_FROM else dib_image(size, _bgra(image))
        images.append((size, blob))

    data = ico_file(images)
    with open(target, "wb") as f:
        f.write(data)
    print(f"Wrote {target}: {len(data):,} bytes, sizes {', '.join(map(str, SIZES))} px")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
