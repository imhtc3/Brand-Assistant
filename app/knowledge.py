"""FAQ retrieval with TF-IDF.

Every FAQ question and each of its example phrasings is indexed as its own
document; an FAQ's score is the best match among its phrasings. Word n-grams
catch meaning, character n-grams catch typos ("refnd", "coupn") that are
common in chat messages.

Text is normalised first: common Hinglish and chat shorthand is mapped to
English, and filler words plus the brand's own name are removed, because
"what is the..." or "Kirana" carry no information about which FAQ is meant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion

from .config import FAQ

# Hinglish / chat shorthand -> English. Small on purpose; extend per client
# from the "top_unanswered" list in the insights report.
NORMALISE = {
    "u": "you", "ur": "your", "cod": "cash on delivery", "pls": "please", "plz": "please", "msg": "message",
    "kab": "when", "milega": "get", "milegi": "get", "kahan": "where", "kaha": "where",
    "kitna": "how much", "kitne": "how many", "paisa": "money", "paise": "money",
    "nahi": "not", "nhi": "not", "aaya": "arrived", "aayega": "arrive", "wapas": "back",
    "toota": "broken", "tuta": "broken", "galat": "wrong", "chahiye": "want",
}
# Hand-written filler list. scikit-learn's ENGLISH_STOP_WORDS is not used because
# it contains domain words that matter here ("bill", "call", "back", "system",
# "fire", "full"). Negations and question words are deliberately kept.
STOP = {
    "a", "an", "the", "is", "am", "are", "was", "were", "be", "been", "being", "do", "does", "did",
    "i", "me", "my", "mine", "we", "our", "you", "your", "it", "its", "this", "that", "these", "those",
    "to", "of", "in", "on", "at", "for", "with", "by", "from", "about", "as", "into", "up",
    "and", "or", "but", "so", "if", "then", "can", "could", "would", "should", "will", "shall", "may",
    "what", "which", "who", "whom", "there", "here", "just", "very", "really", "also", "any", "some",
    "have", "has", "had", "get", "got", "please", "pls", "hi", "hello", "hey", "want", "need", "tell",
    "know", "let", "us", "yet", "today", "now", "since",
}


def normalise(text: str, extra_stop: set[str] = frozenset()) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        for t in NORMALISE.get(w, w).split():
            if t not in STOP and t not in extra_stop:
                out.append(t)
    return " ".join(out)


@dataclass
class Match:
    faq: FAQ
    score: float


class FAQRetriever:
    def __init__(self, faqs: list[FAQ], brand_name: str = ""):
        self.faqs = faqs
        self._brand_stop = set(re.findall(r"[a-z0-9]+", brand_name.lower()))
        texts, owners = [], []
        for i, faq in enumerate(faqs):
            # The answer is indexed too: it holds specifics (cities, hours) customers ask about.
            for phrase in [faq.question, *faq.examples, faq.answer]:
                texts.append(self._prep(phrase))
                owners.append(i)
        self._owners = np.array(owners)
        self._vectorizer = FeatureUnion([
            ("word", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)),
        ])
        self._matrix = self._l2(self._vectorizer.fit_transform(texts))

    def _prep(self, text: str) -> str:
        return normalise(text, self._brand_stop)

    @staticmethod
    def _l2(m):
        # FeatureUnion stacks two normalised blocks, so renormalise to keep cosine in [0, 1].
        norms = np.sqrt(m.multiply(m).sum(axis=1)).A1
        norms[norms == 0] = 1.0
        return m.multiply(1 / norms[:, None]).tocsr()

    def search(self, text: str, k: int = 3) -> list[Match]:
        prepped = self._prep(text)
        if not prepped:
            return [Match(f, 0.0) for f in self.faqs[:k]]
        q = self._l2(self._vectorizer.transform([prepped]))
        sims = (self._matrix @ q.T).toarray().ravel()
        best = np.zeros(len(self.faqs))
        np.maximum.at(best, self._owners, sims)
        order = np.argsort(-best)[:k]
        return [Match(self.faqs[i], float(best[i])) for i in order]
