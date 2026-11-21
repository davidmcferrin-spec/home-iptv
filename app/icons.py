import struct
import zlib


def favicon_svg() -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#121820"/>'
        '<path d="M12 8 L24 16 L12 24 Z" fill="#d7dde8"/>'
        "</svg>"
    ).encode()


def _png(size: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def apple_touch_png() -> bytes:
    return _png(180, (18, 24, 32))


def favicon_ico() -> bytes:
    size = 16
    pixels = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            play = x >= 5 and abs(y - 8) <= (x - 4) and x <= 12
            if play:
                pixels += b"\xe8\xdd\xd7\xff"
            else:
                pixels += b"\x20\x18\x12\xff"
    xor = bytes(pixels)
    header = struct.pack("<IIIHHIIIIII", 40, size, size * 2, 1, 32, 0, len(xor), 0, 0, 0, 0)
    image = header + xor
    return struct.pack("<HHHBBBBHHII", 0, 1, 1, size, size, 0, 0, 1, 32, len(image), 22) + image
