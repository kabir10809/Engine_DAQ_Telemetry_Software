"""Decoding for text frames, Modbus registers and CAN payloads. Pure functions,
so every format is testable with no hardware attached."""
from __future__ import annotations

import json
import math
import struct

_WORDS = {"uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "single": 2, "double": 4}
_FORMATS = {"uint16": "<H", "int16": "<h", "uint32": "<I", "int32": "<i", "single": "<f", "double": "<d"}


def parse_frame(frame: str, fmt: str, delimiter: str, fields: list[str]):
    """Decode one text frame. fields are 1-based column numbers or key names.
    Returns (values, ok); ok is False when malformed or a field is absent."""
    values = [math.nan] * len(fields)
    frame = frame.strip()
    try:
        if fmt == "delimited":
            parts = frame.split(delimiter)
            for k, field in enumerate(fields):
                values[k] = float(parts[int(field) - 1])
        elif fmt == "keyvalue":
            pairs = dict(item.split("=", 1) for item in frame.split(delimiter))
            pairs = {key.strip(): text for key, text in pairs.items()}
            for k, field in enumerate(fields):
                if field in pairs:
                    values[k] = float(pairs[field])
        elif fmt == "json":      # a protocol some instruments speak, not a storage format
            document = json.loads(frame)
            for k, field in enumerate(fields):
                if isinstance(document.get(field), (int, float)):
                    values[k] = float(document[field])
        else:
            raise LookupError(f'Unknown frame format "{fmt}".')
    except LookupError as error:
        if "Unknown frame format" in str(error):
            raise
        return [math.nan] * len(fields), False
    except (ValueError, AttributeError, TypeError):
        return [math.nan] * len(fields), False
    return values, all(math.isfinite(v) for v in values)


def word_count(kind: str) -> int:
    if kind not in _WORDS:
        raise ValueError(f'Unknown register type "{kind}".')
    return _WORDS[kind]


def decode_registers(registers, kind: str, word_order: str) -> float:
    """registers in bus order. word_order: "big" (high word first) or "little"."""
    words = list(registers)
    if word_order == "big":
        words.reverse()
    return float(struct.unpack(_FORMATS[kind], struct.pack(f"<{len(words)}H", *words))[0])


def can_bit_positions(start_bit: int, length: int, byte_order: str) -> list[int]:
    """Absolute bit indices (byte*8 + bit), LSB first, in DBC conventions."""
    if byte_order == "intel":
        return list(range(start_bit, start_bit + length))
    positions, current = [], start_bit
    for _ in range(length):      # Motorola walks from the MSB, down a byte, then to bit 7 of the next
        positions.append(current)
        current = current + 15 if current % 8 == 0 else current - 1
    return positions[::-1]


def decode_can(data, start_bit, length, byte_order, signed, scale, offset) -> float:
    positions = can_bit_positions(start_bit, length, byte_order)
    if max(positions) >= len(data) * 8:
        raise ValueError(f"Signal extends past the {len(data)}-byte payload.")
    raw = sum(((data[p // 8] >> (p % 8)) & 1) << k for k, p in enumerate(positions))
    if signed and raw >= 2 ** (length - 1):
        raw -= 2 ** length
    return raw * scale + offset
