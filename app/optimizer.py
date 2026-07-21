from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass

import litellm

from app.config import get_settings
from app.executor.base import TaskExecution

logger = logging.getLogger(__name__)


@dataclass
class OptimizationProposal:
    agent_content: str
    rationale: str
    learnings: str
    prompt: str
    raw_response: str
    failure_context: list[dict]
    source_agent_version_no: int | None = None


class Optimizer:
    def effective_mode(self) -> str:
        settings = get_settings()
        mode = settings.optimizer_mode
        if mode == "auto":
            return "llm" if settings.openai_api_key else "heuristic"
        return mode

    async def propose(
        self,
        *,
        current_agent: str,
        failing_tasks: list[TaskExecution],
        accumulated_learnings: str,
        iteration_no: int,
        val_score: float,
        source_agent_version_no: int | None = None,
    ) -> OptimizationProposal:
        settings = get_settings()
        mode = self.effective_mode()
        logger.info(
            "[optimizer] iteration %s: mode=%s (configured=%s, openai_key=%s)",
            iteration_no,
            mode,
            settings.optimizer_mode,
            "set" if settings.openai_api_key else "missing",
        )

        if mode == "llm":
            try:
                return await self._propose_llm(
                    current_agent=current_agent,
                    failing_tasks=failing_tasks,
                    accumulated_learnings=accumulated_learnings,
                    iteration_no=iteration_no,
                    val_score=val_score,
                    model=settings.optimizer_model,
                    source_agent_version_no=source_agent_version_no,
                )
            except Exception:
                logger.warning(
                    "[optimizer] iteration %s: LLM proposal failed, falling back to heuristic",
                    iteration_no,
                    exc_info=True,
                )
        return self._propose_heuristic(
            current_agent=current_agent,
            failing_tasks=failing_tasks,
            accumulated_learnings=accumulated_learnings,
            iteration_no=iteration_no,
            val_score=val_score,
            source_agent_version_no=source_agent_version_no,
        )

    async def _propose_llm(
        self,
        *,
        current_agent: str,
        failing_tasks: list[TaskExecution],
        accumulated_learnings: str,
        iteration_no: int,
        val_score: float,
        model: str,
        source_agent_version_no: int | None = None,
    ) -> OptimizationProposal:
        failure_context = build_failure_context(failing_tasks)
        prompt = build_optimizer_prompt(
            current_agent=current_agent,
            failure_context=failure_context,
            accumulated_learnings=accumulated_learnings,
            iteration_no=iteration_no,
            val_score=val_score,
            mode="llm",
        )

        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        agent_content = data.get("agent_content", current_agent)
        _validate_agent(agent_content)
        return OptimizationProposal(
            agent_content=agent_content,
            rationale=data.get("rationale", ""),
            learnings=data.get("learnings", ""),
            prompt=prompt,
            raw_response=raw,
            failure_context=failure_context,
            source_agent_version_no=source_agent_version_no,
        )

    def _propose_heuristic(
        self,
        *,
        current_agent: str,
        failing_tasks: list[TaskExecution],
        accumulated_learnings: str,
        iteration_no: int,
        val_score: float,
        source_agent_version_no: int | None = None,
    ) -> OptimizationProposal:
        failure_context = build_failure_context(failing_tasks)
        prompt = build_optimizer_prompt(
            current_agent=current_agent,
            failure_context=failure_context,
            accumulated_learnings=accumulated_learnings,
            iteration_no=iteration_no,
            val_score=val_score,
            mode="heuristic",
        )
        new_agent = current_agent
        rationale_parts: list[str] = []

        if "TODO" not in current_agent and "plan" not in current_agent.lower():
            new_agent = _inject_after_instruction(
                new_agent,
                "\n- Maintain a TODO plan and update it as you progress.\n",
            )
            rationale_parts.append("Added enforced TODO planning to the system prompt.")

        if "non-interactive" not in current_agent.lower():
            new_agent = _inject_after_instruction(
                new_agent,
                "\n- Work in non-interactive mode; do not ask the user questions.\n",
            )
            rationale_parts.append("Added non-interactive mode guidance.")

        if "verify your solution before finishing" not in current_agent.lower():
            new_agent = _inject_after_instruction(
                new_agent,
                "\n- Always verify your solution with a concrete check before finishing.\n",
            )
            rationale_parts.append("Strengthened verification guidance.")

        learnings = (
            f"## Iteration {iteration_no} — val_score {val_score:.3f}\n\n"
            f"**Failures:** {len(failing_tasks)} task(s)\n\n"
            f"**Changes:** {'; '.join(rationale_parts) or 'No heuristic change applied.'}\n"
        )
        if accumulated_learnings:
            learnings = accumulated_learnings.rstrip() + "\n\n" + learnings

        _validate_agent(new_agent)
        return OptimizationProposal(
            agent_content=new_agent,
            rationale="; ".join(rationale_parts) or "Heuristic optimizer made no changes.",
            learnings=learnings,
            prompt=prompt,
            raw_response=json.dumps(
                {
                    "mode": "heuristic",
                    "rationale": rationale_parts,
                    "changed": new_agent != current_agent,
                }
            ),
            failure_context=failure_context,
            source_agent_version_no=source_agent_version_no,
        )


def _inject_after_instruction(agent: str, snippet: str) -> str:
    marker = "When you are done, send a final text message"
    if marker in agent:
        return agent.replace(marker, snippet + marker, 1)
    if '"""' in agent:
        parts = agent.split('"""', 2)
        if len(parts) >= 3:
            return parts[0] + '"""' + parts[1] + snippet + '"""' + parts[2]
    return agent + "\n" + snippet


def build_failure_context(failing_tasks: list[TaskExecution], *, limit: int = 8) -> list[dict]:
    context: list[dict] = []
    for task in failing_tasks[:limit]:
        context.append(
            {
                "task_id": task.task_id,
                "status": task.status,
                "failure_summary": task.failure_summary,
                "trace_excerpt": trace_excerpt(task.trace),
            }
        )
    return context


def build_optimizer_prompt(
    *,
    current_agent: str,
    failure_context: list[dict],
    accumulated_learnings: str,
    iteration_no: int,
    val_score: float,
    mode: str,
) -> str:
    mode_note = (
        "Respond with JSON only:\n"
        "{\n"
        '  "rationale": "why this change should help",\n'
        '  "learnings": "markdown bullet list of key learnings from this iteration",\n'
        '  "agent_content": "the full new agent/agent.py file as a string"\n'
        "}\n\n"
        "Rules:\n"
        "- Return the COMPLETE agent.py file, not a patch.\n"
        "- Focus on AGENT_INSTRUCTION, TOOLS schema, and run-loop behavior.\n"
        "- Make one focused improvement per iteration.\n"
        "- Do not change MODEL or infrastructure settings."
        if mode == "llm"
        else (
            "(heuristic mode — the service applies fixed prompt edits instead of "
            "calling an LLM; this prompt is stored for debugging what the optimizer saw.)"
        )
    )
    return f"""You are optimizing a Terminal-Bench agent by editing agent/agent.py.

Optimizer mode: {mode}
Current val_score: {val_score:.3f}
Iteration: {iteration_no}

Accumulated learnings:
{accumulated_learnings or "(none yet)"}

Failing tasks (with trace excerpts the optimizer sees):
{json.dumps(failure_context, indent=2)}

Current agent/agent.py:
```python
{current_agent}
```

{mode_note}
"""


def trace_excerpt(trace: dict | list | None, limit: int = 1200) -> str:
    if trace is None:
        return ""
    text = json.dumps(trace, default=str)
    return text[:limit]


def _validate_agent(content: str) -> None:
    ast.parse(content)
    if "class HarnessAgent" not in content:
        raise ValueError("agent_content must define HarnessAgent")
