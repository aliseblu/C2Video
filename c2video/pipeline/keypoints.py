"""Grounded extractive key points for the account-free local writer.

This ranks complete sentences across ALL supplied text; it is not an LLM and
does not claim abstractive rewriting or access to the original full article.
"""

from __future__ import annotations

import re
from collections import Counter

_LEAD = re.compile(
    r"^(?:(?:但是|所以|因此)[，,]?\s*)?"
    r"(?:(?:我的?一个?|本文的?|作者的?)\s*)?"
    r"(?:核心观点|结论|总结|总的来说|综上所述|简而言之|一句话总结)"
    r"(?:是|在于)?[：:,，]?\s*"
)
_FLUFF = re.compile(
    r"^(?:大家好|各位好|亲爱的知友|您好|你好|欢迎|感谢[大家各位]|"
    r"谢邀|先自我介绍|今天给大家|本文将|这篇文章将|点赞|关注我|收藏本文|哈哈)"
)
_CONCLUSION = re.compile(r"核心观点|结论|总结|总的来说|综上|简而言之|关键(?:是|在于)|不是.+而是")
_CAVEAT = re.compile(r"^(?:但|不过|然而|需要注意|值得注意|仅限|只适用|前提|不代表|不能据此)")
_DEPENDENT = re.compile(r"^(?:这(?:一|个|种|些|样)|它|其|上述|因此|所以|也就是说|比如|例如)")
_BACKGROUND = re.compile(r"^(?:首先介绍|先说背景|过去|长期以来|回顾|在讨论|说起)")


def _sentences(text: str) -> list[str]:
    """Keep conditions, negations, decimal numbers and quoted speech intact."""
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"https?://[^\s，。；！？]+", "", text)
    text = text.replace("\u200b", "").replace("\r", "\n")
    pairs = {"“": "”", "‘": "’", "「": "」", "『": "』", "（": "）", "(": ")"}
    stack: list[str] = []
    parts: list[str] = []
    start = 0
    for i, char in enumerate(text):
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        boundary = char in "。！？!?；;\n" or (
            char == "." and (i + 1 == len(text) or text[i + 1].isspace())
        )
        if boundary and not stack:
            parts.append(text[start : i + 1])
            start = i + 1
    parts.append(text[start:])
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        part = " ".join(part.split()).strip("；; \t")
        part = re.sub(r"^(?:[-*•]|\d+[、.)])\s*", "", part)
        key = re.sub(r"\W", "", part).lower()
        if len(key) < 5 or key in seen or _FLUFF.search(part):
            continue
        seen.add(key)
        result.append(part)
    return result


def _tokens(text: str) -> set[str]:
    words = set(re.findall(r"[a-z][a-z0-9_-]+", text.lower()))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(run[i : i + 2] for i in range(len(run) - 1))
    return words


def summarize_keypoints(text: str, *, title: str = "", budget: int = 180) -> str:
    """Conclusion first, then non-redundant supporting facts, without invention.

    Budget is soft: an attached caveat or a complete thought outranks timing.
    Sparse/title-only input fails explicitly instead of fabricating an answer.
    """
    if (
        re.search(r"索引|合集|分类目录", title + text[:200])
        and len(re.findall(r"[？?]", text)) >= 5
    ):
        raise ValueError("素材是目录或问题列表，缺少可概括的正文；请选择具体回答或文章。")
    sentences = _sentences(text)
    candidates = [i for i, s in enumerate(sentences) if not s.endswith(("?", "？"))]
    if not candidates:
        raise ValueError("素材缺少可概括的正文或摘要，请补充内容后重试；不能仅凭问题标题编造答案。")
    tokens = [_tokens(s) for s in sentences]
    frequencies = Counter(token for group in tokens for token in group)
    topic = _tokens(title)

    def score(index: int) -> float:
        sentence = sentences[index]
        group = tokens[index]
        value = sum(min(frequencies[t] - 1, 3) for t in group) / max(len(group), 1)
        value += min(len(group & topic), 4) * 0.5
        if _CONCLUSION.search(sentence):
            value += 10
        if re.search(r"关键|核心|原因|意味着|表明|发现|建议|需要|可以|能够|因为", sentence):
            value += 3
        if re.search(r"\d+(?:\.\d+)?\s*(?:%|倍|秒|分钟|小时|天|元)", sentence):
            value += 1
        if _BACKGROUND.search(sentence):
            value -= 5
        if _DEPENDENT.search(sentence) or (_CAVEAT.search(sentence) and not _LEAD.search(sentence)):
            value -= 4
        if len(sentence) > max(100, budget * 1.5):
            value -= 12
        return value

    ranked = sorted(candidates, key=lambda i: (-score(i), i))

    def context_group(index: int) -> list[int]:
        # Do not detach "this/it/for example" from the sentence it refers to,
        # or detach a claim from its following explicit limitation/correction.
        start = index
        while start > 0 and (
            _DEPENDENT.search(sentences[start])
            or (_CAVEAT.search(sentences[start]) and not _LEAD.search(sentences[start]))
        ):
            start -= 1
        end = index + 1
        while (
            end < len(sentences)
            and _CAVEAT.search(sentences[end])
            and not _LEAD.search(sentences[end])
        ):
            end += 1
        return list(range(start, end))

    lead = context_group(ranked[0])
    if sum(len(sentences[i]) for i in lead) > max(320, budget * 2):
        raise ValueError(
            "本地规则无法把这段素材压缩为清晰要点，请配置模型进行概括，或整理后重新导入。"
        )
    chosen = set(lead)
    used = sum(len(sentences[i]) for i in chosen)
    for index in ranked[1:]:
        if len(chosen) >= 3:
            break
        if index in chosen or _BACKGROUND.search(sentences[index]):
            continue
        if any(
            len(tokens[index] & tokens[j]) / max(len(tokens[index] | tokens[j]), 1) > 0.65
            for j in chosen
        ):
            continue
        group = set(context_group(index)) - chosen
        extra = sum(len(sentences[i]) for i in group)
        if used + extra <= max(40, budget) and len(chosen | group) <= 3:
            chosen.update(group)
            used += extra
    ordered = lead + sorted(chosen - set(lead))
    pieces = []
    for index in ordered:
        sentence = _LEAD.sub("", sentences[index]).strip()
        # Only remove a redundant first-person stance marker, never a negation.
        sentence = re.sub(r"^(?:我认为|在我看来)[，,]?\s*", "", sentence)
        if sentence and sentence[-1] not in "。！？!?….”’」』":
            sentence += "。"
        if sentence:
            pieces.append(sentence)
    if not pieces:
        raise ValueError("未提取到可用要点，请补充正文或配置模型进行概括。")
    return "".join(pieces)
