"""
agents/base.py
──────────────
Base agentic loop using the Google Gemini API (function calling).

Same interface as before — subclass and implement `_execute_tool(name, input)`.
The loop runs until Gemini stops calling functions and returns a text response.

Schema conversion
──────────────────
Our tool definitions use Anthropic-style format (input_schema key).
This base class converts them transparently to Gemini's format (parameters key)
and cleans up unsupported JSON Schema constructs (oneOf, anyOf, null types).
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from google import genai
from google.genai import types

from run_context import get_run_id, set_run_id

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Raised when a tool execution fails."""


# ── Schema cleaner ────────────────────────────────────────────────────────────

def _clean_schema(schema: Any) -> Any:
    """
    Recursively clean a JSON Schema dict for Gemini compatibility.

    Changes made:
    - Replaces oneOf / anyOf with the first non-null type
    - Removes null-only types
    - Drops unknown / unsupported keys
    - Keeps: type, description, properties, required, items, enum
    """
    if not isinstance(schema, dict):
        return schema

    # Handle oneOf / anyOf — pick the first non-null option
    for key in ("oneOf", "anyOf"):
        if key in schema:
            options = schema[key]
            chosen = next(
                (o for o in options if isinstance(o, dict) and o.get("type") != "null"),
                options[0] if options else {"type": "string"},
            )
            # Merge remaining keys into chosen
            merged = {k: v for k, v in schema.items() if k not in ("oneOf", "anyOf")}
            merged.update(chosen)
            return _clean_schema(merged)

    ALLOWED = {"type", "description", "properties", "required", "items", "enum", "format"}
    cleaned: dict[str, Any] = {}

    for k, v in schema.items():
        if k not in ALLOWED:
            continue
        if k == "properties" and isinstance(v, dict):
            cleaned[k] = {pk: _clean_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            cleaned[k] = _clean_schema(v)
        else:
            cleaned[k] = v

    return cleaned


def _to_gemini_tools(anthropic_tools: list[dict]) -> list[types.Tool]:
    """
    Convert Anthropic-style tool definitions to Gemini FunctionDeclarations.

    Anthropic format: {"name": ..., "description": ..., "input_schema": {...}}
    Gemini format:    {"name": ..., "description": ..., "parameters": {...}}
    """
    declarations = []
    for tool in anthropic_tools:
        schema = tool.get("input_schema", {})
        cleaned = _clean_schema(schema)

        declarations.append(
            types.FunctionDeclaration(
                name        = tool["name"],
                description = tool["description"],
                parameters  = cleaned,
            )
        )

    return [types.Tool(function_declarations=declarations)]


# ── Base agent ────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Runs a Gemini agent loop with function calling.

    Parameters
    ──────────
    model          : Gemini model string, e.g. "gemini-3.1-pro-preview"
    tools          : list of tool defs in Anthropic format (converted internally)
    system_prompt  : system instruction string
    max_iterations : safety cap — abort if loop runs longer than this
    max_tokens     : max output tokens per Gemini response
    """

    def __init__(
        self,
        model: str,
        tools: list[dict],
        system_prompt: str,
        max_iterations: int = 20,
        max_tokens: int = 8192,
    ) -> None:
        self.model          = model
        self.tools          = tools
        self.system_prompt  = system_prompt
        self.max_iterations = max_iterations
        self.max_tokens     = max_tokens
        self._client        = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self._gemini_tools  = _to_gemini_tools(tools)

    # ── public API ───────────────────────────────────────────────────────────

    def run(self, user_message: str) -> dict[str, Any]:
        """
        Run the agent loop.

        Returns:
            {
              "text":          str,
              "tool_results":  list,
              "emit":          Any,   # value from the emit_* tool if called
              "iterations":    int,
            }
        """
        contents: list[types.Content] = [
            types.Content(
                role  = "user",
                parts = [types.Part(text=user_message)],
            )
        ]

        iterations       = 0
        all_tool_results: list[dict] = []
        emit_value: Any  = None

        while iterations < self.max_iterations:
            iterations += 1
            log.debug("[%s] iteration %d", self.__class__.__name__, iterations)

            response = self._gemini_call(contents)
            candidate = response.candidates[0]

            # Guard: Gemini occasionally returns a candidate with no content
            # (e.g. finish_reason=MAX_TOKENS, SAFETY, RECITATION).
            # Treat it as a terminal response with empty text.
            if candidate.content is None or not candidate.content.parts:
                finish = getattr(candidate, "finish_reason", "UNKNOWN")
                log.warning(
                    "[%s] candidate.content is None/empty (finish_reason=%s) — "
                    "treating as terminal response",
                    self.__class__.__name__, finish,
                )
                return {
                    "text":         "",
                    "tool_results": all_tool_results,
                    "emit":         emit_value,
                    "iterations":   iterations,
                }

            # Add assistant turn to history
            contents.append(candidate.content)

            # Collect function calls from this response
            function_calls = [
                p for p in candidate.content.parts
                if p.function_call is not None
            ]

            if not function_calls:
                # No function calls → Gemini is done
                text = "".join(
                    p.text for p in candidate.content.parts
                    if hasattr(p, "text") and p.text
                )
                log.info("[%s] done in %d iterations", self.__class__.__name__, iterations)
                return {
                    "text":         text,
                    "tool_results": all_tool_results,
                    "emit":         emit_value,
                    "iterations":   iterations,
                }

            # ── execute function calls (in parallel if multiple) ──────────
            response_parts: list[types.Part] = []

            # Collect all function calls from this turn
            calls = [
                (p.function_call.name, dict(p.function_call.args) if p.function_call.args else {})
                for p in candidate.content.parts
                if p.function_call is not None
            ]

            log.info(
                "[%s] turn %d: %d tool call(s): %s",
                self.__class__.__name__, iterations,
                len(calls), [c[0] for c in calls],
            )

            # Capture run_id from parent thread so sub-threads can inherit it
            _parent_run_id = get_run_id()

            def _run_one(tool_name: str, tool_args: dict) -> tuple[str, dict, Any, Exception | None]:
                """Execute a single tool and return (name, args, result, error).
                Propagates the parent thread's run_id so RunLogHandler captures
                sub-thread logs in the correct run's log stream."""
                set_run_id(_parent_run_id)   # inherit from parent thread
                try:
                    result = self._execute_tool(tool_name, tool_args)
                    return tool_name, tool_args, result, None
                except ToolError as exc:
                    return tool_name, tool_args, None, exc

            # Run all calls for this turn concurrently
            if len(calls) <= 1:
                # No parallelism needed for single calls — avoid thread overhead
                outcomes = [_run_one(n, a) for n, a in calls]
            else:
                with ThreadPoolExecutor(max_workers=len(calls)) as pool:
                    futures = {pool.submit(_run_one, n, a): (n, a) for n, a in calls}
                    outcomes = [f.result() for f in as_completed(futures)]

            emit_called_this_turn = False

            for tool_name, tool_args, result, error in outcomes:
                if error is not None:
                    log.warning("[%s] tool error in %s: %s", self.__class__.__name__, tool_name, error)
                    response_parts.append(
                        types.Part.from_function_response(
                            name     = tool_name,
                            response = {"error": str(error)},
                        )
                    )
                    continue

                # Capture emit_* tool payloads — use tool_args (the model's
                # input), not result (the handler's return value / status)
                if tool_name.startswith("emit_"):
                    emit_value = tool_args
                    emit_called_this_turn = True

                all_tool_results.append({"tool": tool_name, "result": result})

                # Gemini requires function responses as dict
                fn_response = result if isinstance(result, dict) else {"value": str(result)}

                response_parts.append(
                    types.Part.from_function_response(
                        name     = tool_name,
                        response = fn_response,
                    )
                )

            # If an emit_ tool was called this turn, we have what we need.
            # Return immediately — don't ask Gemini for a follow-up response,
            # which can trigger MALFORMED_FUNCTION_CALL on large payloads.
            if emit_called_this_turn:
                log.info(
                    "[%s] emit called — returning after %d iterations",
                    self.__class__.__name__, iterations,
                )
                return {
                    "text":         "",
                    "tool_results": all_tool_results,
                    "emit":         emit_value,
                    "iterations":   iterations,
                }

            contents.append(
                types.Content(role="user", parts=response_parts)
            )

        log.warning("[%s] hit max_iterations=%d", self.__class__.__name__, self.max_iterations)
        return {
            "text":         "",
            "tool_results": all_tool_results,
            "emit":         emit_value,
            "iterations":   iterations,
        }

    # ── abstract ─────────────────────────────────────────────────────────────

    @abstractmethod
    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        """Execute a named tool and return a JSON-serialisable result."""

    # ── private helpers ──────────────────────────────────────────────────────

    # Seconds to wait for a single Gemini API call before treating it as hung.
    # Domain agents send large payloads (200 rows × multiple views) so they
    # need more time. 200s gives headroom without hanging if Gemini drops the connection.
    _CALL_TIMEOUT = 200

    def _gemini_call(self, contents: list[types.Content]) -> Any:
        """Call the Gemini API with retry on rate-limit / overload / timeout."""
        config = types.GenerateContentConfig(
            system_instruction = self.system_prompt,
            tools              = self._gemini_tools,
            max_output_tokens  = self.max_tokens,
            temperature        = 0.2,
        )

        for attempt in range(3):
            try:
                # Wrap in a thread so we can enforce a wall-clock timeout.
                # The background thread may keep running until the HTTP connection
                # closes, but the agent loop is unblocked and can retry.
                with ThreadPoolExecutor(max_workers=1) as _pool:
                    _future = _pool.submit(
                        self._client.models.generate_content,
                        model    = self.model,
                        contents = contents,
                        config   = config,
                    )
                    return _future.result(timeout=self._CALL_TIMEOUT)
            except TimeoutError:
                log.warning(
                    "[%s] Gemini call timed out after %ds (attempt %d/3)",
                    self.__class__.__name__, self._CALL_TIMEOUT, attempt + 1,
                )
                if attempt == 2:
                    raise RuntimeError(
                        f"Gemini API timed out after {self._CALL_TIMEOUT}s on all 3 attempts"
                    )
                time.sleep(5)
            except Exception as exc:
                err = str(exc).lower()
                if "rate" in err or "quota" in err or "429" in err or "503" in err:
                    wait = 2 ** attempt * 5
                    log.warning("Gemini rate limit / overload — waiting %ds (attempt %d/3)", wait, attempt + 1)
                    time.sleep(wait)
                else:
                    log.error("Gemini API error: %s", exc)
                    raise

        raise RuntimeError("Exhausted retries calling Gemini API")
