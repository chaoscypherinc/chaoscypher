# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Judge LLM prompt templates for GraphRAGChatScorer.

Bumped together with CHAT_SCORER_VERSION whenever the rubric changes.
"""

from __future__ import annotations


FAITHFULNESS_PROMPT = """You are a strict evaluator. Score how FAITHFUL the
answer is to the retrieved context. Faithful means every factual claim in
the answer is supported by the retrieved context — no hallucination, no
information beyond what was retrieved.

Retrieved context:
{retrieved}

Answer:
{answer}

Score 1-5 where:
1 = Many claims unsupported or contradicted by context
2 = Some claims unsupported
3 = Mixed; key claims supported but some drift
4 = Mostly faithful; minor unsupported flourishes
5 = Fully faithful; every claim supported

Reply with a single integer 1-5 and nothing else."""


CORRECTNESS_PROMPT = """You are a strict evaluator. Score how CORRECT the
answer is compared to the gold reference answer. Score the substance, not
the wording.

Question:
{question}

Gold reference answer:
{gold_answer}

Answer to evaluate:
{answer}

Score 1-5 where:
1 = Wrong or contradicts gold
2 = Mostly wrong, some overlap
3 = Partially correct; key facts missing or inaccurate
4 = Mostly correct; minor omissions
5 = Fully correct; matches the gold answer's substance

Reply with a single integer 1-5 and nothing else."""


REFUSAL_PROMPT = """You are a strict evaluator. The question below was
designed to be UNANSWERABLE from the retrieved context. A correct response
either refuses to answer, says it doesn't know, or explicitly says the
information is not in the context. A wrong response confidently
hallucinates an answer.

Retrieved context:
{retrieved}

Question:
{question}

Answer to evaluate:
{answer}

Did the answer correctly refuse / say it doesn't know? Reply with just
'yes' or 'no'."""


__all__ = ["CORRECTNESS_PROMPT", "FAITHFULNESS_PROMPT", "REFUSAL_PROMPT"]
