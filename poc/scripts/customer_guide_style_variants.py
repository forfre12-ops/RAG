"""Restricted sentence-ending edits for the frozen batch04 diagnostic panel.

No generic Korean paraphrasing. Unknown declarative endings fail closed;
requests, headings, numbers, conditions and all non-edited spans stay intact.
The whitelist is authored linguistic judgment, not semantic certification.
"""
from __future__ import annotations

import re

from koipa.policy_facts import require, text_digest

PAIRS_TEXT = """
값이다 값입니다
같았다 같았습니다
갱신한다 갱신합니다
검증했다 검증했습니다
결과다 결과입니다
결정한다 결정합니다
계획한다 계획합니다
고친다 고칩니다
관찰한다 관찰합니다
구별한다 구별합니다
구분한다 구분합니다
구분했다 구분했습니다
기록한다 기록합니다
나눈다 나눕니다
나눴다 나눴습니다
남겼다 남겼습니다
남긴다 남깁니다
넣는다 넣습니다
놓는다 놓습니다
늘린다 늘립니다
다르다 다릅니다
대응시킨다 대응시킵니다
대조한다 대조합니다
대조했다 대조했습니다
덧붙인다 덧붙입니다
된다 됩니다
둔다 둡니다
따른다 따릅니다
때문이다 때문입니다
맞춘다 맞춥니다
맞췄다 맞췄습니다
모았다 모았습니다
모은다 모읍니다
묶었다 묶었습니다
반복한다 반복합니다
반영한다 반영합니다
반영했다 반영했습니다
받는다 받습니다
배정한다 배정합니다
변경이다 변경입니다
보관한다 보관합니다
보낸다 보냅니다
보류한다 보류합니다
보존한다 보존합니다
보존했다 보존했습니다
본다 봅니다
분리한다 분리합니다
분리했다 분리했습니다
분석했다 분석했습니다
비교한다 비교합니다
비교했다 비교했습니다
사용했다 사용했습니다
살핀다 살핍니다
선택했다 선택했습니다
센다 셉니다
소개한다 소개합니다
수량이다 수량입니다
수집한다 수집합니다
실험이다 실험입니다
아니다 아닙니다
안정화한다 안정화합니다
않는다 않습니다
않았다 않았습니다
얻었다 얻었습니다
연결한다 연결합니다
연결했다 연결했습니다
예정이다 예정입니다
옮겼다 옮겼습니다
옮긴다 옮깁니다
용도다 용도입니다
위치다 위치입니다
위해서다 위해서입니다
유지한다 유지합니다
읽는다 읽습니다
읽었다 읽었습니다
있다 있습니다
저장한다 저장합니다
적는다 적습니다
적용한다 적용합니다
적용했다 적용했습니다
정렬한다 정렬합니다
정리한다 정리합니다
정했다 정했습니다
제시한다 제시합니다
제조했다 제조했습니다
조건이다 조건입니다
존재했다 존재했습니다
진행한다 진행합니다
찾는다 찾습니다
처리한다 처리합니다
체계다 체계입니다
추가한다 추가합니다
취합이다 취합입니다
취합한다 취합합니다
판독이다 판독입니다
판독했다 판독했습니다
평가한다 평가합니다
포함했다 포함했습니다
표시한다 표시합니다
표시했다 표시했습니다
필요하다 필요합니다
한다 합니다
했다 했습니다
확인한다 확인합니다
확인했다 확인했습니다
"""
PAIRS = [tuple(line.split()) for line in PAIRS_TEXT.strip().splitlines()]
LOOKUP = {word: pair for pair in PAIRS for word in pair}
require(len(LOOKUP) == len(PAIRS)*2, "style_pair_ambiguous")
TERMINAL = re.compile(r"(?<!\S)([^\s]+?)([.!?])(?=\s|$)")
NUMBER_WORD = re.compile(r"(\d+(?:\.\d+)?(?:N/cm|mm|cm|mL|mg|ms|μm|N|g|%|개|건|명|장|행|쪽|칸|곳|대|분|도))(이었습니다|였습니다|이었다|였다|입니다|이다|다)")
UNCHANGED_ENDINGS = {"주세요"}  # Preserve request force; never convert it to a factual assertion.


def word_pair(word):
    if word in LOOKUP:
        return LOOKUP[word]
    match = NUMBER_WORD.fullmatch(word)
    if match:
        stem, ending = match.groups()
        if ending in {"이었다", "이었습니다"}:
            return stem+"이었다", stem+"이었습니다"
        if ending in {"였다", "였습니다"}:
            return stem+"였다", stem+"였습니다"
        return stem+("다" if ending == "다" else "이다"), stem+"입니다"
    return None


def transform(text, style):
    require(style in {"plain", "polite"} and type(text) is str and bool(text), "style_transform_input_invalid")
    edits, pieces, cursor, shift, eligible = [], [], 0, 0, 0
    preserved_requests, fragments = [], []
    for match in TERMINAL.finditer(text):
        word = match.group(1)
        pair = word_pair(word)
        if pair is None:
            if word in UNCHANGED_ENDINGS:
                preserved_requests.append({"start": match.start(1), "word": word})
            else:
                require(not word.endswith(("다", "요")), "style_unreviewed_ending")
                fragments.append({"start": match.start(1), "word": word})
            continue
        eligible += 1
        target = pair[0 if style == "plain" else 1]
        if word == target:
            continue
        start, end = match.span(1)
        pieces.extend([text[cursor:start], target])
        edits.append({"source_start": start, "source_end": end, "before": word, "after": target,
                      "target_start": start+shift, "target_end": start+shift+len(target)})
        shift += len(target)-len(word)
        cursor = end
    pieces.append(text[cursor:])
    result = "".join(pieces)
    require(re.findall(r"\d+(?:\.\d+)?", text) == re.findall(r"\d+(?:\.\d+)?", result), "style_numeric_sequence_changed")
    require(text.splitlines()[0] == result.splitlines()[0], "style_title_changed")
    return {"style": style, "text": result, "body_sha256": text_digest(result), "edits": edits,
            "eligible_terminal_words": eligible, "preserved_requests": preserved_requests,
            "unchanged_fragments": fragments, "changed": result != text,
            "whole_semantic_equivalence_certified": False}


def rebind_claims(claims, source, variant):
    edits, target = variant["edits"], variant["text"]

    def mapped(offset):
        require(not any(e["source_start"] < offset < e["source_end"] for e in edits), "style_claim_cuts_edit")
        return offset + sum(len(e["after"])-len(e["before"]) for e in edits if e["source_end"] <= offset)

    out = []
    for claim in claims:
        c = claim.model_dump() if hasattr(claim, "model_dump") else claim
        require(source[c["start"]:c["end"]] == c["quote"], "style_source_claim_mismatch")
        start, end = mapped(c["start"]), mapped(c["end"])
        quote = target[start:end]
        out.append({**c, "start": start, "end": end, "quote": quote, "claim": quote, "sha256": text_digest(quote)})
    return out
