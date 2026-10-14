"""Generic MCP application connector. Actual submission requires a portal-authorized MCP server.

Expected tool contracts (customize to your provider's schema):
- prepare_application({job, candidate_profile, resume_filename, resume_base64, idempotency_key})
  -> {ready: bool, application_payload: object, message?: str}
- submit_application({job, application_payload, idempotency_key})
  -> {submitted: bool, status: str, message?: str, application_id?: str}

The connector never falls back to browser scraping or bypasses portal controls.
"""
from __future__ import annotations
import asyncio
import base64
import json
import os
import shlex
from urllib.parse import urlparse


def _config_value(name: str, default: str = "") -> str:
    """Read environment variables locally and Streamlit secrets when deployed."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st
        return str(st.secrets.get(name, default)).strip()
    except Exception:
        return default


def _configured_domains() -> set[str]:
    return {x.strip().lower().removeprefix("www.") for x in _config_value("AUTO_APPLY_ALLOWED_DOMAINS").split(",") if x.strip()}


def is_domain_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    domains = _configured_domains()
    return bool(host and domains and any(host == d or host.endswith("." + d) for d in domains))


def mcp_configured() -> bool:
    return bool(_config_value("MCP_SERVER_COMMAND").strip())


def _command_parts() -> tuple[str, list[str]]:
    raw = _config_value("MCP_SERVER_COMMAND").strip()
    if not raw:
        raise RuntimeError("MCP_SERVER_COMMAND is not configured.")
    # Prefer JSON array to avoid shell parsing ambiguities; support a quoted command string too.
    try:
        parts = json.loads(raw)
        if isinstance(parts, list) and parts and all(isinstance(p, str) for p in parts):
            return parts[0], parts[1:]
    except json.JSONDecodeError:
        pass
    parts = shlex.split(raw, posix=False)
    if not parts:
        raise RuntimeError("MCP_SERVER_COMMAND is empty.")
    return parts[0], parts[1:]


async def _call_tools(tool_calls: list[tuple[str, dict]]) -> list[dict]:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise RuntimeError("MCP SDK is missing. Install requirements.txt again.") from exc

    command, args = _command_parts()
    params = StdioServerParameters(command=command, args=args, env=os.environ.copy())
    output = []
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            available = await session.list_tools()
            names = {tool.name for tool in available.tools}
            for tool_name, payload in tool_calls:
                if tool_name not in names:
                    raise RuntimeError(f"Configured MCP server does not expose required tool '{tool_name}'.")
                result = await session.call_tool(tool_name, arguments=payload)
                if getattr(result, "isError", False):
                    raise RuntimeError(f"MCP tool '{tool_name}' reported an error.")
                text_parts = [c.text for c in getattr(result, "content", []) if getattr(c, "type", "") == "text" and getattr(c, "text", "")]
                parsed = None
                for chunk in text_parts:
                    try:
                        parsed = json.loads(chunk)
                        break
                    except json.JSONDecodeError:
                        continue
                if parsed is None:
                    parsed = {"message": " ".join(text_parts)[:1000]}
                output.append(parsed)
    return output


def run_mcp_calls(tool_calls: list[tuple[str, dict]]) -> list[dict]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call_tools(tool_calls))
    raise RuntimeError("An event loop is already running; run MCP calls in a worker thread in this environment.")


def submit_job_application(job: dict, candidate_profile: dict, resume_bytes: bytes, resume_filename: str, idempotency_key: str) -> dict:
    """Prepare and submit one application using a configured authorized MCP server."""
    if not mcp_configured():
        return {"status": "blocked_no_mcp", "message": "No authorized MCP application server is configured."}
    if not is_domain_allowed(job.get("url", "")):
        return {"status": "blocked_domain", "message": "Job domain is not in AUTO_APPLY_ALLOWED_DOMAINS."}

    # Candidate has explicitly enabled auto-apply in the UI before this function is called.
    resume_b64 = base64.b64encode(resume_bytes).decode("ascii")
    prepare_payload = {
        "job": {k: job.get(k) for k in ("title", "company", "location", "description", "url", "source")},
        "candidate_profile": candidate_profile,
        "resume_filename": resume_filename,
        "resume_base64": resume_b64,
        "idempotency_key": idempotency_key,
    }
    prepared = run_mcp_calls([("prepare_application", prepare_payload)])[0]
    if not bool(prepared.get("ready")):
        return {"status": "needs_review", "message": str(prepared.get("message", "Application needs review; not submitted."))[:1000]}
    application_payload = prepared.get("application_payload")
    if application_payload is None:
        return {"status": "needs_review", "message": "MCP prepare tool returned no application_payload; not submitted."}
    submitted = run_mcp_calls([("submit_application", {
        "job": prepare_payload["job"],
        "application_payload": application_payload,
        "idempotency_key": idempotency_key,
    })])[0]
    success = bool(submitted.get("submitted"))
    return {
        "status": str(submitted.get("status") or ("submitted" if success else "submission_failed")),
        "message": str(submitted.get("message", "Submission confirmed by MCP provider." if success else "MCP provider did not confirm submission."))[:1000],
        "application_id": str(submitted.get("application_id", ""))[:200],
    }
