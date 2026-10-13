"""글자 2-gram TF-IDF 낱말 채널 — 형태소 분석 없이 한국어 조사 변화에 버틴다. 외부 의존 없음(numpy 만).

시험(measure_regulation_evidence.py)의 `Lexical` 은 조각마다 사전을 훑는 순수 파이썬 루프였다. 조항이 수백 개면
요청당 수백 ms 라서, 여기서는 **역색인(posting)** 으로 바꿨다 — 질의의 글자 2-gram 마다 그 2-gram 이 든 조항의
(번호 배열, 가중치 배열)만 numpy 로 더한다. 점수는 시험의 값과 같다(코사인 = 정규화된 두 벡터의 내적).
"""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np


def _bigrams(text: str) -> Counter:
    s = re.sub(r"\s+", "", text)
    return Counter(s[i:i + 2] for i in range(len(s) - 1))


class LexicalIndex:
    def __init__(self, docs: list[str]) -> None:
        self.n = len(docs)
        tfs = [_bigrams(d) for d in docs]
        df: Counter = Counter()
        for tf in tfs:
            df.update(tf.keys())
        self.idf = {g: math.log((self.n + 1) / (c + 1)) + 1 for g, c in df.items()}
        post_idx: dict[str, list[int]] = {}
        post_w: dict[str, list[float]] = {}
        for i, tf in enumerate(tfs):
            w = {g: (1 + math.log(c)) * self.idf[g] for g, c in tf.items()}
            norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
            for g, v in w.items():
                post_idx.setdefault(g, []).append(i)
                post_w.setdefault(g, []).append(v / norm)
        self._idx = {g: np.asarray(v, dtype=np.int32) for g, v in post_idx.items()}
        self._w = {g: np.asarray(v, dtype=np.float32) for g, v in post_w.items()}

    def scores(self, query: str) -> np.ndarray:
        """질의 글자와 각 조항의 코사인 유사도(길이 n). 색인에 없는 글자는 무시한다."""
        out = np.zeros(self.n, dtype=np.float32)
        if self.n == 0:
            return out
        tf = _bigrams(query)
        qw = {g: (1 + math.log(c)) * self.idf[g] for g, c in tf.items() if g in self.idf}
        norm = math.sqrt(sum(v * v for v in qw.values())) or 1.0
        for g, v in qw.items():
            out[self._idx[g]] += (v / norm) * self._w[g]
        return out
