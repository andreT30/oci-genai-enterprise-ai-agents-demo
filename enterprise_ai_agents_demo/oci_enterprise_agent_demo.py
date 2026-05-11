#!/usr/bin/env python3
"""Quick OCI Enterprise AI Agents demo using OpenAI-compatible Responses APIs.

The flow mirrors the handoff MD:
conversation memory, structured outputs, explicit tool context, backend-owned
tool execution, and final response composition. It intentionally uses local
JSON data instead of a database so it can run quickly on an OCI Compute instance.
"""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI


ROOT = Path(__file__).resolve().parent
DEFAULT_MEMORY_FILE = ROOT / "agent_memory.json"
DEFAULT_LOG_FILE = ROOT / "agent_demo.log"
DEFAULT_QUESTION = (
    "Payments API looks slow in phx. What should I do, and do any policies apply?"
)
FOLLOW_UP = "Now make that an executive summary and include the owner."


def setup_logging(log_file: str | Path | None = None) -> logging.Logger:
    logger = logging.getLogger("oci_enterprise_agent_demo")
    path = Path(log_file or os.getenv("OCI_AGENT_LOG_FILE", str(DEFAULT_LOG_FILE)))
    current_path = getattr(logger, "_oci_agent_log_file", None)
    if logger.handlers and current_path == str(path):
        return logger

    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    path.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    logger._oci_agent_log_file = str(path)
    return logger


LOGGER = setup_logging()


def strict_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": name,
        "strict": True,
        "schema": schema,
    }


INTENT_SCHEMA = strict_schema(
    "intent_result",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["label", "score10", "needs_clarify", "reason"],
        "properties": {
            "label": {
                "type": "string",
                "enum": ["OPERATIONS_TRIAGE", "POLICY_QA", "SUMMARY", "GENERAL_CHAT"],
            },
            "score10": {"type": "integer", "minimum": 0, "maximum": 10},
            "needs_clarify": {"type": "boolean"},
            "reason": {"type": "string"},
        },
    },
)

PLAN_SCHEMA = strict_schema(
    "agent_plan",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["goal", "tool_calls"],
        "properties": {
            "goal": {"type": "string"},
            "tool_calls": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tool_name", "query", "reason"],
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "enum": [
                                "search_runbooks",
                                "get_service_health",
                                "check_policy",
                            ],
                        },
                        "query": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
    },
)

ANSWER_SCHEMA = strict_schema(
    "final_answer",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer", "actions", "citations", "confidence"],
        "properties": {
            "answer": {"type": "string"},
            "actions": {"type": "array", "items": {"type": "string"}},
            "citations": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 10},
        },
    },
)


@dataclass
class DemoConfig:
    region: str
    project: str
    model: str
    auth: str
    profile: str
    api_key: str | None
    dry_run: bool

    @property
    def base_url(self) -> str:
        return (
            f"https://inference.generativeai.{self.region}.oci.oraclecloud.com"
            "/openai/v1"
        )


class JsonMemoryStore:
    """Small file-backed store for app state, not model memory."""

    def __init__(self, path: str | Path = DEFAULT_MEMORY_FILE) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"sessions": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_local_id() -> str:
        return f"local_{uuid.uuid4().hex[:12]}"

    def get_session(self, session_id: str) -> dict[str, Any]:
        data = self.load()
        sessions = data.setdefault("sessions", {})
        session = sessions.setdefault(
            session_id,
            {
                "conversation_id": None,
                "memory_subject_id": session_id,
                "messages": [],
            },
        )
        session.setdefault("memory_subject_id", session_id)
        session.setdefault("messages", [])
        self._migrate_session(session_id, session)
        self.save(data)
        return session

    def _migrate_session(self, session_id: str, session: dict[str, Any]) -> None:
        if session.get("conversations"):
            session.setdefault("active_conversation_local_id", session["conversations"][0]["local_id"])
            return

        now = self._now()
        local_id = self._new_local_id()
        conversation = {
            "local_id": local_id,
            "title": "Conversation 1",
            "conversation_id": session.get("conversation_id") or "",
            "memory_subject_id": session.get("memory_subject_id") or session_id,
            "messages": session.get("messages", []),
            "created_at": now,
            "updated_at": now,
        }
        session["conversations"] = [conversation]
        session["active_conversation_local_id"] = local_id

    def list_conversations(self, session_id: str) -> list[dict[str, Any]]:
        session = self.get_session(session_id)
        return sorted(
            session.get("conversations", []),
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )

    def get_active_conversation(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        active_id = session.get("active_conversation_local_id")
        conversations = session.setdefault("conversations", [])
        for conversation in conversations:
            if conversation["local_id"] == active_id:
                return conversation
        if not conversations:
            return self.add_conversation(session_id)
        session["active_conversation_local_id"] = conversations[0]["local_id"]
        self._save_session(session_id, session)
        return conversations[0]

    def _save_session(self, session_id: str, session: dict[str, Any]) -> None:
        data = self.load()
        data.setdefault("sessions", {})[session_id] = session
        self.save(data)

    def set_active_conversation(self, session_id: str, local_id: str) -> None:
        data = self.load()
        session = data.setdefault("sessions", {}).setdefault(session_id, {})
        self._migrate_session(session_id, session)
        if any(conv["local_id"] == local_id for conv in session["conversations"]):
            session["active_conversation_local_id"] = local_id
        self.save(data)

    def add_conversation(
        self,
        session_id: str,
        memory_subject_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        data = self.load()
        session = data.setdefault("sessions", {}).setdefault(session_id, {})
        session.setdefault("memory_subject_id", session_id)
        self._migrate_session(session_id, session)
        now = self._now()
        subject = memory_subject_id or f"{session_id}-{uuid.uuid4().hex[:8]}"
        conversation = {
            "local_id": self._new_local_id(),
            "title": title or f"Conversation {len(session['conversations']) + 1}",
            "conversation_id": "",
            "memory_subject_id": subject,
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        session["conversations"].append(conversation)
        session["active_conversation_local_id"] = conversation["local_id"]
        session["memory_subject_id"] = subject
        self.save(data)
        return conversation

    def delete_active_conversation(self, session_id: str) -> None:
        data = self.load()
        session = data.setdefault("sessions", {}).setdefault(session_id, {})
        self._migrate_session(session_id, session)
        active_id = session.get("active_conversation_local_id")
        session["conversations"] = [
            conv for conv in session["conversations"] if conv["local_id"] != active_id
        ]
        if session["conversations"]:
            session["active_conversation_local_id"] = session["conversations"][0]["local_id"]
            session["memory_subject_id"] = session["conversations"][0].get(
                "memory_subject_id", session_id
            )
        else:
            conversation = {
                "local_id": self._new_local_id(),
                "title": "Conversation 1",
                "conversation_id": "",
                "memory_subject_id": session_id,
                "messages": [],
                "created_at": self._now(),
                "updated_at": self._now(),
            }
            session["conversations"] = [conversation]
            session["active_conversation_local_id"] = conversation["local_id"]
            session["memory_subject_id"] = session_id
        self.save(data)

    def set_session_metadata(
        self,
        session_id: str,
        conversation_id: str | None = None,
        memory_subject_id: str | None = None,
    ) -> None:
        data = self.load()
        session = data.setdefault("sessions", {}).setdefault(session_id, {})
        self._migrate_session(session_id, session)
        active_id = session.get("active_conversation_local_id")
        active = next(
            conv for conv in session["conversations"] if conv["local_id"] == active_id
        )
        if conversation_id is not None:
            session["conversation_id"] = conversation_id
            active["conversation_id"] = conversation_id
        if memory_subject_id is not None:
            session["memory_subject_id"] = memory_subject_id
            active["memory_subject_id"] = memory_subject_id
        self.save(data)

    def start_new_conversation(self, session_id: str) -> None:
        active = self.get_active_conversation(session_id)
        self.add_conversation(
            session_id,
            memory_subject_id=active.get("memory_subject_id", session_id),
            title="New conversation",
        )

    def append_turn(self, session_id: str, turn: dict[str, Any]) -> None:
        data = self.load()
        session = data.setdefault("sessions", {}).setdefault(session_id, {})
        self._migrate_session(session_id, session)
        active_id = session.get("active_conversation_local_id")
        conversation = next(
            conv for conv in session["conversations"] if conv["local_id"] == active_id
        )
        messages = conversation.setdefault("messages", [])

        answer = turn["answer"]
        now = self._now()
        messages.extend(
            [
                {"role": "user", "content": turn["question"], "created_at": now},
                {"role": "assistant", "content": format_answer(answer), "created_at": now},
            ]
        )
        conversation["updated_at"] = now
        if conversation["title"].startswith("Conversation ") or conversation["title"] == "New conversation":
            conversation["title"] = turn["question"][:48]
        session["conversation_id"] = conversation.get("conversation_id", "")
        session["memory_subject_id"] = conversation.get("memory_subject_id", session_id)
        session["messages"] = messages
        self.save(data)

    def clear(self, session_id: str) -> None:
        data = self.load()
        data.setdefault("sessions", {}).pop(session_id, None)
        self.save(data)


class DryRunClient:
    """Deterministic stand-in that lets the orchestration be tested locally."""

    class Conversations:
        @staticmethod
        def create(**_kwargs: Any) -> Any:
            return type("Conversation", (), {"id": "conv_dry_run"})()

    def __init__(self) -> None:
        self.conversations = self.Conversations()

    def structured_response(
        self, schema_name: str, _prompt: str, _conversation_id: str
    ) -> dict[str, Any]:
        if schema_name == "intent_result":
            return {
                "label": "OPERATIONS_TRIAGE",
                "score10": 9,
                "needs_clarify": False,
                "reason": "The user is asking for incident triage and policy impact.",
            }
        if schema_name == "agent_plan":
            return {
                "goal": "Triage Payments API degradation and identify policy obligations.",
                "tool_calls": [
                    {
                        "tool_name": "get_service_health",
                        "query": "Payments API phx",
                        "reason": "Confirm current health and ownership.",
                    },
                    {
                        "tool_name": "search_runbooks",
                        "query": "Payments API latency",
                        "reason": "Find the operational runbook.",
                    },
                    {
                        "tool_name": "check_policy",
                        "query": "customer escalation two incidents commerce service",
                        "reason": "Identify customer escalation requirements.",
                    },
                ],
            }
        return {
            "answer": (
                "Payments API in phx is degraded with two open incidents and p95 "
                "latency at 810 ms. Start the latency triage runbook, page "
                "commerce-platform only after human approval, and prepare a "
                "customer-impact bridge."
            ),
            "actions": [
                "Check upstream gateway error rate.",
                "Compare p95 latency between phx and iad.",
                "Ask a human approver before paging or notifying customers.",
            ],
            "citations": ["service_health:Payments API", "RB-101", "POL-7", "POL-12"],
            "confidence": 9,
        }

    @staticmethod
    def create_response(**_kwargs: Any) -> Any:
        input_text = str(_kwargs.get("input", ""))
        user_question = input_text.rsplit("User question:", 1)[-1].lower()
        if "executive summary" in user_question:
            output_text = (
                "Executive summary: Payments API in phx remains degraded. "
                "Owner: commerce-platform. Recommended next step is to follow "
                "RB-101 and prepare a customer-impact bridge after human approval."
            )
        else:
            output_text = (
                "Payments API in phx is degraded with two open incidents and p95 "
                "latency at 810 ms. Start the latency triage runbook, keep "
                "commerce-platform informed, and require human approval before paging "
                "or customer notification."
            )
        return {
            "id": "resp_dry_run",
            "output_text": output_text,
            "output": [],
        }


class OciResponsesClient:
    def __init__(self, config: DemoConfig) -> None:
        self.model = config.model
        LOGGER.info(
            "initializing OCI Responses client region=%s model=%s auth=%s",
            config.region,
            config.model,
            config.auth,
        )
        self.client = make_openai_client(config)

    @property
    def conversations(self) -> Any:
        return self.client.conversations

    def structured_response(
        self, schema_name: str, prompt: str, conversation_id: str
    ) -> dict[str, Any]:
        schema = {
            "intent_result": INTENT_SCHEMA,
            "agent_plan": PLAN_SCHEMA,
            "final_answer": ANSWER_SCHEMA,
        }[schema_name]
        response = self._create_with_retry(
            phase=schema_name,
            input=prompt,
            conversation=conversation_id,
            temperature=0,
            text={"format": schema},
            metadata={"source": "enterprise_ai_agents_demo", "phase": schema_name},
        )
        return json.loads(extract_output_text(response))

    def create_response(self, **kwargs: Any) -> Any:
        phase = kwargs.get("metadata", {}).get("phase", "response")
        return self._create_with_retry(phase=phase, **kwargs)

    def stream_response_text(self, phase: str, **kwargs: Any) -> Any:
        LOGGER.info(
            "responses.create stream phase=%s model=%s conversation=%s",
            phase,
            self.model,
            kwargs.get("conversation"),
        )
        stream = self.client.responses.create(
            model=self.model,
            stream=True,
            **kwargs,
        )
        for event in stream:
            delta = extract_stream_delta(event)
            if delta:
                yield delta

    def _create_with_retry(self, phase: str, **kwargs: Any) -> Any:
        max_attempts = int(os.getenv("OCI_AGENT_MAX_RETRIES", "3"))
        delay_seconds = float(os.getenv("OCI_AGENT_RETRY_DELAY_SECONDS", "1.5"))
        for attempt in range(1, max_attempts + 1):
            try:
                LOGGER.info(
                    "responses.create phase=%s attempt=%s/%s model=%s conversation=%s",
                    phase,
                    attempt,
                    max_attempts,
                    self.model,
                    kwargs.get("conversation"),
                )
                return self.client.responses.create(model=self.model, **kwargs)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                request_id = getattr(exc, "request_id", None)
                retriable = status_code is None or status_code == 429 or status_code >= 500
                LOGGER.exception(
                    "responses.create failed phase=%s attempt=%s/%s status_code=%s "
                    "request_id=%s retriable=%s",
                    phase,
                    attempt,
                    max_attempts,
                    status_code,
                    request_id,
                    retriable,
                )
                if not retriable or attempt == max_attempts:
                    raise
                time.sleep(delay_seconds * attempt)


def make_openai_client(config: DemoConfig) -> "OpenAI":
    import httpx
    from openai import OpenAI

    if config.auth == "api_key":
        if not config.api_key:
            raise ValueError("OCI_GENAI_API_KEY is required when OCI_GENAI_AUTH=api_key")
        return OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            project=config.project,
        )

    try:
        from oci_genai_auth import (
            OciInstancePrincipalAuth,
            OciResourcePrincipalAuth,
            OciSessionAuth,
            OciUserPrincipalAuth,
        )
    except ImportError:
        from oci_openai import (
            OciInstancePrincipalAuth,
            OciResourcePrincipalAuth,
            OciSessionAuth,
            OciUserPrincipalAuth,
        )

    auth_handlers = {
        "instance_principal": OciInstancePrincipalAuth,
        "resource_principal": OciResourcePrincipalAuth,
        "session": lambda: OciSessionAuth(profile_name=config.profile),
        "user_principal": lambda: OciUserPrincipalAuth(profile_name=config.profile),
    }
    try:
        auth_handler = auth_handlers[config.auth]()
    except KeyError as exc:
        choices = ", ".join(sorted([*auth_handlers, "api_key"]))
        raise ValueError(f"Unknown OCI_GENAI_AUTH={config.auth!r}; use one of {choices}") from exc

    return OpenAI(
        base_url=config.base_url,
        api_key="not-used",
        project=config.project,
        http_client=httpx.Client(auth=auth_handler),
    )


def extract_output_text(response: Any) -> str:
    if isinstance(response, dict) and response.get("output_text"):
        return response["output_text"]
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text
    if hasattr(response, "model_dump"):
        payload = model_dump_without_warnings(response)
    elif isinstance(response, dict):
        payload = response
    else:
        payload = json.loads(response.model_dump_json())

    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(text)
    if not chunks:
        raise ValueError(f"No output text found in response: {payload}")
    return "\n".join(chunks)


def extract_stream_delta(event: Any) -> str:
    if isinstance(event, dict):
        payload = event
    elif hasattr(event, "model_dump"):
        payload = model_dump_without_warnings(event)
    else:
        payload = getattr(event, "__dict__", {})

    event_type = payload.get("type")
    if event_type in {"response.output_text.delta", "response.refusal.delta"}:
        return payload.get("delta") or ""
    if event_type == "response.output_item.done":
        item = payload.get("item", {})
        if item.get("type") == "message":
            chunks = []
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    chunks.append(content.get("text", ""))
            return "".join(chunks)
    return ""


def response_to_dict(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return model_dump_without_warnings(response)
    return json.loads(response.model_dump_json())


def model_dump_without_warnings(response: Any) -> dict[str, Any]:
    try:
        return response.model_dump(warnings=False)
    except TypeError:
        return response.model_dump()


def response_id(response: Any) -> str:
    if isinstance(response, dict):
        return response["id"]
    return response.id


def extract_function_calls(response: Any) -> list[dict[str, Any]]:
    payload = response_to_dict(response)
    calls = []
    for item in payload.get("output", []):
        if item.get("type") == "function_call":
            calls.append(
                {
                    "call_id": item["call_id"],
                    "name": item["name"],
                    "arguments": json.loads(item.get("arguments") or "{}"),
                }
            )
    return calls


def load_knowledge_base() -> dict[str, Any]:
    return json.loads((ROOT / "knowledge_base.json").read_text(encoding="utf-8"))


def get_oncall_contacts(service: str) -> list[dict[str, Any]]:
    kb = load_knowledge_base()
    matches = [
        row
        for row in kb["oncall_contacts"]
        if service.lower() in row["service"].lower()
        or row["service"].lower() in service.lower()
    ]
    return matches


def contains(text: str, query: str) -> bool:
    terms = [term.lower() for term in query.replace("-", " ").split() if len(term) > 2]
    haystack = text.lower()
    return any(term in haystack for term in terms)


def run_local_tool(tool_name: str, query: str, kb: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "get_service_health":
        service_terms = [
            row
            for row in kb["service_health"]
            if contains(row["service"], query)
        ]
        results = [
            row
            for row in service_terms
            if contains(f"{row['service']} {row['region']} {row['owner']}", query)
        ]
    elif tool_name == "search_runbooks":
        results = [
            row
            for row in kb["runbooks"]
            if contains(f"{row['id']} {row['title']} {' '.join(row['applies_to'])}", query)
        ]
    elif tool_name == "check_policy":
        results = [
            row
            for row in kb["policies"]
            if contains(f"{row['id']} {row['name']} {row['rule']}", query)
        ]
    else:
        raise ValueError(f"Unsupported tool: {tool_name}")

    return {"tool_name": tool_name, "query": query, "results": results}


def build_prompt(title: str, payload: dict[str, Any]) -> str:
    return (
        "You are an OCI enterprise AI agent demo. Return only the requested "
        "structured JSON. Do not include markdown.\n\n"
        f"{title}\n"
        f"{json.dumps(payload, indent=2)}"
    )


def build_enterprise_agent_prompt(question: str) -> str:
    return (
        "You are a concise OCI Enterprise AI Agent demo assistant. "
        "Use the OCI conversation state for prior turns. "
        "Answer the user's question directly.\n\n"
        f"{question}"
    )


def run_turn(
    responses: OciResponsesClient | DryRunClient,
    conversation_id: str,
    question: str,
) -> dict[str, Any]:
    response = responses.create_response(
        conversation=conversation_id,
        input=build_enterprise_agent_prompt(question),
    )
    answer_text = extract_output_text(response)

    return build_basic_chat_turn(question, answer_text)


def build_basic_chat_turn(question: str, answer_text: str) -> dict[str, Any]:
    return {
        "question": question,
        "intent": {
            "label": "BASIC_CHAT",
            "score10": 8,
            "needs_clarify": False,
            "reason": "Routed to the simple conversation-memory chat path.",
        },
        "plan": {
            "goal": "Answer directly with OCI Responses and OCI conversation memory.",
            "tool_calls": [],
        },
        "tool_results": [],
        "answer": as_agent_answer(
            answer_text,
            citations=["OCI Responses", "OCI Conversations memory"],
            confidence=8,
        ),
    }


class BasicChatStream:
    def __init__(
        self,
        question: str,
        config: DemoConfig,
        session_id: str = "default",
        memory_file: str | Path = DEFAULT_MEMORY_FILE,
        memory_subject_id: str | None = None,
    ) -> None:
        self.question = question
        self.config = config
        self.session_id = session_id
        self.memory_file = memory_file
        self.memory_subject_id = memory_subject_id
        self.result: dict[str, Any] | None = None

    def __iter__(self) -> Any:
        if not self.config.dry_run and not self.config.project:
            raise ValueError("Set OCI_GENAI_PROJECT_OCID or pass --project.")

        store = JsonMemoryStore(self.memory_file)
        responses = make_responses_client(self.config)
        conversation_id = get_or_create_conversation_id(
            responses,
            store,
            self.session_id,
            self.memory_subject_id,
        )
        chunks: list[str] = []

        if isinstance(responses, DryRunClient):
            text = extract_output_text(
                responses.create_response(input=build_enterprise_agent_prompt(self.question))
            )
            for chunk in text.split(" "):
                piece = f"{chunk} "
                chunks.append(piece)
                yield piece
        else:
            for chunk in responses.stream_response_text(
                "chat_basic_stream",
                conversation=conversation_id,
                input=build_enterprise_agent_prompt(self.question),
            ):
                chunks.append(chunk)
                yield chunk

        answer_text = "".join(chunks).strip()
        turn = build_basic_chat_turn(self.question, answer_text)
        store.append_turn(self.session_id, turn)
        conversation_state = store.get_active_conversation(self.session_id)
        self.result = {
            "conversation_id": conversation_id,
            "conversation_local_id": conversation_state.get("local_id"),
            "memory_subject_id": conversation_state.get(
                "memory_subject_id",
                self.session_id,
            ),
            "turn": turn,
        }


def stream_basic_answer_question(
    question: str,
    config: DemoConfig,
    session_id: str = "default",
    memory_file: str | Path = DEFAULT_MEMORY_FILE,
    memory_subject_id: str | None = None,
) -> BasicChatStream:
    return BasicChatStream(
        question,
        config,
        session_id=session_id,
        memory_file=memory_file,
        memory_subject_id=memory_subject_id,
    )


def make_responses_client(config: DemoConfig) -> OciResponsesClient | DryRunClient:
    return DryRunClient() if config.dry_run else OciResponsesClient(config)


def get_or_create_conversation_id(
    responses: OciResponsesClient | DryRunClient,
    store: JsonMemoryStore,
    session_id: str,
    memory_subject_id: str | None = None,
) -> str:
    conversation_state = store.get_active_conversation(session_id)
    subject_id = (
        memory_subject_id
        or conversation_state.get("memory_subject_id")
        or session_id
    )
    if subject_id != conversation_state.get("memory_subject_id"):
        store.set_session_metadata(session_id, memory_subject_id=subject_id)
        conversation_state = store.get_active_conversation(session_id)
    conversation_id = conversation_state.get("conversation_id")
    if conversation_id:
        return conversation_id
    LOGGER.info(
        "creating OCI conversation session_id=%s memory_subject_id=%s",
        session_id,
        subject_id,
    )
    metadata = {}
    if os.getenv("OCI_AGENT_ENABLE_LONG_TERM_MEMORY", "false").lower() == "true":
        metadata["memory_subject_id"] = subject_id
    if os.getenv("OCI_AGENT_ENABLE_SHORT_TERM_COMPACTION", "false").lower() == "true":
        metadata["short_term_memory_optimization"] = "True"

    conversation = (
        responses.conversations.create(metadata=metadata)
        if metadata
        else responses.conversations.create()
    )
    store.set_session_metadata(
        session_id,
        conversation_id=conversation.id,
        memory_subject_id=subject_id,
    )
    return conversation.id


def classify_chat_route(question: str) -> str:
    text = question.lower()
    function_terms = [
        "on call",
        "on-call",
        "contact",
        "contacts",
        "owner",
        "escalation channel",
        "who should i page",
    ]
    code_terms = [
        "calculate",
        "average",
        "median",
        "percentile",
        "above 500",
        "latency math",
        "python tool",
        "use python",
        "hello world",
        "current_time",
        "current time",
        "run python",
        "run py",
    ]
    if any(term in text for term in function_terms):
        return "function_tool"
    if any(term in text for term in code_terms):
        return "code_interpreter"
    return "basic_chat"


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def attach_tools_to_conversation() -> bool:
    """Whether tool-enabled Responses calls should share the OCI conversation.

    OCI Conversations is the stable memory path for basic chat. Some OCI
    provider/tool combinations can fail when Code Interpreter or Function Calling
    replays prior conversation output items. Keep tools isolated by default while
    allowing explicit opt-in for testing.
    """

    return env_flag("OCI_AGENT_ATTACH_TOOLS_TO_CONVERSATION", default=False)


def answer_question(
    question: str,
    config: DemoConfig,
    session_id: str = "default",
    memory_file: str | Path = DEFAULT_MEMORY_FILE,
    memory_subject_id: str | None = None,
) -> dict[str, Any]:
    if not config.dry_run and not config.project:
        raise ValueError("Set OCI_GENAI_PROJECT_OCID or pass --project.")

    LOGGER.info(
        "answer_question session_id=%s dry_run=%s question=%r",
        session_id,
        config.dry_run,
        question[:300],
    )
    store = JsonMemoryStore(memory_file)
    responses = make_responses_client(config)
    conversation_id = get_or_create_conversation_id(
        responses, store, session_id, memory_subject_id
    )
    route = classify_chat_route(question)
    LOGGER.info("routing chat turn to %s session_id=%s", route, session_id)
    attach_tools = attach_tools_to_conversation()
    if route == "function_tool":
        turn = run_function_tool_turn(
            responses,
            conversation_id,
            question,
            attach_conversation=attach_tools,
        )
    elif route == "code_interpreter":
        turn = run_code_interpreter_turn(
            responses,
            conversation_id,
            question,
            attach_conversation=attach_tools,
        )
    else:
        turn = run_turn(responses, conversation_id, question)
    store.append_turn(session_id, turn)
    conversation_state = store.get_active_conversation(session_id)
    return {
        "conversation_id": conversation_id,
        "conversation_local_id": conversation_state.get("local_id"),
        "memory_subject_id": conversation_state.get("memory_subject_id", session_id),
        "turn": turn,
    }


def format_answer(answer: dict[str, Any]) -> str:
    lines = [answer["answer"]]
    if answer.get("actions"):
        lines.extend(["", "Actions:"])
        lines.extend(f"- {action}" for action in answer["actions"])
    if answer.get("citations"):
        lines.extend(["", f"Citations: {', '.join(answer['citations'])}"])
    return "\n".join(lines)


FUNCTION_TOOL_DEFINITION = [
    {
        "type": "function",
        "name": "get_oncall_contacts",
        "description": "Return on-call escalation contacts for a named enterprise service.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name, for example Payments API.",
                }
            },
            "required": ["service"],
        },
    }
]


def as_agent_answer(
    answer: str,
    actions: list[str] | None = None,
    citations: list[str] | None = None,
    confidence: int = 8,
) -> dict[str, Any]:
    return {
        "answer": answer,
        "actions": actions or [],
        "citations": citations or [],
        "confidence": confidence,
    }


def run_function_tool_turn(
    responses: OciResponsesClient | DryRunClient,
    conversation_id: str,
    question: str,
    attach_conversation: bool = False,
) -> dict[str, Any]:
    LOGGER.info(
        "running function tool turn conversation_id=%s attach_conversation=%s",
        conversation_id,
        attach_conversation,
    )
    if isinstance(responses, DryRunClient):
        contacts = get_oncall_contacts("Payments API")
        answer = (
            "Payments API is owned by commerce-platform. The primary contact "
            "is Ava Patel in #commerce-platform-sev."
        )
        return {
            "question": question,
            "intent": {
                "label": "FUNCTION_TOOL",
                "score10": 10,
                "needs_clarify": False,
                "reason": "The user asked for contact/escalation information.",
            },
            "plan": {
                "goal": "Use OCI Function Calling to retrieve on-call contacts.",
                "tool_calls": [
                    {
                        "tool_name": "get_oncall_contacts",
                        "query": "Payments API",
                        "reason": "The question asks who is on call.",
                    }
                ],
            },
            "tool_results": [{"tool_name": "get_oncall_contacts", "results": contacts}],
            "answer": as_agent_answer(
                answer,
                actions=["Confirm human approval before paging anyone."],
                citations=["oncall_contacts:Payments API"],
                confidence=9,
            ),
        }

    request_kwargs: dict[str, Any] = {}
    if attach_conversation:
        request_kwargs["conversation"] = conversation_id

    initial = responses.create_response(
        tools=FUNCTION_TOOL_DEFINITION,
        input=question,
        instructions=(
            "If the user asks for on-call, owner, contact, or escalation channel "
            "information, use get_oncall_contacts. Otherwise answer briefly."
        ),
        metadata={
            "source": "enterprise_ai_agents_demo",
            "phase": "chat_function_tool",
            "tool_conversation_mode": "attached" if attach_conversation else "isolated",
        },
        **request_kwargs,
    )
    tool_outputs = []
    function_calls = extract_function_calls(initial)
    for call in function_calls:
        if call["name"] != "get_oncall_contacts":
            continue
        contacts = get_oncall_contacts(call["arguments"]["service"])
        tool_outputs.append(
            {
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": json.dumps({"contacts": contacts}),
            }
        )

    if tool_outputs:
        final = responses.create_response(
            instructions=(
                "Summarize the function result for an operations user. Mention that "
                "human approval is still required before paging."
            ),
            tools=FUNCTION_TOOL_DEFINITION,
            input=tool_outputs,
            previous_response_id=response_id(initial),
            metadata={
                "source": "enterprise_ai_agents_demo",
                "phase": "chat_function_tool_final",
            },
        )
        answer_text = extract_output_text(final)
    else:
        answer_text = extract_output_text(initial)

    return {
        "question": question,
        "intent": {
            "label": "FUNCTION_TOOL",
            "score10": 10 if function_calls else 5,
            "needs_clarify": False,
            "reason": "Routed through the OCI Function Calling chat path.",
        },
        "plan": {
            "goal": (
                "Let OCI Responses request a local function call"
                + (
                    " attached to the OCI conversation."
                    if attach_conversation
                    else " in an isolated tool call."
                )
            ),
            "tool_calls": function_calls,
            "tool_conversation_mode": "attached" if attach_conversation else "isolated",
        },
        "tool_results": tool_outputs,
        "answer": as_agent_answer(
            answer_text,
            actions=["Keep paging/customer notification decisions behind human approval."],
            citations=["OCI Function Calling", "oncall_contacts"],
            confidence=8,
        ),
    }


def run_code_interpreter_turn(
    responses: OciResponsesClient | DryRunClient,
    conversation_id: str,
    question: str,
    attach_conversation: bool = False,
) -> dict[str, Any]:
    LOGGER.info(
        "running code interpreter turn conversation_id=%s attach_conversation=%s",
        conversation_id,
        attach_conversation,
    )
    task = build_code_interpreter_task(question)
    if isinstance(responses, DryRunClient):
        answer = dry_run_code_interpreter_answer(task)
    else:
        request_kwargs: dict[str, Any] = {}
        if attach_conversation:
            request_kwargs["conversation"] = conversation_id

        response = responses.create_response(
            tools=[{"type": "code_interpreter", "container": {"type": "auto"}}],
            instructions=(
                "Use the python tool to execute the requested Python code. "
                "Return the exact printed output and one short note that it was "
                "run with Code Interpreter."
            ),
            input=task,
            metadata={
                "source": "enterprise_ai_agents_demo",
                "phase": "chat_code_interpreter",
                "tool_conversation_mode": "attached" if attach_conversation else "isolated",
            },
            **request_kwargs,
        )
        answer = extract_output_text(response)

    return {
        "question": question,
        "intent": {
            "label": "CODE_INTERPRETER",
            "score10": 10,
            "needs_clarify": False,
            "reason": "The user asked for a calculation or data-analysis style answer.",
        },
        "plan": {
            "goal": (
                "Use OCI Code Interpreter to run Python"
                + (
                    " attached to the OCI conversation."
                    if attach_conversation
                    else " in an isolated tool call."
                )
            ),
            "tool_calls": [{"type": "code_interpreter", "container": {"type": "auto"}}],
            "tool_conversation_mode": "attached" if attach_conversation else "isolated",
        },
        "tool_results": [{"tool_name": "code_interpreter", "results": "OCI-managed python sandbox"}],
        "answer": as_agent_answer(
            answer,
            citations=["OCI Code Interpreter"],
            confidence=9,
        ),
    }


def build_code_interpreter_task(question: str) -> str:
    text = question.lower()
    current_time = datetime.now().astimezone().isoformat(timespec="seconds")
    if "hello world" in text or "current_time" in text or "current time" in text:
        return (
            "Run this Python code with the python tool and return the printed "
            "output exactly:\n\n"
            "```python\n"
            f"current_time = {current_time!r}\n"
            "print(f\"Hello World! The time is {current_time}\")\n"
            "```"
        )
    return question


def dry_run_code_interpreter_answer(task: str) -> str:
    if "Hello World!" in task:
        current_time = task.split("current_time = ", 1)[1].split("\n", 1)[0].strip("'\"")
        return f"Hello World! The time is {current_time}"
    return (
        "Average p95 latency is 470 ms. Payments API is the only service "
        "above 500 ms."
    )


def run_function_tool_example(config: DemoConfig) -> dict[str, Any]:
    if config.dry_run:
        contacts = get_oncall_contacts("Payments API")
        return {
            "question": "Who is on call for Payments API?",
            "function_calls": [
                {
                    "name": "get_oncall_contacts",
                    "arguments": {"service": "Payments API"},
                }
            ],
            "tool_outputs": [{"contacts": contacts}],
            "answer": (
                "Payments API is owned by commerce-platform. The primary contact "
                "is Ava Patel in #commerce-platform-sev."
            ),
        }

    responses = OciResponsesClient(config)
    initial = responses.create_response(
        tools=FUNCTION_TOOL_DEFINITION,
        input=(
            "Use the get_oncall_contacts function to find the on-call contact "
            "for Payments API, then answer briefly."
        ),
        metadata={"source": "enterprise_ai_agents_demo", "phase": "function_tool"},
    )
    tool_outputs = []
    function_calls = extract_function_calls(initial)
    for call in function_calls:
        if call["name"] != "get_oncall_contacts":
            continue
        contacts = get_oncall_contacts(call["arguments"]["service"])
        tool_outputs.append(
            {
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": json.dumps({"contacts": contacts}),
            }
        )

    if not tool_outputs:
        return {
            "question": "Who is on call for Payments API?",
            "function_calls": function_calls,
            "tool_outputs": [],
            "answer": extract_output_text(initial),
        }

    final = responses.create_response(
        instructions=(
            "Summarize the function result for an operations user. Mention that "
            "human approval is still required for paging."
        ),
        tools=FUNCTION_TOOL_DEFINITION,
        input=tool_outputs,
        previous_response_id=response_id(initial),
        metadata={"source": "enterprise_ai_agents_demo", "phase": "function_tool_final"},
    )
    return {
        "question": "Who is on call for Payments API?",
        "function_calls": function_calls,
        "tool_outputs": tool_outputs,
        "answer": extract_output_text(final),
    }


def run_code_interpreter_example(config: DemoConfig) -> dict[str, Any]:
    question = 'Run a py code for "Hello World! The time is <<current_time>>"'
    task = build_code_interpreter_task(question)
    if config.dry_run:
        return {
            "question": question,
            "tool": {"type": "code_interpreter", "container": {"type": "auto"}},
            "answer": dry_run_code_interpreter_answer(task),
        }

    responses = OciResponsesClient(config)
    response = responses.create_response(
        tools=[{"type": "code_interpreter", "container": {"type": "auto"}}],
        instructions=(
            "Use the python tool to execute the requested Python code. Return the "
            "exact printed output."
        ),
        input=task,
        metadata={"source": "enterprise_ai_agents_demo", "phase": "code_interpreter"},
    )
    return {
        "question": question,
        "tool": {"type": "code_interpreter", "container": {"type": "auto"}},
        "answer": extract_output_text(response),
    }


def config_from_env(args: argparse.Namespace) -> DemoConfig:
    return DemoConfig(
        region=args.region or os.getenv("OCI_GENAI_REGION", "us-chicago-1"),
        project=args.project or os.getenv("OCI_GENAI_PROJECT_OCID", ""),
        model=args.model or os.getenv("OCI_GENAI_MODEL", "openai.gpt-oss-120b"),
        auth=args.auth or os.getenv("OCI_GENAI_AUTH", "instance_principal"),
        profile=args.profile or os.getenv("OCI_CLI_PROFILE", "DEFAULT"),
        api_key=os.getenv("OCI_GENAI_API_KEY"),
        dry_run=args.dry_run,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Demo OCI Enterprise AI Agents with Responses + Conversations."
    )
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--follow-up", default=FOLLOW_UP)
    parser.add_argument("--region")
    parser.add_argument("--project")
    parser.add_argument("--model")
    parser.add_argument(
        "--auth",
        choices=[
            "instance_principal",
            "resource_principal",
            "session",
            "user_principal",
            "api_key",
        ],
    )
    parser.add_argument("--profile")
    parser.add_argument("--session-id", default="default")
    parser.add_argument("--memory-file", default=str(DEFAULT_MEMORY_FILE))
    parser.add_argument("--log-file", default=os.getenv("OCI_AGENT_LOG_FILE", str(DEFAULT_LOG_FILE)))
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--function-tool-demo", action="store_true")
    parser.add_argument("--code-interpreter-demo", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--reset-memory", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_diagnostics(config: DemoConfig) -> int:
    checks: list[tuple[str, Any]] = []

    responses = OciResponsesClient(config)

    def plain_response() -> str:
        response = responses.create_response(
            input="In one sentence, what is Oracle APEX?",
            metadata={"source": "enterprise_ai_agents_demo", "phase": "diagnostic_plain"},
        )
        return extract_output_text(response)

    def conversation_response_no_metadata() -> str:
        conversation = responses.conversations.create()
        response = responses.create_response(
            conversation=conversation.id,
            input="In one sentence, what is Oracle APEX?",
            metadata={
                "source": "enterprise_ai_agents_demo",
                "phase": "diagnostic_conversation",
            },
        )
        return extract_output_text(response)

    def conversation_response_long_term_metadata() -> str:
        conversation = responses.conversations.create(
            metadata={
                "memory_subject_id": "diagnostic-user",
                "source": "enterprise_ai_agents_demo",
            }
        )
        response = responses.create_response(
            conversation=conversation.id,
            input="In one sentence, what is Oracle APEX?",
            metadata={
                "source": "enterprise_ai_agents_demo",
                "phase": "diagnostic_long_term_memory",
            },
        )
        return extract_output_text(response)

    def conversation_response_compaction_metadata() -> str:
        conversation = responses.conversations.create(
            metadata={
                "short_term_memory_optimization": "True",
                "source": "enterprise_ai_agents_demo",
            }
        )
        response = responses.create_response(
            conversation=conversation.id,
            input="In one sentence, what is Oracle APEX?",
            metadata={
                "source": "enterprise_ai_agents_demo",
                "phase": "diagnostic_short_term_compaction",
            },
        )
        return extract_output_text(response)

    checks.extend(
        [
            ("plain response without conversation", plain_response),
            ("conversation without memory metadata", conversation_response_no_metadata),
            ("conversation with memory_subject_id", conversation_response_long_term_metadata),
            (
                "conversation with short_term_memory_optimization",
                conversation_response_compaction_metadata,
            ),
        ]
    )

    failures = 0
    for name, check in checks:
        print(f"\n== {name} ==")
        try:
            print(check())
            print("PASS")
        except Exception as exc:
            failures += 1
            LOGGER.exception("diagnostic failed: %s", name)
            print(f"FAIL: {type(exc).__name__}: {exc}")
    return failures


def main() -> None:
    args = parse_args()
    global LOGGER
    LOGGER = setup_logging(args.log_file)
    config = config_from_env(args)
    store = JsonMemoryStore(args.memory_file)
    if args.reset_memory:
        store.clear(args.session_id)

    if args.function_tool_demo:
        print(json.dumps(run_function_tool_example(config), indent=2))
        return

    if args.code_interpreter_demo:
        print(json.dumps(run_code_interpreter_example(config), indent=2))
        return

    if args.diagnostics:
        if config.dry_run:
            print("--diagnostics requires live OCI mode; remove --dry-run.")
            return
        raise SystemExit(run_diagnostics(config))

    if args.interactive:
        print("OCI Enterprise AI Agent demo. Type 'exit' to quit.")
        while True:
            question = input("\nYou: ").strip()
            if question.lower() in {"exit", "quit"}:
                break
            result = answer_question(question, config, args.session_id, args.memory_file)
            print(f"\nAgent: {format_answer(result['turn']['answer'])}")
        return

    first = answer_question(args.question, config, args.session_id, args.memory_file)
    second = answer_question(args.follow_up, config, args.session_id, args.memory_file)

    print(
        json.dumps(
            {
                "conversation_id": first["conversation_id"],
                "memory_subject_id": first["memory_subject_id"],
                "first_turn": first["turn"],
                "follow_up_turn": second["turn"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
