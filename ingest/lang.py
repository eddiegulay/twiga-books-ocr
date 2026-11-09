"""Dependency-free language sniffing (sw / en / fr / zh / ar). Used to cross-check catalogue labels:
several files in the corpus are labelled English-medium but contain the Kiswahili edition, and vice versa."""
import re
from collections import Counter

_SW = set("na ya wa kwa ni za la katika au kama pia hii huu hiki hizi yake wake lakini kuwa ili sana wao sisi mimi wewe ndiyo hapo zaidi kila baada kabla mtu watu mwalimu darasa zoezi".split())
_EN = set("the and of to in is are for with that this on as it be by from or an at which can you your we they their what how use used".split())
_FR = set("le la les des du de et est un une pour dans que qui sur avec nous vous sont ce cette aux au pas plus".split())


def detect_language(text: str) -> str:
    """Return 'sw', 'en', 'fr', 'zh', 'ar' or 'unknown'."""
    sample = text[:60000]
    letters = [c for c in sample if c.isalpha()]
    if len(letters) < 80:
        return "unknown"
    n = len(letters)
    cjk = sum("一" <= c <= "鿿" for c in letters)
    arab = sum("؀" <= c <= "ۿ" for c in letters)
    if cjk / n > 0.3:
        return "zh"
    if arab / n > 0.3:
        return "ar"
    words = re.findall(r"[a-zà-ÿ']+", sample.lower())
    if len(words) < 30:
        return "unknown"
    c = Counter(words)
    score = {k: sum(c[w] for w in v) / len(words) for k, v in (("sw", _SW), ("en", _EN), ("fr", _FR))}
    best = max(score, key=score.get)
    return best if score[best] >= 0.04 else "unknown"


_LABEL = {"kiswahili": "sw", "english": "en", "english-medium": "en", "kiswahili-medium": "sw",
          "french": "fr", "chinese": "zh", "arabic": "ar"}


def label_to_code(label: str, subject: str = "", file_name: str = "") -> str:
    """Language the catalogue/filename claims (medium of instruction or language subject)."""
    key = re.sub(r"\s*\(\d+\)$", "", (label or "").strip().lower())
    if key in _LABEL:
        return _LABEL[key]
    s = f"{subject} {file_name}".lower()
    for needle, code in (("kichina", "zh"), ("chinese", "zh"), ("kiarabu", "ar"), ("arabic", "ar"),
                         ("kifaransa", "fr"), ("french", "fr")):
        if needle in s:
            return code
    return ""
