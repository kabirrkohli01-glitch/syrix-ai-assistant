"""One OpenAI-compatible chat client for every pool provider.

All six lanes (Groq, Cerebras, OpenRouter, GitHub Models, NVIDIA NIM, Ollama)
speak the same /chat/completions dialect — one transport, one error model.
Raises ProviderHTTPError with the status code so the pool can distinguish a
429 (cooldown + rotate) from a 5xx (health decay) from a timeout.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional

import requests
from requests import exceptions as requests_exceptions

from .privacy import payload_contains_secret, provider_is_loopback

_session_lock = threading.Lock()
_session: Optional[requests.Session] = None
_monotonic = time.monotonic


def _http_session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _session = s
        return _session


class ProviderHTTPError(RuntimeError):
    def __init__(self, status: int, snippet: str = "", retry_after_s: Optional[float] = None):
        super().__init__(f"HTTP {status}: {snippet or 'request failed'}")
        self.status = int(status)
        self.snippet = snippet
        self.retry_after_s = retry_after_s


class ProviderTimeout(RuntimeError):
    pass


class ProviderNetworkError(RuntimeError):
    pass


class ProviderPrivacyBlocked(RuntimeError):
    pass


class EmptyReply(RuntimeError):
    pass


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage_tokens: int = 0
    model: str = ""
    finish_reason: str = ""
    stop_reason: str = ""
    continuation_count: int = 0
    truncated: bool = False


_LENGTH_STOP_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})
_MAX_LENGTH_CONTINUATIONS = 1
_MIN_CONTINUATION_OVERLAP = 8
_CONTINUATION_INSTRUCTION = (
    "Continue the same answer from the exact next character. Do not repeat any "
    "earlier text. Finish the answer within this response."
)
_TRUNCATION_NOTICE = (
    "\n\n[Output limit reached; bounded continuation could not complete.]"
)


def _completion_metadata(choice: Dict[str, Any], envelope: Dict[str, Any]) -> tuple[str, str]:
    """Retain both OpenAI ``finish_reason`` and provider ``stop_reason``."""
    finish_reason = str(choice.get("finish_reason") or envelope.get("finish_reason") or "").strip()
    stop_reason = str(choice.get("stop_reason") or envelope.get("stop_reason") or "").strip()
    return finish_reason, stop_reason


def _is_length_stop(finish_reason: str, stop_reason: str) -> bool:
    return any(
        str(reason or "").strip().lower() in _LENGTH_STOP_REASONS
        for reason in (finish_reason, stop_reason)
    )


def _continuation_messages(messages: List[Dict[str, Any]], partial: str) -> List[Dict[str, Any]]:
    return [
        *messages,
        {"role": "assistant", "content": partial},
        {"role": "user", "content": _CONTINUATION_INSTRUCTION},
    ]


def _nonduplicating_suffix(existing: str, continuation: str) -> str:
    """Drop an exact repeated boundary while preserving the next character."""
    if not existing or not continuation:
        return continuation
    leading = len(continuation) - len(continuation.lstrip())
    starts = (0, leading) if leading else (0,)
    best_overlap = 0
    best_start = 0
    for start in starts:
        candidate = continuation[start:]
        for size in range(
            min(len(existing), len(candidate), 4096),
            _MIN_CONTINUATION_OVERLAP - 1,
            -1,
        ):
            if existing.endswith(candidate[:size]):
                if size > best_overlap:
                    best_overlap = size
                    best_start = start
                break
    return continuation[best_start + best_overlap:] if best_overlap else continuation


def _sanitize_snippet(text: str, limit: int = 200) -> str:
    out = " ".join(str(text or "").split())[:limit]
    # never leak anything that smells like a credential
    lowered = out.lower()
    if "bearer" in lowered or "api_key" in lowered or "apikey" in lowered:
        return "(redacted error body)"
    return out


def _parse_retry_after(resp: requests.Response) -> Optional[float]:
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after") or ""
    try:
        v = float(str(raw).strip())
        if 0 < v < 3600:
            return v
    except Exception:
        pass
    return None


def _sanitize_messages(messages: List[Dict[str, Any]], keep_tools: bool) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages or []:
        role = m.get("role")
        if role in ("system", "user", "assistant"):
            content = m.get("content")
            if isinstance(content, list):
                parts = [str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = "\n".join(p for p in parts if p).strip()
            if role == "assistant" and keep_tools and m.get("tool_calls"):
                out.append({"role": "assistant", "content": m.get("content"), "tool_calls": m["tool_calls"]})
                continue
            if isinstance(content, str) and content.strip():
                out.append({"role": str(role), "content": content})
        elif role == "tool" and keep_tools:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(m.get("tool_call_id") or ""),
                    "content": str(m.get("content") or ""),
                }
            )
    return out


def chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.55,
    max_tokens: int = 1024,
    timeout_s: float = 30.0,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: str = "auto",
    extra_headers: Optional[Dict[str, str]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> ChatResult:
    if (
        payload_contains_secret(messages, tools)
        and not provider_is_loopback(base_url)
    ):
        raise ProviderPrivacyBlocked("external_provider_secret_blocked")
    clean = _sanitize_messages(messages, keep_tools=bool(tools))
    if not clean:
        raise EmptyReply("no text messages to send")
    body: Dict[str, Any] = {
        "model": model,
        "messages": clean,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    # Per-lane request fields the caller knows about and this transport does
    # not — e.g. turning a local reasoning model's thinking pass off, without
    # teaching the shared client which providers have one.
    if extra_body:
        body.update({k: v for k, v in extra_body.items() if k not in body})
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers.update(extra_headers or {})

    request_messages = clean
    assembled = ""
    usage_tokens = 0
    deadline = _monotonic() + max(0.001, float(timeout_s))
    length_finish_reason = ""
    length_stop_reason = ""

    def _handled_truncation() -> ChatResult:
        return ChatResult(
            text=assembled.rstrip() + _TRUNCATION_NOTICE,
            usage_tokens=usage_tokens,
            model=model,
            finish_reason=length_finish_reason,
            stop_reason=length_stop_reason,
            continuation_count=_MAX_LENGTH_CONTINUATIONS,
            truncated=True,
        )

    for continuation_count in range(_MAX_LENGTH_CONTINUATIONS + 1):
        body["messages"] = request_messages
        remaining_s = deadline - _monotonic()
        if remaining_s <= 0.0:
            if continuation_count and assembled:
                return _handled_truncation()
            raise ProviderTimeout(f"timed out after {timeout_s:.0f}s")
        try:
            resp = _http_session().post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=body,
                timeout=max(0.001, remaining_s),
            )
        except requests_exceptions.Timeout as exc:
            if continuation_count and assembled:
                return _handled_truncation()
            raise ProviderTimeout(f"timed out after {timeout_s:.0f}s") from exc
        except requests_exceptions.RequestException as exc:
            if continuation_count and assembled:
                return _handled_truncation()
            raise ProviderNetworkError(type(exc).__name__) from exc

        if resp.status_code >= 400:
            if continuation_count and assembled:
                return _handled_truncation()
            raise ProviderHTTPError(
                resp.status_code,
                _sanitize_snippet(getattr(resp, "text", "")),
                retry_after_s=_parse_retry_after(resp),
            )
        try:
            data = resp.json()
        except Exception as exc:
            if continuation_count and assembled:
                return _handled_truncation()
            raise EmptyReply("non-JSON response") from exc

        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            if continuation_count and assembled:
                return _handled_truncation()
            raise EmptyReply("no choices in response")
        choice = choices[0]
        msg = choice.get("message") or {}
        raw_text = msg.get("content") or ""
        raw_text = raw_text if isinstance(raw_text, str) else str(raw_text)
        text = raw_text.strip()
        finish_reason, stop_reason = _completion_metadata(choice, data)

        tool_calls: List[Dict[str, Any]] = []
        for tc in msg.get("tool_calls") or []:
            try:
                fn = tc.get("function") or {}
                args_raw = fn.get("arguments")
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except Exception:
                        args = {}
                elif isinstance(args_raw, dict):
                    args = args_raw
                else:
                    args = {}
                tool_calls.append(
                    {"id": str(tc.get("id") or ""), "name": str(fn.get("name") or ""), "arguments": args}
                )
            except Exception:
                continue

        usage = data.get("usage") or {}
        usage_tokens += int(usage.get("total_tokens") or 0)
        length_stopped = _is_length_stop(finish_reason, stop_reason)
        if length_stopped and (tools or tool_calls):
            # Partial JSON arguments are unsafe to execute and cannot be joined
            # reliably. Return an explicit non-tool result on this same lane so
            # the pool cannot execute partial arguments or rotate generations.
            safe_text = text.rstrip() + _TRUNCATION_NOTICE if text else _TRUNCATION_NOTICE.strip()
            return ChatResult(
                text=safe_text,
                usage_tokens=usage_tokens,
                model=model,
                finish_reason=finish_reason,
                stop_reason=stop_reason,
                continuation_count=continuation_count,
                truncated=True,
            )
        if not text and not tool_calls:
            if continuation_count and assembled:
                return _handled_truncation()
            raise EmptyReply("empty content")

        if text:
            assembled += _nonduplicating_suffix(assembled, raw_text)
        if length_stopped and continuation_count < _MAX_LENGTH_CONTINUATIONS:
            length_finish_reason = finish_reason
            length_stop_reason = stop_reason
            request_messages = _continuation_messages(clean, assembled)
            continue
        if length_stopped:
            assembled = assembled.rstrip() + _TRUNCATION_NOTICE

        return ChatResult(
            text=assembled.strip(),
            tool_calls=tool_calls,
            usage_tokens=usage_tokens,
            model=model,
            finish_reason=finish_reason,
            stop_reason=stop_reason,
            continuation_count=continuation_count,
            truncated=length_stopped,
        )

    raise EmptyReply("completion continuation exhausted")  # pragma: no cover


def stream(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.55,
    max_tokens: int = 1024,
    timeout_s: float = 45.0,
    extra_headers: Optional[Dict[str, str]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Generator[str, None, None]:
    """Yield text deltas from an SSE stream. Raises like chat() on failure."""
    if payload_contains_secret(messages) and not provider_is_loopback(base_url):
        raise ProviderPrivacyBlocked("external_provider_secret_blocked")
    clean = _sanitize_messages(messages, keep_tools=False)
    if not clean:
        raise EmptyReply("no text messages to send")
    body: Dict[str, Any] = {
        "model": model,
        "messages": clean,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "stream": True,
    }
    # Same reason as chat(): without this a local reasoning model streams its
    # thinking and no content, so the stream ends having yielded nothing.
    if extra_body:
        body.update({k: v for k, v in extra_body.items() if k not in body})
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers.update(extra_headers or {})
    request_messages = clean
    assembled = ""
    received_any = False
    deadline = _monotonic() + max(0.001, float(timeout_s))

    for continuation_count in range(_MAX_LENGTH_CONTINUATIONS + 1):
        body["messages"] = request_messages
        remaining_s = deadline - _monotonic()
        if remaining_s <= 0.0:
            if continuation_count and received_any:
                yield _TRUNCATION_NOTICE
                return
            raise ProviderTimeout(f"stream timed out after {timeout_s:.0f}s")
        try:
            resp = _http_session().post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=body,
                stream=True,
                timeout=max(0.001, remaining_s),
            )
        except (requests_exceptions.Timeout, requests_exceptions.RequestException) as exc:
            if continuation_count and received_any:
                yield _TRUNCATION_NOTICE
                return
            if isinstance(exc, requests_exceptions.Timeout):
                raise ProviderTimeout(f"stream timed out after {timeout_s:.0f}s") from exc
            raise ProviderNetworkError(type(exc).__name__) from exc
        if resp.status_code >= 400:
            if continuation_count and received_any:
                yield _TRUNCATION_NOTICE
                return
            raise ProviderHTTPError(
                resp.status_code,
                _sanitize_snippet(getattr(resp, "text", "")),
                retry_after_s=_parse_retry_after(resp),
            )

        leg_parts: List[str] = []
        finish_reason = ""
        stop_reason = ""
        for line in resp.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
            if not line_str.startswith("data: "):
                continue
            payload = line_str[6:]
            if payload.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
                choices = chunk.get("choices") or []
                if not choices or not isinstance(choices[0], dict):
                    continue
                choice = choices[0]
                chunk_finish, chunk_stop = _completion_metadata(choice, chunk)
                finish_reason = chunk_finish or finish_reason
                stop_reason = chunk_stop or stop_reason
                delta = choice.get("delta") or {}
                content = delta.get("content") or ""
                if content:
                    leg_parts.append(str(content))
                    if continuation_count == 0:
                        received_any = True
                        assembled += str(content)
                        yield str(content)
            except Exception:
                continue

        leg_text = "".join(leg_parts)
        if continuation_count:
            suffix = _nonduplicating_suffix(assembled, leg_text)
            if suffix:
                received_any = True
                assembled += suffix
                yield suffix
        if not leg_text:
            if continuation_count and received_any:
                yield _TRUNCATION_NOTICE
                return
            raise EmptyReply("stream produced no content")

        length_stopped = _is_length_stop(finish_reason, stop_reason)
        if length_stopped and continuation_count < _MAX_LENGTH_CONTINUATIONS:
            request_messages = _continuation_messages(clean, assembled)
            continue
        if length_stopped:
            yield _TRUNCATION_NOTICE
        return

    raise EmptyReply("stream continuation exhausted")  # pragma: no cover
