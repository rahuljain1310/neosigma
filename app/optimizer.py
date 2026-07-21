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
    ) -> OptimizationProposal:
        failure_context = []
        for t in failing_tasks[:8]:
            failure_context.append(
                {
                    "task_id": t.task_id,
                    "status": t.status,
                    "failure_summary": t.failure_summary,
                    "trace_excerpt": _trace_excerpt(t.trace),
                }
            )

        prompt = f"""You are optimizing a Terminal-Bench agent by editing agent/agent.py.

Current val_score: {val_score:.3f}
Iteration: {iteration_no}

Accumulated learnings:
{accumulated_learnings or "(none yet)"}

Failing tasks:
{json.dumps(failure_context, indent=2)}

Current agent/agent.py:
```python
{current_agent}
```

Respond with JSON only:
{{
  "rationale": "why this change should help",
  "learnings": "markdown bullet list of key learnings from this iteration",
  "agent_content": "the full new agent/agent.py file as a string"
}}

Rules:
- Return the COMPLETE agent.py file, not a patch.
- Focus on AGENT_INSTRUCTION, TOOLS schema, and run-loop behavior.
- Make one focused improvement per iteration.
- Do not change MODEL or infrastructure settings.
"""

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
        )

    def _propose_heuristic(
        self,
        *,
        current_agent: str,
        failing_tasks: list[TaskExecution],
        accumulated_learnings: str,
        iteration_no: int,
        val_score: float,
    ) -> OptimizationProposal:
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
            prompt="(heuristic optimizer — no LLM prompt)",
            raw_response=json.dumps({"rationale": rationale_parts}),
        )


def _inject_after_instruction(agent: str, snippet: str) -> str:
    marker = 'When you are done, send a final text message'
    if marker in agent:
        return agent.replace(marker, snippet + marker, 1)
    if '"""' in agent:
        parts = agent.split('"""', 2)
        if len(parts) >= 3:
            return parts[0] + '"""' + parts[1] + snippet + '"""' + parts[2]
    return agent + "\n" + snippet


def _trace_excerpt(trace: dict | list | None, limit: int = 1200) -> str:
    if trace is None:
        return ""
    text = json.dumps(trace, default=str)
    return text[:limit]


def _validate_agent(content: str) -> None:
    ast.parse(content)
    if "class HarnessAgent" not in content:
        raise ValueError("agent_content must define HarnessAgent")
