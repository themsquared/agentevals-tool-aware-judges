"""Show what changed in agentevals v0.12.0's tool-use judge prompt.

v0.11.0 and earlier: the rubric_based_tool_use_quality_v1 judge only saw the
invocation it was scoring: the user turn, the tool call that was actually
made, and the final response. It had no way to know a better tool existed
and went unused.

v0.12.0 (PR #264): the judge prompt now carries an <available_tools> block
with every tool the agent could have called, names, descriptions and
parameter schemas included, sourced from the trace's own
gen_ai.tool.definitions.

This script drives the real agentevals code (agentevals.loader,
agentevals.genai.extract, agentevals.adk_bridge, agentevals.builtin_metrics)
against the demo trace in traces/pricing-coupon.json, with a scripted
stand-in for the LLM judge call instead of a real model, so this reproduces
with no API key. The stand-in follows the same pattern as agentevals' own
test suite: tests/test_builtin_metrics.py::test_replayed_judge_sees_all_available_tools.

What it is NOT: a real LLM's judgment. The "after" verdict below is a
deterministic rule (does the prompt mention a tool documented for "the
amount actually charged" that was not called), not a model's reasoning. It
exists to show that the information needed to catch the bug reaches the
judge in v0.12.0 and did not in v0.11.0, which is a structural, inspectable
fact independent of which model you point at it.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from agentevals import builtin_metrics
from agentevals.adk_bridge import to_adk_invocations
from agentevals.genai.extract import extract_conversation
from agentevals.loader import load_traces

TRACE_PATH = "traces/pricing-coupon.json"
RUBRIC = "When an available tool is documented as computing the exact amount actually charged to a customer, the agent uses that tool rather than a tool documented as an estimate or preview, for any number that will appear on an invoice or charge."


class RecordingJudge(BaseLlm):
    """Captures the exact prompt agentevals sends to the judge model."""

    prompts: list[str] = []
    verdict: str = "yes"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        prompt = llm_request.contents[0].parts[0].text
        self.prompts.append(prompt)
        text = f"ID: rubric_0\nProperty: exact tool for charged amounts\nRationale: scripted\nVerdict: {self.verdict}"
        yield LlmResponse(content=genai_types.Content(role="model", parts=[genai_types.Part(text=text)]))


def strip_available_tools(prompt: str) -> str:
    """Simulate the pre-v0.12.0 prompt by removing the <available_tools> block
    this version of agentevals adds. Confirms the block is additive, not a
    rewrite of surrounding instructions."""
    return re.sub(r"<available_tools>.*?</available_tools>\n?", "", prompt, flags=re.DOTALL)


def score_tool_choice(prompt: str) -> tuple[str, str]:
    """A scripted stand-in for the judge's reasoning, not a real model call.
    Flags the call only when the prompt itself names a tool documented for
    'the amount actually charged' that the trace did not call."""
    if "<available_tools>" not in prompt:
        return "yes", "Saw only the call to estimate_discount and a dollar figure in the response; no way to know another tool existed."
    tools_block = prompt.rsplit("<available_tools>", 1)[1].split("</available_tools>")[0]
    if "amount actually charged" in tools_block and "apply_exact_discount" in tools_block:
        return "no", "apply_exact_discount is declared and documented for the amount actually charged; the agent called estimate_discount instead, which is documented for marketing previews only."
    return "yes", "No stricter tool documented for charged amounts appears in the available tools."


async def main() -> None:
    (turn,) = extract_conversation(load_traces(TRACE_PATH)).turns
    invocations = to_adk_invocations([turn])
    definitions = turn.llm_calls[0].tool_definitions
    print(f"Tools the trace declares as available: {[d['name'] for d in definitions]}")
    print(f"Tool the agent actually called: {[c.name for c in turn.tool_calls]}\n")

    metric = builtin_metrics.build_eval_metric(
        "rubric_based_tool_use_quality_v1", None, 0.5, rubrics=[RUBRIC]
    )
    evaluator = builtin_metrics.get_evaluator(metric)
    judge = RecordingJudge(model="recording-judge")
    evaluator._judge_model = judge
    await evaluator.evaluate_invocations(actual_invocations=invocations)

    v012_prompt = judge.prompts[0]
    v011_prompt = strip_available_tools(v012_prompt)

    print("=== v0.11.0-style prompt (no available_tools) ===")
    verdict, rationale = score_tool_choice(v011_prompt)
    print(f"Verdict: {verdict}\nRationale: {rationale}\n")

    print("=== v0.12.0 prompt (actual agentevals output, unmodified) ===")
    available = json.loads(v012_prompt.rsplit("<available_tools>\n", 1)[1].split("</available_tools>")[0])
    tool_names = [
        fn["name"]
        for agent_tools in available["tool_declarations"].values()
        for tool in agent_tools
        for fn in tool["function_declarations"]
    ]
    print(f"Judge now sees {len(tool_names)} declared tools: {tool_names}")
    verdict, rationale = score_tool_choice(v012_prompt)
    print(f"Verdict: {verdict}\nRationale: {rationale}")


if __name__ == "__main__":
    asyncio.run(main())
