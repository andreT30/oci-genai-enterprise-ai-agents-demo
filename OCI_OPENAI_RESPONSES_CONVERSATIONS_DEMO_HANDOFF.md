# OCI OpenAI-Compatible Responses/Conversations Demo Handoff

## Goal

This note summarizes the OCI OpenAI-compatible `responses` and `conversations` work used in OVBot. The production implementation target was PL/SQL/APEX, but the same API concepts apply to a quick Python + OpenWebUI demo.

Use this as context for building a demo around:

- OCI Generative AI OpenAI-compatible endpoints
- `responses` for individual model/tool calls
- `conversations` for memory and follow-up continuity
- structured output with JSON schema
- backend/tool orchestration around the LLM

## Core Concept

OCI Generative AI exposes OpenAI-compatible endpoints, so the app can call OpenAI-style APIs against OCI-hosted models.

Typical endpoints:

```text
POST https://inference.generativeai.<region>.oci.oraclecloud.com/openai/v1/responses
POST https://inference.generativeai.<region>.oci.oraclecloud.com/openai/v1/conversations
```

In OVBot, these calls are currently made from PL/SQL packages, but in a Python/OpenWebUI demo the same request shapes can be sent through the OCI OpenAI-compatible SDK/client.

## Responses API

We used `responses` as the main execution API for each LLM-powered phase/tool.

Each request includes:

- model OCID or model name
- prompt/input text
- temperature/top_p/top_k where supported
- max output tokens
- optional structured output schema
- metadata identifying source and phase

Example request shape:

```json
{
  "model": "<oci-model-ocid-or-model-name>",
  "input": "Prompt text for this phase/tool",
  "temperature": 0,
  "top_p": 1,
  "max_output_tokens": 4000,
  "text": {
    "format": {
      "type": "json_schema",
      "name": "tool_result",
      "strict": true,
      "schema": {
        "type": "object",
        "additionalProperties": false,
        "required": ["result"],
        "properties": {
          "result": { "type": "string" }
        }
      }
    }
  },
  "metadata": {
    "source": "demo",
    "phase": "SQL_DRAFT"
  }
}
```

Typical response envelope:

```json
{
  "metadata": {
    "source": "nl2sql_orch",
    "phase": "CHART"
  },
  "usage": {
    "input_tokens": 1247,
    "output_tokens": 173,
    "total_tokens": 1420
  },
  "output": [
    {
      "type": "message",
      "content": [
        {
          "type": "output_text",
          "text": "{\"chart_type\":\"bar\",\"x\":\"MONTH_DT\"}"
        }
      ]
    }
  ],
  "model": "gpt-oss-120b",
  "status": "completed"
}
```

The assistant text is normally extracted from:

```text
output[].content[].text
```

## Conversations API

We used `conversations` to create and maintain model-side conversation continuity.

A conversation create response looks like:

```json
{
  "id": "conv_fra_xxx",
  "object": "conversation",
  "metadata": {
    "source": "nl2sql_orch",
    "phase": "INTENT_CLASSIFIER"
  }
}
```

The app stores the returned conversation id and associates later model calls with that conversation, allowing follow-up prompts to benefit from memory.

Important lesson from OVBot: memory helps, but it is not enough by itself for reliable analytical follow-ups. We still pass explicit context into the relevant tool calls, including:

- previous user prompt
- previous SQL
- previous assistant answer
- previous summary
- previous raw response/reasoning artifacts when useful
- previous execution data
- previous chart spec
- latest user prompt

This made follow-ups like the below much more reliable:

```text
Initial: Display passes sold per year month for 2025 and 2026. Also provide the top 5 performing months.
Follow-up: and top 10?
```

## Structured Output

We moved many LLM phases from free-form JSON to API-level structured output using `text.format.type = "json_schema"` with `strict = true`.

Example intent classifier schema:

```json
{
  "type": "json_schema",
  "name": "intent_classifier_result",
  "strict": true,
  "schema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["label", "score10", "needs_clarify"],
    "properties": {
      "label": {
        "type": "string",
        "enum": ["NL2SQL", "FOLLOWUP", "GENERAL_CHAT", "BUSINESS_CHAT"]
      },
      "score10": {
        "type": "integer",
        "minimum": 0,
        "maximum": 10
      },
      "needs_clarify": {
        "type": "boolean"
      }
    }
  }
}
```

Why structured output helped:

- less manual JSON extraction
- fewer malformed outputs
- clearer contracts between tools
- easier validation per phase
- better foundation for future tool calling

Tradeoff:

- reasoning details are not always exposed consistently across providers/models
- OpenAI/OSS-style models with structured output may hide reasoning details that appeared in non-structured responses
- we chose reliability and contract stability over depending on internal reasoning output

## Reasoning Controls

We tested `reasoning.effort` and `reasoning.summary`.

Example:

```json
{
  "reasoning": {
    "effort": "medium",
    "summary": "auto"
  }
}
```

Observed behavior:

- OSS/OpenAI-compatible models may expose reasoning in non-structured output via `output_tokens_details.reasoning_tokens` and/or `output[]` items with `type = "reasoning"`.
- With structured output, reasoning visibility was inconsistent.
- Gemini exposed reasoning token details differently.
- Some models/providers reject reasoning controls entirely.

Practical rule we discussed:

- If model name contains `openai` or `oss`, reasoning controls may be sent.
- If request uses an OCI model OCID, look up the model name from model config and apply the same rule.
- Otherwise skip reasoning controls.

For the stable path, we decided:

- use structured output
- do not depend on exposed reasoning
- estimate token consumption from returned usage for now
- optionally run calibration benchmarks later

## Tool / Agent Pattern

OVBot is currently backend-orchestrated. Each phase is a tool-like step.

Typical NL2SQL flow:

```text
Intent Classifier
-> Router
-> Filter Planner
-> Reasoning Planner
-> SQL Draft Builder
-> SQL Lint / Allowlist / Cost Guard
-> SQL Repair if needed
-> SQL Execute
-> Result Summarizer
-> Chart Builder
-> Compose Response
```

Each LLM-backed tool has:

- visible system instruction
- structured output schema
- model/config JSON
- explicit input context
- strict backend validation

For a Python/OpenWebUI demo, the same pattern can be implemented as:

```text
User prompt
-> Python orchestrator calls /responses for intent
-> Python orchestrator calls /responses for SQL draft
-> Python runs local SQL lint/allowlist tool
-> optional /responses call for SQL repair
-> Python executes SQL against demo DB
-> /responses call for summary
-> /responses call for chart spec
-> OpenWebUI displays answer/table/chart
```

## Tool Calling Direction

We discussed moving from backend-driven orchestration to LLM-driven tool calling.

Recommended architecture: hybrid.

The LLM can coordinate bounded tool loops, but the backend remains authoritative for:

- security
- SQL execution
- allowlists
- cost controls
- persistence/logging
- final validation

Example future pattern:

```text
LLM planning agent:
  call sql_builder_tool
  call sql_lint_tool
  call sql_allowlist_tool
  if failed:
    call sql_repair_tool
    call sql_lint_tool again
  return final candidate

Backend:
  enforce hard guards again
  execute only if valid
  log everything
  render UI
```

This gives the LLM flexibility while keeping deterministic safety boundaries outside the model.

## Important Lessons From OVBot

1. Structured output improves reliability, but prompts must not contain conflicting output instructions.
2. Tool schemas and prompt contracts must match exactly.
3. Do not mix old free-form JSON instructions with strict structured schemas.
4. Memory is useful, but explicit context is still needed for follow-up accuracy.
5. Router/planner outputs should be semantic guidance, not final SQL fragments.
6. SQL generation should own SQL expressions.
7. Chart generation should consume clean result shapes and not infer missing temporal fields.
8. Backend validation is still required even with structured output.
9. For monthly data, prefer one combined period field such as `MONTH_DT` or `YEAR_MONTH`; separate `YEAR` and `MONTH` can break chart semantics.
10. For follow-ups, pass previous SQL/result/summary explicitly instead of relying only on conversation memory.

## Suggested Python + OpenWebUI Demo

Demo question:

```text
Show monthly sales for 2025 and the top 5 months.
```

Follow-up:

```text
Make it top 10.
```

Minimum demo phases:

1. Create or reuse OCI conversation.
2. Intent classifier via `responses` + structured output.
3. SQL draft via `responses` + structured output.
4. Python SQL lint/allowlist.
5. SQL execution against a sample DB.
6. Summary via `responses`.
7. Chart spec via `responses` + structured output.
8. Render in OpenWebUI.

Minimum structured schemas:

- `intent_result`
- `sql_result`
- `summary_result`
- `chart_result`

Example SQL result schema:

```json
{
  "type": "json_schema",
  "name": "sql_result",
  "strict": true,
  "schema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["sql"],
    "properties": {
      "sql": {
        "type": "string",
        "minLength": 1
      }
    }
  }
}
```

Recommended SQL prompt rule for the demo:

```text
For monthly/year-month analysis, output one combined period column such as
TRUNC(date_col, 'MM') AS month_dt or TO_CHAR(TRUNC(date_col, 'MM'), 'YYYY-MM') AS year_month.
Do not output separate YEAR and MONTH columns for the same grain unless the user explicitly asks for separate fields.
```

## Demo Takeaway

The demo should show that OCI OpenAI-compatible `responses` and `conversations` can support a practical agentic workflow:

- structured model outputs
- durable conversation memory
- explicit tool context
- safe backend validation
- SQL generation and repair loop
- result summarization
- chart specification

The key architectural choice is to let the model propose and coordinate, while Python/backend code validates, executes, logs, and owns the safety boundary.
