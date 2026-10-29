# Reproducibility Assessment

You are evaluating whether a reader could reproduce this paper's results. Write for a technical reader who wants a straight answer fast — an engineer deciding whether to trust or reuse this work.

## Paper

- Title: {title}
- Authors: {authors}
- URL: {url}
- Published: {published}

## Abstract

{abstract}

## Code repositories found

{code_links}

## Instructions

Write a short assessment (150-300 words) in Markdown with exactly these sections:

## Verdict
One line: **Reproducible**, **Partially reproducible**, **Hard to reproduce**, or **Not reproducible from the paper alone** — then one sentence of justification.

## What exists
What a reproducer can start from: official or community code, released weights, datasets, eval harnesses. Link repos inline when listed above. If nothing was found but you know of artifacts from your own knowledge, say "I believe" or "likely" — never assert a link you weren't given or don't genuinely know.

## What's missing
The gaps that block reproduction: unpublished code, private data, underspecified hyperparameters, compute requirements, closed weights. If the abstract doesn't say, say so honestly.

## Effort estimate
A plain-language estimate (e.g. "a weekend with a GPU" or "weeks + significant compute") and the single best first step to attempt reproduction.

## Rules

- Be honest over flattering — most papers are partially reproducible at best.
- Never invent repository URLs, DOIs, or artifact names.
- No greeting, sign-off, or meta-commentary. Start with the Verdict heading.
