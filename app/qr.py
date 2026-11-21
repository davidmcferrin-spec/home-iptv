import segno


def svg(payload: str) -> str:
    # segno is BSD-licensed. The picture is generated here; its source is not copied in.
    code = segno.make(payload, error="l", micro=False)
    return code.svg_inline(scale=4, dark="#111111", light="#ffffff", svgclass=None)
