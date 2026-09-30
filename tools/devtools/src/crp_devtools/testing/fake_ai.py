"""Labelled fake OpenAI-compatible model endpoint for browser tests. Never use it for real work.

It follows a fixed procedure instead of reasoning: it reads what refactorX's own tools return and
cites that text back, so ``crp-dev test-e2e`` exercises the real pipeline (project policy,
disclosure, masking, citation checks, storage and UI) without a paid provider. Every answer it
gives says it comes from this test double. It listens on loopback only and requires the bearer key
it was started with.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from crp_devtools.infra import LOOPBACK

MODEL = "refactorx-e2e-fake"
LABEL = "[Test provider]"
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_HIT = re.compile(r"^(?P<path>.+?):(?P<line>\d+)(?: \[instruction file\])?: (?P<text>.*\S.*)$")
_SOURCE = re.compile(r'<source path="(?P<path>[^"]+)" lines="\d+-\d+"')
_LINE = re.compile(r"^\s*(?P<line>\d+) \| (?P<text>.*\S.*)$")
_LOCATION = re.compile(r"^Location: (?P<path>.+?):(?P<line>\d+)$", re.MULTILINE)


def _tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "arguments": arguments}


def _first_user_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return str(message["content"])
    return ""


def _source_lines(text: str) -> tuple[str | None, list[tuple[int, str]]]:
    match = _SOURCE.search(text)
    lines = [(int(m["line"]), m["text"].strip()) for m in map(_LINE.match, text.splitlines()) if m]
    return (match["path"] if match else None), lines


def _question(first: str, results: list[str]) -> dict[str, Any]:
    question = first.split("Question from the reviewer:\n", 1)[-1].split("\n\n", 1)[0]
    words = sorted(_WORD.findall(question), key=len, reverse=True)
    if not results:
        return _tool_call("search_code", {"query": words[0] if words else "main"})
    hits = [m for m in map(_HIT.match, results[-1].splitlines()) if m]
    if not hits:
        return _tool_call(
            "submit_answer",
            {
                "answer": f"{LABEL} No code matched the words of the question.",
                "abstained": True,
                "uncertainty": "The test provider only searches for the longest word.",
            },
        )
    hit = hits[0]
    return _tool_call(
        "submit_answer",
        {
            "answer": (
                f"{LABEL} The first code matching the question is in {hit['path']} at line "
                f"{hit['line']}. This answer is synthetic and only tests the pipeline."
            ),
            "citations": [
                {
                    "path": hit["path"],
                    "start_line": int(hit["line"]),
                    "end_line": int(hit["line"]),
                    "quote": hit["text"].strip(),
                }
            ],
        },
    )


def _finding_review(first: str) -> dict[str, Any]:
    location = _LOCATION.search(first)
    path, lines = _source_lines(first)
    anchors = []
    if location and path:
        wanted = int(location["line"])
        quoted = next((text for number, text in lines if number == wanted), None)
        if quoted:
            anchors.append(
                {"path": path, "start_line": wanted, "end_line": wanted, "quote": quoted}
            )
    return _tool_call(
        "submit_review",
        {
            "summary": f"{LABEL} Synthetic second opinion used to test the pipeline.",
            "finding_assessment": {
                "verdict": "uncertain",
                "explanation": f"{LABEL} This fake model does not judge code; it cites the "
                "flagged line so the citation check can be tested.",
                "anchors": anchors,
            },
        },
    )


def _file_review(first: str, results: list[str]) -> dict[str, Any]:
    files = first.split("Files:\n", 1)[-1].split("\n\n", 1)[0].splitlines()
    paths = [line[2:].strip() for line in files if line.startswith("- ")]
    if not results:
        return _tool_call("read_file", {"path": paths[0] if paths else ""})
    path, lines = _source_lines(results[-1])
    findings = []
    if path and lines:
        number, text = lines[0]
        findings.append(
            {
                "title": f"Test provider note on {path.rsplit('/', 1)[-1]}",
                "category": "maintainability",
                "severity": "info",
                "severity_rationale": "Synthetic finding from the E2E test provider.",
                "confidence": "low",
                "anchors": [
                    {"path": path, "start_line": number, "end_line": number, "quote": text}
                ],
                "triggering_conditions": "Not applicable: synthetic.",
                "impact": "None: this finding only tests the review pipeline.",
                "recommendation": "No action needed.",
            }
        )
    return _tool_call(
        "submit_review",
        {
            "summary": f"{LABEL} Synthetic review used to test the pipeline.",
            "reviewed_paths": [path] if path else [],
            "findings": findings,
        },
    )


def respond(body: dict[str, Any]) -> dict[str, Any]:
    """The next chat completion for a request (pure function; the tests drive it via HTTP)."""
    messages = body.get("messages") or []
    tools = {t["function"]["name"] for t in body.get("tools") or []}
    first = _first_user_text(messages)
    results = [str(m.get("content") or "") for m in messages if m.get("role") == "tool"]
    if "submit_answer" in tools:
        call = _question(first, results)
    elif "Review this finding" in first:
        call = _finding_review(first)
    else:
        call = _file_review(first, results)
    size = len(json.dumps(body))
    return {
        "id": f"chatcmpl-e2e-{len(messages)}",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{len(messages)}",
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["arguments"]),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": size // 4, "completion_tokens": 60},
    }


class FakeAiProvider:
    """Serve ``respond`` at ``{base_url}/chat/completions`` on a free loopback port."""

    def __init__(self, api_key: str) -> None:
        expected = f"Bearer {api_key}"

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path != "/v1/chat/completions":
                    self._send(404, {"error": {"message": "not found"}})
                    return
                if self.headers.get("Authorization") != expected:
                    self._send(401, {"error": {"message": "invalid key"}})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length))
                    self._send(200, respond(body))
                except (ValueError, KeyError, TypeError) as exc:
                    self._send(400, {"error": {"message": f"bad request: {type(exc).__name__}"}})

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((LOOPBACK, 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://{LOOPBACK}:{self._server.server_address[1]}/v1"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
