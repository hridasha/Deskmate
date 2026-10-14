"""A cheap, LLM-free router: pick the right specialist (and guess an emotion) from text.

This is a confidence-gated classifier in the spirit of a decision model: when it is sure, the system
skips an expensive planning call; when it is not, the LLM planner decides.
"""
import math
import re
from collections import Counter

STOP = set(
    "a an the and or but if then than that this these those is are was were be been being to of in on for with at by from as it its "
    "i you he she we they me my your our their us them do does did have has had will would can could should may might must not no yes "
    "please just also very really about into over under again more most some any all ask have tell get want need help give make "
    "what which who whom how when where why there here out up down so too one two".split()
)

# Expertise hints for the built-in cast, so routing works without any training data.
HINTS: dict[str, str] = {
    "oscar_martinez": "accounting finance money interest tax budget invoice expenses investing math explain correct facts economics",
    "dwight_schrute": "research facts verify investigate security rules survival farming beets data sources study evidence",
    "pam_beesly": "schedule plan calendar organize design art creative layout drawing planning study week agenda",
    "jim_halpert": "status update casual joke funny prank easy everyday task todo humor email quick summary",
    "angela_martin": "edit proofread review audit grammar standards policy strict quality check errors",
    "kevin_malone": "numbers calculate simple math counting chili food estimate basic arithmetic times multiply divided plus minus much total sum percent",
    "stanley_hudson": "short brief concise crossword answer quickly minimal direct yes no",
    "darryl_philbin": "logistics shipping warehouse project steps timeline resources inventory delivery process practical",
}


_SUFFIXES = (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", ""))


def stem(word: str) -> str:
    """A light stemmer so 'returns', 'returning' and 'returned' all match 'return'."""
    for suffix, replacement in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3 and not (suffix == "s" and word.endswith("ss")):
            return word[: -len(suffix)] + replacement
    return word


def tokens(text: str) -> list[str]:
    return [stem(w) for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP and len(w) > 2]


class Router:
    """TF-IDF cosine similarity between a request and each agent's profile (role + persona + hints)."""

    def __init__(self, agents: dict[str, dict]):
        self.ids = list(agents)
        docs = []
        for aid, a in agents.items():
            persona = re.sub(r"^You are [^.]*\.", "", a.get("persona", ""))
            docs.append(tokens(f"{a.get('archetype', '')} {a.get('archetype', '')} {HINTS.get(aid, '')} {HINTS.get(aid, '')} {persona}"))
        n = len(docs)
        df = Counter(t for d in docs for t in set(d))
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
        self.vecs = [self._vec(d) for d in docs]

    def _vec(self, toks: list[str]) -> dict[str, float]:
        tf = Counter(toks)
        v = {t: (1 + math.log(c)) * self.idf.get(t, 1.0) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def scores(self, text: str) -> list[tuple[str, float]]:
        q = self._vec([t for t in tokens(text) if t in self.idf])
        out = [(aid, sum(w * vec.get(t, 0.0) for t, w in q.items())) for aid, vec in zip(self.ids, self.vecs)]
        return sorted(out, key=lambda x: x[1], reverse=True)

    @staticmethod
    def confident(scores: list[tuple[str, float]], min_top: float = 0.18, ratio: float = 1.4) -> bool:
        if not scores or scores[0][1] < min_top:
            return False
        return len(scores) < 2 or scores[0][1] >= ratio * max(scores[1][1], 1e-9)


def explicit_targets(text: str, agents: dict[str, dict]) -> list[str]:
    """Agents the user named ('ask Oscar and Pam to...'), in the order they appear."""
    found: list[tuple[int, str]] = []
    low = text.lower()
    for aid, a in agents.items():
        first = a["name"].split()[0].lower()
        for m in re.finditer(rf"\b{re.escape(first)}\b", low):
            found.append((m.start(), aid))
            break
    return [aid for _, aid in sorted(found)]


def clean_task(message: str, names: list[str]) -> str:
    """Turn 'Ask Oscar to explain X, then summarize for me' into 'Explain X'."""
    text = message.strip()
    for n in names:
        first = re.escape(n.split()[0])
        text = re.sub(rf"^(?:please\s+)?(?:ask|have|tell|get|let)\s+{first}(?:\s+and\s+\w+)?\s+(?:to\s+|for\s+)?", "", text, flags=re.I)
    text = re.sub(r",?\s*(?:and\s+)?then\s+(?:summari[sz]e|give me|tell me|report).*$", "", text, flags=re.I)
    text = text.strip(" ,.")
    return (text[:1].upper() + text[1:]) if text else message.strip()


EMOTION_WORDS = {
    "happy": ["great", "glad", "congrat", "awesome", "love", "yay", "wonderful", "fantastic", "delighted", "proud", "excellent", "happy"],
    "annoyed": ["sigh", "ugh", "unfortunately", "annoying", "honestly", "fine.", "whatever", "tedious", "again?"],
    "surprised": ["wow", "whoa", "really?", "surprising", "unexpected", "no way", "shocked", "incredible"],
    "thinking": ["hmm", "let me think", "consider", "perhaps", "it depends", "on the other hand", "let's see"],
}


def guess_emotion(text: str) -> str:
    """Fallback when the model forgets its emotion tag: a small lexicon classifier."""
    low = text.lower()
    best, score = "neutral", 0
    for emo, words in EMOTION_WORDS.items():
        s = sum(low.count(w) for w in words) + (low.count("!") * 0.3 if emo == "happy" else 0)
        if s > score:
            best, score = emo, s
    return best


_DELEGATE_VERBS = r"(?:ask|have|tell|get|let|check with|consult|loop in|bring in|delegate to|assign to)"


def delegated_targets(text: str, agents: dict[str, dict]) -> list[str]:
    """Agents the user is handing work to: a delegation verb followed directly by a name ('ask Oscar and Pam to ...').
    'Tell me where Oscar is' does NOT count: there the verb is followed by 'me', not a name."""
    firsts = {a["name"].split()[0].lower(): aid for aid, a in agents.items()}
    if not firsts:
        return []
    names = "|".join(re.escape(f) for f in firsts)
    found: list[tuple[int, str]] = []
    for m in re.finditer(rf"\b{_DELEGATE_VERBS}\s+(?:both\s+)?((?:{names})(?:\s*(?:,|and|&)\s*(?:{names}))*)\b", text, re.IGNORECASE):
        for n in re.findall(names, m.group(1), re.IGNORECASE):
            found.append((m.start(1) + m.group(1).lower().find(n.lower()), firsts[n.lower()]))
    seen, out = set(), []
    for _, aid in sorted(found):
        if aid not in seen:
            seen.add(aid)
            out.append(aid)
    return out
