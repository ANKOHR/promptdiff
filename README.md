# PromptDiff

PromptDiff treats prompts like code: versioned, diffed, tested, and released only when the numbers say so.

It is a local prompt version store and release gate. The CLI saves immutable prompt records, computes deterministic diffs, runs static checks against fictional fixtures, tracks a token-count heuristic and declared latency estimates, and records promotion decisions in an append-only audit log.

## Quickstart

Install the CLI from this repository:

```powershell
python -m pip install -e .
```

Create a repository and save a first version:

```powershell
mkdir my-prompts
cd my-prompts
promptdiff init
promptdiff version --name v1
```

Edit `prompt.md`, including its YAML frontmatter and prompt body, then save and compare another version:

```powershell
promptdiff version --name v2
promptdiff history
promptdiff diff v1 v2 --format md
promptdiff eval v2 --against v1
promptdiff gate v2 --against v1
promptdiff report --format md
```

To generate a complete fictional demo repository with a passing baseline, a changed version with one planted regression, a cost-bloated version, and a flagged ambiguous request:

```powershell
promptdiff fixtures --out promptdiff-demo
cd promptdiff-demo
promptdiff eval baseline
promptdiff eval improved --against baseline
promptdiff gate baseline
promptdiff gate improved
promptdiff gate cost-bloated
promptdiff report --format md
```

The generated baseline gate promotes. The improved version is blocked by its planted regression. The cost-bloated version is blocked by the token budget. All fixture text and prompt examples are fictional.

## Prompt records and versions

`prompt.md` is the editable draft. A record has YAML frontmatter and a Markdown body:

```markdown
---
name: support-reply
version: draft
author: Example author
created: 2026-09-29
tags:
  - support
latency:
  base_ms: 120
  ms_per_100_tokens: 6
---
# Instructions
Answer directly and accurately.
```

The required fields are `name`, `version`, `author`, `created`, and `tags`. `created` is an ISO date. The optional `latency` profile declares `base_ms` and `ms_per_100_tokens`. `promptdiff version --name v1` stores a copy at `.promptdiff/versions/v1.md`. A version name can contain letters, digits, dots, underscores, and hyphens. A saved version cannot be replaced. `promptdiff history` lists stored versions in creation order with their metadata.

## Architecture

```text
prompt.md draft
    |
    | promptdiff version --name v1
    v
.promptdiff/versions/*.md ----> promptdiff diff ----> JSON or Markdown line and section diff
          |                                  |
          |                                  +--> lexical token-count delta
          v
.promptdiff/fixtures.yaml --> promptdiff eval --> .promptdiff/results/eval/*.json
                                                   | fixture evidence and token ledger
                                                   v
                                      promptdiff gate --against baseline
                                        | measured thresholds
                                        +--> .promptdiff/results/gate/*.json
                                        +--> pd-audit.jsonl
                                                   |
                                                   v
                                        promptdiff report --format md|json
```

The implementation uses Python and PyYAML only. It uses no accelerator packages, does not make model calls, and fixture generation is local with no downloads. Evaluation and diffing are deterministic code paths with no random seed because no random numbers are used.

## Diff behavior

`promptdiff diff <from> <to>` emits deterministic JSON by default. Use `--format md` for a readable summary and unified line diff. The section summary treats Markdown headings as section names and reports added, removed, and changed sections. Duplicate heading names receive a stable numeric suffix. Token deltas use the same lexical counting rule as the ledger.

## Evaluation and evidence

`promptdiff eval <version>` checks the selected record against the configured fixture file. `--against <version>` adds fixture-by-fixture comparison evidence, with regressions and improvements listed separately. Every result contains a flakiness check that runs the same deterministic checks twice and compares canonical JSON bytes.

Rules include literal contains and not-contains checks, alternatives, regular expressions, required Markdown sections, exact text checks, declared example-output checks, and ambiguity flags. Each fixture result carries its rule, pass/warn/fail status, measured values, and whether a failure blocks promotion. An `expected_output` fixture checks a literal example declared in the prompt record. It does not generate or execute a model response.

Each eval and gate action appends a JSON object to `pd-audit.jsonl`. Result artifacts are stored under `.promptdiff/results/`. A gate record includes its thresholds, measured inputs, evidence paths, decision, and stable audit event ID. Reports sort their evidence by artifact filename and omit audit timestamps, so repeated report rendering produces identical bytes when the stored results have not changed.

## Cost and latency ledger

The declared token-count rule matches Unicode word runs and individual non-whitespace punctuation characters with the regular expression `\w+|[^\w\s]`. A per-fixture run counts the full prompt body plus that fixture's `input` text. The ledger reports prompt tokens, fixture-input tokens, total tokens across the fixture set, average and maximum tokens per run. These counts are deterministic heuristics, not tokenizer output and not a billing measurement.

Latency uses the version's declared profile:

```text
estimated average milliseconds = base_ms + average tokens per run / 100 * ms_per_100_tokens
```

This is an estimate only. No live service is called and no latency is measured.

## HONESTY BOUNDARY

No language models are called anywhere in this system, and none are needed to use it. Evaluation judgments are deterministic code checks against synthetic fixtures. The fixture checks inspect prompt text or fixture-defined strings; they do not invoke a prompt against a model and do not make model-quality claims. The token count is a documented heuristic, not a real billing measurement. Latency is estimated from a profile declared in each prompt version, not measured. CLI output and generated reports repeat this boundary. Fixture examples are fictional and generated locally.

## Gate catalogue

The gate catalogue lives in `.promptdiff/config.yaml` under `gates`. The generated demo uses these thresholds:

### `max_regressions`

Threshold: `max: 0`. A fixture is a regression if its status declines in the order fail, warn, pass, its measured `matched_count` decreases, or it is missing from the candidate. The baseline comes from the `baseline` config field or `promptdiff gate <version> --against <baseline>`. Without a baseline, this gate fails because a regression count cannot be measured.

### `max_cost_per_run`

Threshold: `max_tokens: 200` heuristic tokens per fixture run in the generated demo. The measured value is the average of prompt-body tokens plus fixture-input tokens over the configured fixture set. It is a heuristic budget, not currency or provider billing.

### `all_fixtures_passing`

Threshold: `max_failures: 0`. The measured value is the number of blocking fixture results with status `fail`. A rule configured with warning severity can report `warn` without blocking. The ambiguity fixture passes its static detection rule and records `finding: warn` in measured evidence, making the ambiguous request visible without treating successful detection as a failed fixture.

Promotion occurs only when every configured gate passes. Edit `.promptdiff/config.yaml` to change a threshold or baseline. Gate inputs and decision are recorded in the audit log.

## CLI reference

```text
promptdiff init [--root DIR]
promptdiff version --name NAME [--root DIR]
promptdiff history [--root DIR]
promptdiff diff FROM TO [--format json|md] [--root DIR]
promptdiff eval VERSION [--against BASELINE] [--format json|md] [--root DIR]
promptdiff gate VERSION [--against BASELINE] [--format json|md] [--root DIR]
promptdiff report [--format md|json] [--out FILE] [--root DIR]
promptdiff fixtures --out DIR
```

All outputs that contain evaluation or promotion results carry the honesty banner. `promptdiff report` can be run repeatedly and produces deterministic JSON or Markdown bytes for unchanged stored evidence.
