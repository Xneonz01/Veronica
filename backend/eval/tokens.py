"""Texts to hold the token estimate (`adapters/llm/tokens.py`) against the model's own
count: what people paste into a chat, and what a count of words would misread.

Everything here is generated or written for the purpose, nobody's own text, and each
text is seeded on its own so it comes out the same every time. `MEASURED` is what the
server counted for each (`scripts/token_probe.py`); `tests/test_tokens.py` requires the
estimate to be at least that. Another model needs its own measurement.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import uuid

#: Counted by qwen3.5:9b through Ollama 0.33.3 on 2026-09-26.
MEASURED = {
    "prose": 854,
    "code": 966,
    "json": 2410,
    "log lines": 1891,
    "markdown table": 1678,
    "uuids": 1962,
    "hex": 2639,
    "base64": 2185,
    "camel case": 508,
    "snake case": 600,
    "french": 355,
    "german": 350,
    "polish": 470,
    "vietnamese": 392,
    "russian": 416,
    "greek": 488,
    "arabic": 447,
    "hebrew": 676,
    "hindi": 614,
    "thai": 388,
    "japanese": 891,
    "korean": 665,
    "chinese": 1213,
    "emoji": 880,
    "spaces": 11,
    "newlines": 34,
    "space pairs": 1199,
    "digits": 2000,
    "phone numbers": 1500,
    "dna": 10000,
    "random letters": 2120,
    "random cases": 1940,
    "random words": 1729,
    "underscores": 1600,
    "letters and digits": 2262,
    "punctuation": 1080,
    "combining marks": 1000,
    "beyond the plane": 1200,
    "full width": 1002,
}

_PROSE = (
    "The meeting moved to Thursday afternoon, so the draft needs to be ready a day "
    "earlier than we planned. Could you look over the second section again? The numbers "
    "in the table don't match the summary, and I'd rather fix that before anyone reads "
    "it. After that, send it to the whole team with a short note about what changed. "
)
_CODE = '''def load_settings(path: str) -> dict[str, int]:
    """Read `key = value` lines, skipping comments and blank lines."""
    settings: dict[str, int] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            settings[key.strip()] = int(value)
    return settings

'''
_LANGUAGES = {
    "french": "Le train part à huit heures, mais nous arrivons toujours en retard parce que "
    "la gare est loin de la maison. Après le déjeuner, on a préféré marcher le long de la "
    "rivière jusqu'au marché, où l'été dernier les pêches étaient déjà très chères. ",
    "german": "Über den Fluss führt eine schmale Brücke, die im Frühling häufig überflutet "
    "wird. Die Straßenbahn fährt zwölf Minuten später als üblich. ",
    "polish": "Wczoraj wieczorem poszliśmy na długi spacer nad jeziorem, a potem zjedliśmy "
    "kolację w małej restauracji. Żółć jesiennych liści była piękna. ",
    "vietnamese": "Hôm nay trời mưa rất to nên chúng tôi ở nhà đọc sách và uống trà nóng. "
    "Ngày mai nếu trời nắng, cả nhà sẽ đi chợ mua rau và trái cây tươi. ",
    "russian": "Утром мы поехали на вокзал, но поезд уже ушёл, поэтому пришлось ждать "
    "следующий. Вечером все собрались дома и долго разговаривали о планах на лето. ",
    "greek": "Το πρωί πήγαμε στην αγορά για να αγοράσουμε ψωμί και φρούτα. Το απόγευμα "
    "καθίσαμε στην παραλία και διαβάσαμε βιβλία. ",
    "arabic": "ذهبنا في الصباح إلى السوق لشراء الخبز والفاكهة، ثم عدنا إلى البيت قبل الظهر. ",
    "hebrew": "בבוקר הלכנו לשוק לקנות לחם ופירות, ואחר כך חזרנו הביתה לפני הצהריים. ",
    "hindi": "सुबह हम बाज़ार गए और रोटी तथा फल खरीदे। शाम को हम बगीचे में बैठे और बात की। ",
    "thai": "ตอนเช้าเราไปตลาดเพื่อซื้อขนมปังและผลไม้แล้วกลับบ้านก่อนเที่ยง ",
    "japanese": "朝、私たちは市場へ行ってパンと果物を買い、昼前に家へ帰りました。",
    "korean": "아침에 우리는 시장에 가서 빵과 과일을 샀고 점심 전에 집으로 돌아왔습니다. ",
    "chinese": "的一是在不了有和人这中大为上个国我以要他时来用们生到作"
    "地于出就分对成会可主发年动同工",
}
# Emoji as code points: a family joined by U+200D and a heart with U+FE0F, which are
# invisible in source.
_EMOJI = "".join(
    map(chr, [0x1F600, 0x1F389, 0x1F44D, 0x1F3FD, 0x1F680, 0x1F355, 0x1F468, 0x200D, 0x1F469,
              0x200D, 0x1F467, 0x1F1EB, 0x1F1F7, 0x2764, 0xFE0F, 0x1F525, 0x2728, 0x1F40D])
)  # fmt: skip
_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _fill(text: str, chars: int) -> str:
    return (text * (chars // len(text) + 1))[:chars]


def _rng(name: str) -> random.Random:
    return random.Random(f"tokens:{name}")


def samples() -> dict[str, str]:
    """Every text, by name."""
    texts = {
        "prose": _fill(_PROSE, 4000),
        "code": _fill(_CODE, 4000),
        "json": json.dumps(_record(), indent=2),
        **_data(),
        **{name: _fill(text, 1500) for name, text in _LANGUAGES.items()},
        "emoji": _EMOJI * 20,
        **_hard(),
    }
    return texts


def _record() -> list[dict[str, object]]:
    rng = _rng("json")
    return [
        {
            "id": index,
            "title": f"notes {index}",
            "tags": ["draft", "work"][: rng.randint(1, 2)],
            "size": rng.randint(10, 90000),
            "shared": rng.random() < 0.5,
        }
        for index in range(40)
    ]


def _data() -> dict[str, str]:
    """Machine text: logs, tables, identifiers, digests."""
    rng = _rng("data")
    digest = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(125))
    logs = "".join(
        f"2026-09-{rng.randint(1, 28):02d} 13:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d} "
        f"INFO app.chat: turn done in {rng.randint(40, 9000)} ms (tools={rng.randint(0, 3)})\n"
        for _ in range(50)
    )
    table = "| Name | Size | Updated |\n|---|---:|---|\n" + "".join(
        f"| notes-{i}.md | {rng.randint(1, 90000):,} | 2026-09-{rng.randint(10, 28)} |\n"
        for i in range(60)
    )
    camel = ["getElementById", "parseJsonResponseBody", "addEventListener", "toLocaleDateString"]
    snake = ["user_profile_settings", "cache_key_prefix", "max_retry_count", "__init__"]
    return {
        "log lines": logs,
        "markdown table": table,
        "uuids": "\n".join(str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(60)),
        "hex": digest.hex()[:3000],
        "base64": base64.b64encode(digest).decode()[:3000],
        "camel case": " ".join(rng.choice(camel) for _ in range(200)),
        "snake case": " ".join(rng.choice(snake) for _ in range(200)),
    }


def _hard() -> dict[str, str]:
    """What a count of words misreads: whitespace, digits, letters that aren't words."""
    lower = _rng("lower")
    mixed = _rng("mixed")
    words = _rng("words")
    alnum = _rng("alnum")
    return {
        "spaces": "a" + " " * 1000 + "b",
        "newlines": "a" + "\n" * 1000 + "b",
        "space pairs": "a  b  " * 300,
        "digits": "".join(str(i * 7919 % 10) for i in range(2000)),
        "phone numbers": " ".join(f"+1 555-{100 + i:03d}-{1000 + 37 * i}" for i in range(100)),
        "dna": "ACGT" * 5000,
        "random letters": "".join(lower.choice(_LETTERS) for _ in range(4000)),
        "random cases": "".join(mixed.choice(_LETTERS + _LETTERS.upper()) for _ in range(3000)),
        "random words": " ".join(
            "".join(words.choice(_LETTERS) for _ in range(words.randint(3, 12))) for _ in range(400)
        ),
        "underscores": " ".join(["a_b_c_d_e_f_g_h"] * 200),
        "letters and digits": " ".join(
            "".join(alnum.choice(_LETTERS + "0123456789") for _ in range(12)) for _ in range(250)
        ),
        "punctuation": "!?.,;:()[]{}<>@#$%^&*~`|/\"'" * 60,
        "combining marks": ("x" + chr(0x301)) * 500,
        "beyond the plane": chr(0x1D54F) * 400,
        "full width": "".join(chr(0xFF21 + i % 26) for i in range(900)),
    }
