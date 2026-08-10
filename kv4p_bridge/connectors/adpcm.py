"""IMA ADPCM helpers for connector-side tests."""

from __future__ import annotations

INDEX_TABLE = (
    -1, -1, -1, -1, 2, 4, 6, 8,
    -1, -1, -1, -1, 2, 4, 6, 8,
)

STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
    19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
    130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
    5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)


def encode_block(pcm: list[int]) -> bytes:
    """Encode one mono IMA ADPCM block."""
    if not pcm:
        return b""

    predictor = _clamp_i16(pcm[0])
    index = 0
    out = bytearray()
    out.extend(int(predictor).to_bytes(2, "little", signed=True))
    out.append(index)
    out.append(0)

    high_nibble = False
    packed = 0
    for sample in pcm[1:]:
        code, predictor, index = _encode_nibble(sample, predictor, index)
        if not high_nibble:
            packed = code & 0x0F
            high_nibble = True
        else:
            out.append(packed | ((code & 0x0F) << 4))
            high_nibble = False

    if high_nibble:
        out.append(packed)

    return bytes(out)


def decode_block(adpcm: bytes, samples: int) -> list[int]:
    """Decode one mono IMA ADPCM block."""
    if len(adpcm) < 4 or samples <= 0:
        return []

    predictor = int.from_bytes(adpcm[0:2], "little", signed=True)
    index = _clamp_index(adpcm[2])
    pcm = [predictor]

    for packed in adpcm[4:]:
        predictor, index = _decode_nibble(packed & 0x0F, predictor, index)
        pcm.append(predictor)
        if len(pcm) >= samples:
            break
        predictor, index = _decode_nibble((packed >> 4) & 0x0F, predictor, index)
        pcm.append(predictor)
        if len(pcm) >= samples:
            break

    return pcm


def _encode_nibble(sample: int, predictor: int, index: int) -> tuple[int, int, int]:
    step = STEP_TABLE[index]
    diff = sample - predictor
    code = 0
    if diff < 0:
        code = 8
        diff = -diff

    delta = step >> 3
    if diff >= step:
        code |= 4
        diff -= step
        delta += step
    if diff >= (step >> 1):
        code |= 2
        diff -= step >> 1
        delta += step >> 1
    if diff >= (step >> 2):
        code |= 1
        delta += step >> 2

    if code & 8:
        predictor -= delta
    else:
        predictor += delta

    predictor = _clamp_i16(predictor)
    index = _clamp_index(index + INDEX_TABLE[code])
    return code, predictor, index


def _decode_nibble(code: int, predictor: int, index: int) -> tuple[int, int]:
    step = STEP_TABLE[index]
    delta = step >> 3
    if code & 4:
        delta += step
    if code & 2:
        delta += step >> 1
    if code & 1:
        delta += step >> 2

    if code & 8:
        predictor -= delta
    else:
        predictor += delta

    predictor = _clamp_i16(predictor)
    index = _clamp_index(index + INDEX_TABLE[code & 0x0F])
    return predictor, index


def _clamp_i16(value: int) -> int:
    return max(-32768, min(32767, value))


def _clamp_index(value: int) -> int:
    return max(0, min(88, value))
