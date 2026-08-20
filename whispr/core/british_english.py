"""
british_english.py — UK/British English localisation for the STT output.

Whisper is multilingual and handles British accents well, but it was trained on
a lot of US text, so it frequently emits US spellings ("color", "organize",
"center") even when a British speaker said the British word. There's no separate
"en-GB" Whisper model, so the correct, reliable fix is:

  1. Tell Whisper it's British English via language="en" + an accent-context
     initial_prompt written in British spelling (biases the decoder).
  2. Deterministically normalise the output US -> UK spelling afterwards.

Step 2 is what actually guarantees British spelling. It's a pure text transform,
so it's fully testable here. It's rule-based (suffix patterns) plus an explicit
exceptions map for irregulars, and it preserves capitalisation.

This is exactly how commercial "British English" modes are implemented on top of
US-centric models.
"""
from __future__ import annotations
import re

# Accent-context prompt, written in British spelling so the decoder is nudged
# toward it. Kept short (Whisper only reads ~224 prompt tokens).
UK_ACCENT_PROMPT = (
    "The following is British English speech, spelled the British way: "
    "colour, favourite, organise, realise, centre, theatre, licence, "
    "travelling, analyse, catalogue, programme, cheque, kerb, tyre."
)

# --- Irregular / non-suffix US->UK words. Keys are lowercase US forms. ---
_UK_EXCEPTIONS = {
    "airplane": "aeroplane",
    "aluminum": "aluminium",
    "analog": "analogue",
    "ax": "axe",
    "catalog": "catalogue",
    "check": "cheque",          # NOTE: financial sense; risk on "check the box"
    "cozy": "cosy",
    "curb": "kerb",             # NOTE: roadside sense
    "defense": "defence",
    "dialog": "dialogue",
    "donut": "doughnut",
    "draft": "draught",         # NOTE: beer/air sense
    "gray": "grey",
    "jewelry": "jewellery",
    "license": "licence",       # noun sense
    "maneuver": "manoeuvre",
    "mold": "mould",
    "mustache": "moustache",
    "offense": "offence",
    "pajamas": "pyjamas",
    "plow": "plough",
    "practice": "practise",     # verb sense; noun is 'practice' in UK too
    "program": "programme",     # NOTE: not computer 'program' in UK either — see below
    "skeptic": "sceptic",
    "skeptic": "sceptic",
    "specialty": "speciality",
    "story": "storey",          # NOTE: building floor sense only — too risky, excluded below
    "sulfur": "sulphur",
    "tire": "tyre",             # NOTE: wheel sense
    "ton": "tonne",             # risky, excluded below
    "traveler": "traveller",
    "willful": "wilful",
    "fulfill": "fulfil",
    "enroll": "enrol",
    "install": "install",       # same in UK
    "aging": "ageing",
    "judgment": "judgement",
}

# Words above that are too context-dependent to flip safely by default.
# (e.g. "check the box" must NOT become "cheque the box"). We keep the app
# safe-by-default and let these be handled by the user's own corrections/vocab.
_TOO_RISKY = {"check", "curb", "draft", "practice", "program", "story", "ton", "tire"}

_APPLIED_EXCEPTIONS = {k: v for k, v in _UK_EXCEPTIONS.items() if k not in _TOO_RISKY}

_WORD_RE = re.compile(r"[A-Za-z]+")


def _match_case(src: str, tgt: str) -> str:
    if src.isupper():
        return tgt.upper()
    if src[:1].isupper():
        return tgt[:1].upper() + tgt[1:]
    return tgt


def _suffix_rules(word: str) -> str | None:
    """
    Regular US->UK suffix transforms. Return the UK form (lowercase reasoning)
    or None if no rule applies. Operates on a lowercase word; caller restores case.
    """
    w = word

    # -ize / -ization -> -ise / -isation   (but keep 'ize' words that are UK-fine
    # like 'size', 'prize', 'seize' — those don't end the stem in a verb-forming ize)
    if w.endswith("ization") and len(w) > 8:
        return w[:-7] + "isation"
    if w.endswith("izational") and len(w) > 10:
        return w[:-9] + "isational"
    if w.endswith("ize") and len(w) > 4 and w not in (
        "size", "prize", "seize", "maize", "capsize"
    ):
        return w[:-3] + "ise"
    if w.endswith("izer") and len(w) > 5:
        return w[:-4] + "iser"
    if w.endswith("izing") and len(w) > 6:
        return w[:-5] + "ising"
    if w.endswith("ized") and len(w) > 5:
        return w[:-4] + "ised"

    # -yze -> -yse (analyze -> analyse, paralyze -> paralyse)
    if w.endswith("yze") and len(w) > 4:
        return w[:-3] + "yse"
    if w.endswith("yzed") and len(w) > 5:
        return w[:-4] + "ysed"
    if w.endswith("yzing") and len(w) > 6:
        return w[:-5] + "ysing"

    # -or -> -our  (color->colour, favor->favour, neighbor->neighbour)
    _OUR = {
        "color": "colour", "favor": "favour", "flavor": "flavour",
        "honor": "honour", "humor": "humour", "labor": "labour",
        "neighbor": "neighbour", "rumor": "rumour", "vapor": "vapour",
        "behavior": "behaviour", "harbor": "harbour", "odor": "odour",
        "savior": "saviour", "splendor": "splendour", "valor": "valour",
        "endeavor": "endeavour", "favorite": "favourite",
        "favorable": "favourable", "colored": "coloured",
        "coloring": "colouring", "honorable": "honourable",
        "neighborhood": "neighbourhood", "neighboring": "neighbouring",
    }
    if w in _OUR:
        return _OUR[w]
    # plural: colors -> colours, favorites -> favourites
    if w.endswith("s") and w[:-1] in _OUR:
        return _OUR[w[:-1]] + "s"

    # -er -> -re  (center->centre, theater->theatre, meter->metre, liter->litre)
    _RE = {
        "center": "centre", "theater": "theatre", "meter": "metre",
        "liter": "litre", "fiber": "fibre", "caliber": "calibre",
        "somber": "sombre", "specter": "spectre", "scepter": "sceptre",
        "centers": "centres", "meters": "metres", "liters": "litres",
        "centered": "centred", "centering": "centring",
    }
    if w in _RE:
        return _RE[w]
    if w.endswith("s") and w[:-1] in _RE:
        return _RE[w[:-1]] + "s"

    # -og -> -ogue handled in exceptions (catalog/dialog/analog)

    # doubled-L before suffix (traveling->travelling, canceled->cancelled,
    # modeling->modelling, labeled->labelled, fueled->fuelled)
    _LL = {
        "traveling": "travelling", "traveled": "travelled",
        "traveler": "traveller", "canceling": "cancelling",
        "canceled": "cancelled", "modeling": "modelling",
        "modeled": "modelled", "labeling": "labelling",
        "labeled": "labelled", "fueling": "fuelling", "fueled": "fuelled",
        "signaling": "signalling", "signaled": "signalled",
        "counseling": "counselling", "counseled": "counselled",
        "marveling": "marvelling", "marveled": "marvelled",
        "totaling": "totalling", "totaled": "totalled",
    }
    if w in _LL:
        return _LL[w]

    return None


def to_british(text: str) -> str:
    """Convert US spellings in `text` to UK spellings, preserving case & spacing."""
    if not text:
        return text

    def repl(m: re.Match) -> str:
        word = m.group(0)
        lw = word.lower()
        # exceptions first (irregulars)
        if lw in _APPLIED_EXCEPTIONS:
            return _match_case(word, _APPLIED_EXCEPTIONS[lw])
        # then regular suffix rules
        uk = _suffix_rules(lw)
        if uk and uk != lw:
            return _match_case(word, uk)
        return word

    return _WORD_RE.sub(repl, text)
