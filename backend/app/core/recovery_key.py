"""A recovery key (ADR 0001): 160 random bits a person keeps, which open her data keys
where Windows no longer can: another Windows computer, Windows reinstalled, a reset
account.

Written as 36 characters in nine groups of four, in Crockford's base 32: digits and
letters without I, L, O or U, so no character is mistaken for another, and read back in
either case with I and L taken as 1 and O as 0. The first 32 carry the key and the last
4 a CRC-20 of it, which catches, for any key, a character typed wrong or two
neighbours swapped before the key is tried: either changes at most 10 bits in a row,
and a CRC finds every such run shorter than its own 20 (a hash of the same size missed
a changed character in about 1 key of 1,000). The key that seals each data key, and
the id that says which recovery key sealed a key file, are derived from it with
HKDF-SHA256 (`data_key.seal_recovery`). The recovery key itself is kept nowhere.
"""

from __future__ import annotations

import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SECRET_BYTES = 20
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_READ = {**{symbol: value for value, symbol in enumerate(_ALPHABET)}, "I": 1, "L": 1, "O": 0}
_KEY_SYMBOLS = SECRET_BYTES * 8 // 5
_CHECK_SYMBOLS = 4
_GROUP = 4
_CHECK_BITS = 20
_POLYNOMIAL = 1 << 20 | 1 << 3 | 1  # x^20 + x^3 + 1
# Never change either: every recovery key made would stop opening what it sealed, or
# stop being known as the one that did.
_SEALING_INFO = b"veronica-recovery-key-1"
_ID_INFO = b"veronica-recovery-id-1"


class NotARecoveryKey(ValueError):
    """Text that isn't a recovery key: the wrong length, a character none has, or a check
    that doesn't add up, as when a character was typed wrong."""


def new() -> bytes:
    """A new recovery key's 160 bits."""
    return secrets.token_bytes(SECRET_BYTES)


def written(secret: bytes) -> str:
    """`secret` as a person keeps it: nine groups of four, joined by hyphens."""
    number = int.from_bytes(secret, "big")
    symbols = _symbols(number, _KEY_SYMBOLS) + _symbols(_crc(number), _CHECK_SYMBOLS)
    return "-".join(symbols[at : at + _GROUP] for at in range(0, len(symbols), _GROUP))


def read(text: str) -> bytes:
    """The recovery key written in `text`, however it is spaced, split or cased;
    `NotARecoveryKey` for anything else."""
    compact = "".join(character for character in text.upper() if character.isalnum())
    if len(compact) != _KEY_SYMBOLS + _CHECK_SYMBOLS:
        raise NotARecoveryKey("not the length of a recovery key")
    try:
        values = [_READ[character] for character in compact]
    except KeyError:
        raise NotARecoveryKey("a character no recovery key has") from None
    number = _number(values[:_KEY_SYMBOLS])
    if _number(values[_KEY_SYMBOLS:]) != _crc(number):
        raise NotARecoveryKey("its check doesn't add up")
    return number.to_bytes(SECRET_BYTES, "big")


def sealing_key(secret: bytes) -> bytes:
    """The key the data keys are sealed with, derived from the recovery key's bits."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_SEALING_INFO).derive(secret)


def key_id(secret: bytes) -> str:
    """Which recovery key sealed a key file: the same for each file it sealed, and
    nothing about the key itself."""
    return HKDF(algorithm=hashes.SHA256(), length=8, salt=None, info=_ID_INFO).derive(secret).hex()


def _symbols(number: int, count: int) -> str:
    return "".join(_ALPHABET[number >> 5 * (count - 1 - at) & 31] for at in range(count))


def _number(values: list[int]) -> int:
    number = 0
    for value in values:
        number = number << 5 | value
    return number


def _crc(number: int) -> int:
    """The remainder of the key's 160 bits, followed by 20 zeros, divided by the
    polynomial over GF(2): a CRC-20."""
    remainder = number << _CHECK_BITS
    for bit in range(SECRET_BYTES * 8 + _CHECK_BITS - 1, _CHECK_BITS - 1, -1):
        if remainder >> bit & 1:
            remainder ^= _POLYNOMIAL << (bit - _CHECK_BITS)
    return remainder
