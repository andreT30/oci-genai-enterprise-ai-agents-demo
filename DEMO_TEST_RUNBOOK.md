# OCI Enterprise AI Agents Demo Test Runbook

Use this runbook to validate the Streamlit demo before presenting it.

## 1. Start the App

On the VM:

```bash
cd /home/ubuntu/openai
source .env.demo
./run_streamlit_demo.sh restart
./run_streamlit_demo.sh status
```

Watch logs in another terminal:

```bash
./run_streamlit_demo.sh tail
```

Open:

```text
http://<vm-public-ip>:8501
```

Expected:

- Streamlit UI loads.
- Sidebar shows region, project OCID, model, auth, memory file, and log file.
- A conversation exists in the conversation selector.

## 2. Basic OCI Responses Check

Prompt:

```text
In one sentence, what is Oracle APEX?
```

Expected:

- Assistant returns a concise answer.
- Agent trace shows:

```json
{
  "intent": {
    "label": "BASIC_CHAT"
  },
  "plan": {
    "tool_calls": []
  },
  "tool_results": []
}
```

This confirms basic OCI Responses plus OCI conversation id works.

## 3. Short-Term Memory Check

Prompt:

```text
Remember that my demo app name is ApexOps.
```

Follow-up:

```text
What is my demo app name?
```

Expected:

- Assistant recalls `ApexOps`.
- Agent trace shows the same `conversation_id` for both turns.

This demonstrates OCI short-term memory through the same `conversation=<id>`.

## 4. Conversation History Check

In the sidebar:

1. Click **Add conv**.
2. Ask:

```text
What is Oracle Database?
```

3. Use the conversation selector to return to the previous conversation.

Expected:

- Each conversation keeps its own visible chat history.
- Switching conversations shows the correct local transcript.
- Each conversation has its own OCI conversation id after the first message.

Note: local JSON stores the UI history and OCI ids. It is not injected as model
memory.

## 5. Long-Term Memory Check

Prerequisites:

- OCI project has long-term memory enabled.
- `.env.demo` includes:

```bash
OCI_AGENT_ENABLE_LONG_TERM_MEMORY=true
```

Restart:

```bash
./run_streamlit_demo.sh restart
```

In Streamlit:

1. Set **OCI memory subject id** to:

```text
demo-user-1
```

2. Prompt:

```text
Remember that my preferred APEX demo theme is Redwood and my favorite OCI region is Frankfurt.
```

3. Wait briefly for service-side memory extraction.
4. Click **Add conv same subject**.
5. Prompt:

```text
What do you remember about my APEX demo preferences?
```

Expected:

- New conversation has a different `conversation_id`.
- Memory subject remains `demo-user-1`.
- Assistant recalls Redwood and/or Frankfurt.

If it does not recall:

- wait longer and retry
- confirm project long-term memory is enabled
- run diagnostics from the CLI

## 6. Function Calling Check

Prompt:

```text
Who is on call for Payments API?
```

Expected:

- Assistant returns the on-call contact from local JSON data.
- Agent trace shows:

```json
{
  "intent": {
    "label": "FUNCTION_TOOL"
  },
  "plan": {
    "tool_calls": [
      {
        "name": "get_oncall_contacts"
      }
    ]
  }
}
```

Expected answer content:

```text
Ava Patel
commerce-platform
#commerce-platform-sev
```

This demonstrates OCI Function Calling. The model asks for a function call,
Python executes `get_oncall_contacts`, and the function output is sent back with
`previous_response_id`.

## 7. Code Interpreter Check

Prompt:

```text
Run a py code for "Hello World! The time is <<current_time>>"
```

Expected:

- Assistant returns output similar to:

```text
Hello World! The time is 2026-05-07T...
```

- Agent trace shows:

```json
{
  "intent": {
    "label": "CODE_INTERPRETER"
  },
  "plan": {
    "tool_calls": [
      {
        "type": "code_interpreter",
        "container": {
          "type": "auto"
        }
      }
    ]
  }
}
```

This demonstrates OCI Code Interpreter with an auto container.

## 8. Delete Conversation Check

In the sidebar:

1. Select a non-critical test conversation.
2. Click **Delete Conv**.

Expected:

- The selected local conversation entry disappears.
- Another conversation becomes active.

Note: this deletes local UI state, not the OCI service-side conversation resource.

## 9. Clear Session Check

In the sidebar:

1. Click **Clear this session**.

Expected:

- Local conversation list resets.
- A new empty local conversation appears.
- Previous UI transcripts for that session are gone.

Note: this clears the local JSON session only.

## 10. Logs Check

Watch:

```bash
tail -f enterprise_ai_agents_demo/agent_demo.log
```

Expected log entries:

```text
answer_question session_id=demo
creating OCI conversation
routing chat turn to basic_chat
routing chat turn to function_tool
routing chat turn to code_interpreter
responses.create phase=...
```

If errors occur, capture:

- timestamp
- phase
- status code
- request id
- model
- region
- conversation id

## 11. Diagnostics Check

Run:

```bash
source .venv/bin/activate
source .env.demo
python enterprise_ai_agents_demo/oci_enterprise_agent_demo.py --diagnostics
```

Expected:

- Plain response passes.
- Conversation response passes.
- Optional memory metadata tests pass only if the project/model/region supports
  them.

Interpretation:

- Plain response fails: model, auth, project, or region issue.
- Plain passes but conversation fails: OCI Conversations issue.
- Conversation passes but memory metadata fails: long-term or compaction metadata issue.
- Tools fail separately: tool support issue for the selected region/model.

## 12. Streaming Curiosity Check

In Streamlit sidebar:

1. Enable **Stream basic responses**.
2. Prompt:

```text
In one sentence, what is Oracle APEX?
```

Expected:

- If streaming works, text appears incrementally.
- If streaming fails, app logs the streaming error and retries without streaming.

Recommended demo setting:

- Keep streaming off unless it has been tested in your exact region/model.

## 13. Quick Demo Script

Use this sequence for a live walkthrough:

1. Basic chat:

```text
In one sentence, what is Oracle APEX?
```

2. Short-term memory:

```text
Remember that my demo app name is ApexOps.
```

```text
What is my demo app name?
```

3. Function Calling:

```text
Who is on call for Payments API?
```

4. Code Interpreter:

```text
Run a py code for "Hello World! The time is <<current_time>>"
```

5. Conversation management:

- Click **Add conv**
- Ask a new question
- Switch back to the first conversation

6. Optional long-term memory:

- Set subject id
- Ask it to remember a preference
- Click **Add conv same subject**
- Ask what it remembers

## 14. Known Safe Defaults

Recommended `.env.demo` for the live demo:

```bash
OCI_GENAI_REGION=eu-frankfurt-1
OCI_GENAI_PROJECT_OCID=ocid1.generativeaiproject...
OCI_GENAI_MODEL=openai.gpt-oss-120b
OCI_GENAI_AUTH=instance_principal
OCI_AGENT_MAX_RETRIES=1
OCI_AGENT_ENABLE_LONG_TERM_MEMORY=true
```

Keep these off unless specifically testing them:

```bash
OCI_AGENT_ENABLE_SHORT_TERM_COMPACTION=true
```

Keep Streamlit **Stream basic responses** off unless streaming has been verified.
