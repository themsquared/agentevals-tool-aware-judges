# agentevals tool-aware judges

> 📖 **Read the write-up:** _link added once the post is live_

[agentevals](https://github.com/agentevals-dev/agentevals) v0.12.0 (released 2026-11-04) changed what its LLM-judge metrics see when they grade a tool call. Before this release, `rubric_based_tool_use_quality_v1` and `rubric_based_final_response_quality_v1` only saw the invocation being scored: the user turn, the tool call the agent actually made, and the final response. The judge had no way to know a better tool existed and went unused, because nothing in the prompt told it one did.

v0.12.0 ships an `<available_tools>` block in the judge prompt listing every tool the agent could have called, with full descriptions and parameter schemas, sourced from the trace's own `gen_ai.tool.definitions`. This repo is a minimal, runnable demonstration of that change, and of the verdict it can flip.

## The problem it demonstrates

A pricing agent has two tools available:

- `estimate_discount` - documented as rounding the cart total to the nearest dollar before applying a percentage, "for marketing previews only, not the amount charged."
- `apply_exact_discount` - documented as computing the exact discounted total in cents, "use this for the amount actually charged."

Asked "a customer's cart total is $19.99 and they have a 15% loyalty coupon, what do we charge them," the agent in [`traces/pricing-coupon.json`](traces/pricing-coupon.json) calls `estimate_discount` and answers "$17.00." The correct charge is $16.99 (19.99 x 0.85 = 16.9915). The agent picked the wrong tool for a number that was about to go on an invoice, and the tool it should have used says so in its own description.

A judge that only sees the call to `estimate_discount` and a dollar figure in the response has no basis to object: the tool ran, it returned a number, the agent reported that number. A judge that also sees `apply_exact_discount` declared as the tool for charged amounts has what it needs to flag the call.

## What's in this repo

- `traces/pricing-coupon.json` - a single-turn OTLP trace in agentevals' native format: one `generate_content` span declaring both tools and calling `estimate_discount`, an `execute_tool` span for that call, a second `generate_content` span with the final answer, and the ADK `invoke_agent`/`invoke_workflow` wrapper spans.
- `eval_set.json` - an ADK `EvalSet` with two golden signals for the same case: an `intermediate_data.tool_uses` entry that pins down the exact call `tool_trajectory_avg_score` expects, and a `TOOL_USE_QUALITY` rubric that states the real requirement in words.
- `scripts/judge_visibility_demo.py` - drives agentevals' own trace-loading and evaluator code against the trace above, captures the literal prompt it builds for the `rubric_based_tool_use_quality_v1` judge, and scores it twice: once with the `<available_tools>` block agentevals v0.12.0 adds, once with it stripped out to approximate the pre-v0.12.0 prompt.

## Why the deterministic metric doesn't catch this

`tool_trajectory_avg_score` only compares the trace's tool calls against a literal golden list. If that golden list was recorded from an earlier run of the same agent (a common way these eval sets get written: capture a session, mark it golden), it captures `estimate_discount` as the expected call and the bug becomes the specification.

```bash
agentevals run traces/pricing-coupon.json --eval-set eval_set.json -m tool_trajectory_avg_score
```

```
Trace: a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6
Invocations: 1
        Metric                       Score  Status      Per-Invocation  Time
------  -------------------------  -------  --------  ----------------  ------
[PASS]  tool_trajectory_avg_score        1  PASSED                   1  6ms
```

Verified: this ran against the trace in this repo with `agentevals-cli==0.12.0`.

## The new efficiency metrics (also v0.12.0)

The same release adds four `INFORMATIONAL` metrics that report a value with no pass/fail threshold: `tool_call_count_v1`, `inference_call_count_v1`, `token_usage_v1`, `invocation_duration_v1`.

```bash
agentevals run traces/pricing-coupon.json \
  -m tool_call_count_v1 -m inference_call_count_v1 -m token_usage_v1 -m invocation_duration_v1
```

```
        Metric                     Score  Status           Per-Invocation
------  -----------------------  -------  -------------  ----------------
[INFO]  tool_call_count_v1          1     INFORMATIONAL              1
[INFO]  token_usage_v1            430     INFORMATIONAL            430
[INFO]  inference_call_count_v1     2     INFORMATIONAL              2
[INFO]  invocation_duration_v1      0.12  INFORMATIONAL              0.12
```

Verified: this ran against the trace in this repo with `agentevals-cli==0.12.0`.

## Reproducing the judge prompt difference

Rubric-based metrics need an LLM judge, which normally means an API key for whatever model you point at it (default `gemini-2.5-flash`). This repo avoids that by using a scripted stand-in for the judge call, following the exact pattern agentevals' own test suite uses for this (`tests/test_builtin_metrics.py::test_replayed_judge_sees_all_available_tools` in the [agentevals repo](https://github.com/agentevals-dev/agentevals)): a fake `BaseLlm` that records the prompt it receives instead of calling a real model.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/judge_visibility_demo.py
```

```
Tools the trace declares as available: ['estimate_discount', 'apply_exact_discount']
Tool the agent actually called: ['estimate_discount']

=== v0.11.0-style prompt (no available_tools) ===
Verdict: yes
Rationale: Saw only the call to estimate_discount and a dollar figure in the response; no way to know another tool existed.

=== v0.12.0 prompt (actual agentevals output, unmodified) ===
Judge now sees 2 declared tools: ['estimate_discount', 'apply_exact_discount']
Verdict: no
Rationale: apply_exact_discount is declared and documented for the amount actually charged; the agent called estimate_discount instead, which is documented for marketing previews only.
```

Verified: this is the real output of the script above, run with `agentevals-cli==0.12.0` and `google-adk==2.13.0` in a clean virtualenv.

The "verdict" in this script is a scripted rule, not a real model's judgment; it exists to show the information a judge needs is now structurally present in the prompt, independent of which model you use. I also confirmed the inverse directly: running the same extraction and evaluator code under `agentevals-cli==0.11.0` against the identical trace produces a prompt with no `<available_tools>` block at all (the script's JSON parse of that block raises `JSONDecodeError`, because the string isn't there to parse). The block is new in v0.12.0, not something this repo invented.

## What this isn't

This repo does not call a real LLM judge, run a live agent, or stand up the agentevals server. It is a recorded trace plus the minimum code needed to show a structural change in a released library, so it runs the same way for anyone, with no credentials.

## Links

- [agentevals](https://github.com/agentevals-dev/agentevals) - the project this repo demonstrates
- [agentevals v0.12.0 release notes](https://github.com/agentevals-dev/agentevals/releases/tag/v0.12.0)
- [agentevals eval set format docs](https://github.com/agentevals-dev/agentevals/blob/main/docs/eval-set-format.md)

## Topics

`agentevals` `ai-agents` `llm-evaluation` `opentelemetry` `mcp`

## License

Apache-2.0, see [LICENSE](LICENSE).
