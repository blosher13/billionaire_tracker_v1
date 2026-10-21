"""Checks for discussion posts: text only, no links or images, no swear words or sexual language.

The word list is config/blocked_words.txt (one word per line, edit freely, restart the API):
  word     blocks the word on its own, with common endings (ass, asses; not "class" or "assess")
  *word    blocks it anywhere inside a word too (fuck -> motherfucker, fuckface)
  =word    blocks only that exact word (spic, but not spicy or spices)

Common disguises are undone before matching: letter/number swaps (sh1t, $hit, @ss), stretched
letters (fuuuck), letters split by spaces or dots (f u c k, f.u.c.k), accents and look-alike
Unicode letters, and invisible characters.
"""

import re
import unicodedata
from pathlib import Path

from producer.config import PROJECT_ROOT

WORDS_FILE = PROJECT_ROOT / "config" / "blocked_words.txt"

MAX_BODY = 1000
MAX_AUTHOR = 40

ENDINGS = r"(?:s|es|ed|er|ers|ing|y|ies)?"
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
                      "@": "a", "$": "s", "!": "i", "|": "i", "+": "t"})

# Links in any spelling: scheme, www., e-mail, IP address, or something.tld (also "example(dot)com").
TLDS = ("com|net|org|io|co|me|ly|gg|xyz|app|dev|info|biz|ru|uk|us|ca|de|fr|tv|link|site|online|shop|"
        "store|to|be|cc|ai|tk|ml|ga|cf|gq|top|club|live|pro|in|cn|jp|au|nl|eu|onion|bit|lol|fun|vip|"
        "click|page|news|blog|tech|space|website|ws|is|it|es|pl|br|mx|ch|se|no|fi|dk|at|nz|za|sg|hk|kr")
LINK_PATTERNS = [
    re.compile(r"[a-z][a-z0-9+.-]*://", re.I),                                  # http://, ftp://
    re.compile(r"\bwww\s*\.", re.I),
    re.compile(r"\b(?:data|javascript|blob|file)\s*:", re.I),
    re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I),                            # e-mail
    re.compile(rf"\b[\w-]+(?:\.|\s*[(\[]\s*dot\s*[)\]]\s*)(?:{TLDS})\b", re.I),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),                                 # IP address
]
# Image or HTML markup: posts are plain text and shown as plain text.
MARKUP = re.compile(r"<\s*/?\s*[a-z!]|!\[[^\]]*\]\(|\[[^\]]+\]\(", re.I)


class Rejected(ValueError):
    """A post broke a rule; the message is shown to the person posting."""


def _load_patterns() -> tuple[re.Pattern, re.Pattern]:
    groups: dict[str, list[str]] = {"": [], "*": [], "=": []}
    for line in WORDS_FILE.read_text().splitlines():
        word = line.split("#", 1)[0].strip().lower()
        if not word:
            continue
        kind = word[0] if word[0] in "*=" else ""
        # Each letter may repeat (fuuuck) and spaces inside phrases are optional.
        groups[kind].append("".join(re.escape(c) + "+" if c != " " else r"\s*" for c in word.lstrip("*=")))
    whole = rf"^(?:(?:{'|'.join(groups[''])}){ENDINGS}|{'|'.join(groups['='])})$"
    return re.compile(whole), re.compile("|".join(groups["*"]))


WHOLE, ANYWHERE = _load_patterns()


def _plain(text: str) -> str:
    """Lower case, look-alike letters and accents folded, invisible characters dropped."""
    text = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text))
    return "".join(c for c in text if not unicodedata.combining(c) and unicodedata.category(c) != "Cf").lower()


def _words(text: str) -> list[str]:
    """The words to check: each word as typed, plus runs of single letters joined (f u c k)."""
    tokens = re.findall(r"[a-z]+", _plain(text).translate(LEET))
    words, run = list(tokens), []
    for token in tokens + [""]:
        if len(token) == 1:
            run.append(token)
            continue
        if len(run) >= 3:
            words.append("".join(run))
        run = []
    return words


def is_profane(text: str) -> bool:
    words = _words(text)
    return any(WHOLE.match(w) or ANYWHERE.search(w) for w in words) or bool(ANYWHERE.search(" ".join(words)))


def clean_text(text: str, field: str, max_len: int, required: bool) -> str:
    """Return the tidied text, or raise Rejected with a message for the person posting."""
    text = "".join(c for c in (text or "") if c in "\n\t" or unicodedata.category(c)[0] != "C")
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\t", " ")).strip()
    if required and len(text) < 2:
        raise Rejected(f"Write a {field} first.")
    if len(text) > max_len:
        raise Rejected(f"Keep the {field} under {max_len} characters (it's {len(text)}).")
    if MARKUP.search(text):
        raise Rejected(f"The {field} can only be plain text: no images, HTML or link markup.")
    if any(p.search(_plain(text)) for p in LINK_PATTERNS):
        raise Rejected(f"Links and web or e-mail addresses aren't allowed. Remove them from the {field} and try again.")
    if is_profane(text):
        raise Rejected(f"Swear words and sexual language aren't allowed. Reword the {field} and try again.")
    return text
