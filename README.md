# OCI Enterprise AI Agents Python Demo

Lightweight Python + Streamlit demo for OCI Generative AI Enterprise Agents using
OCI's OpenAI-compatible API surface.

It demonstrates:

- OCI `responses` for model calls
- OCI `conversations` for short-term service-side memory
- optional OCI `memory_subject_id` for long-term memory across conversations
- OCI Function Calling from the normal chat flow
- OCI Code Interpreter from the normal chat flow
- local file-backed app state for Streamlit history and OCI ids
- no database dependency

The stable general-chat path is intentionally simple: one Responses call with an
OCI conversation id. Clear contact/on-call questions route to Function Calling,
and clear Python/calculation questions route to Code Interpreter.

## Architecture

```text
Streamlit UI
-> local JSON app state
   - visible chat history
   - selected conversation
   - OCI conversation id
   - OCI memory subject id
-> OCI OpenAI-compatible Responses API
   - basic chat
   - function calling
   - code interpreter
-> OCI Conversations API for memory
```

Local JSON is not injected as model memory. It is only the UI/session address
book so the app can resume the correct OCI conversation.

## Key Files

- `enterprise_ai_agents_demo/streamlit_app.py` - Streamlit UI
- `enterprise_ai_agents_demo/oci_enterprise_agent_demo.py` - OCI client, routing, tools, memory state
- `enterprise_ai_agents_demo/knowledge_base.json` - small local data source for the function tool
- `run_streamlit_demo.sh` - start/stop/status/tail helper for the VM
- `.env.example` - environment variable template
- `PROVISION_STREAMLIT_DEMO_ON_OCI_UBUNTU.md` - full OCI VM deployment guide
- `DEMO_TEST_RUNBOOK.md` - validation and live-demo test script

Runtime files created locally:

- `enterprise_ai_agents_demo/agent_memory.json`
- `enterprise_ai_agents_demo/agent_demo.log`
- `streamlit_demo.pid`
- `streamlit_demo.out.log`

## Quick Start

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env.demo`:

```bash
OCI_GENAI_REGION=eu-frankfurt-1
OCI_GENAI_PROJECT_OCID=ocid1.generativeaiproject...
OCI_GENAI_MODEL=openai.gpt-oss-120b
OCI_GENAI_AUTH=instance_principal
OCI_AGENT_MAX_RETRIES=1
OCI_AGENT_ENABLE_LONG_TERM_MEMORY=true
```

Start Streamlit:

```bash
chmod +x run_streamlit_demo.sh
./run_streamlit_demo.sh start
```

Open:

```text
http://<vm-public-ip>:8501
```

Useful commands:

```bash
./run_streamlit_demo.sh status
./run_streamlit_demo.sh tail
./run_streamlit_demo.sh restart
./run_streamlit_demo.sh stop
```

## What To Try

Basic chat:

```text
In one sentence, what is Oracle APEX?
```

Short-term memory:

```text
Remember that my demo app name is ApexOps.
```

```text
What is my demo app name?
```

Function Calling:

```text
Who is on call for Payments API?
```

Code Interpreter:

```text
Run a py code for "Hello World! The time is <<current_time>>"
```

Conversation management in the sidebar:

- **Add conv**
- **Add conv same subject**
- **Delete Conv**
- **Clear this session**

## Memory Model

Short-term memory:

- Uses the same OCI `conversation=<id>` for each turn in a selected conversation.

Long-term memory:

- Enable with `OCI_AGENT_ENABLE_LONG_TERM_MEMORY=true`.
- Uses `metadata.memory_subject_id` when creating new OCI conversations.
- Use **Add conv same subject** to create a new OCI conversation with the same subject id.

Local memory file:

- Stores Streamlit-visible history and OCI ids only.
- It is not sent back to the model as hidden context.

## Diagnostics

Run this when OCI calls fail:

```bash
source .venv/bin/activate
source .env.demo
python enterprise_ai_agents_demo/oci_enterprise_agent_demo.py --diagnostics
```

Interpretation:

- Plain response fails: model, auth, project, or region issue.
- Plain passes but conversation fails: OCI Conversations issue.
- Conversation passes but memory metadata fails: long-term or compaction metadata issue.
- Tool failures are usually model/region/tool-support related.

Logs:

```bash
tail -f enterprise_ai_agents_demo/agent_demo.log
```

## Detailed Docs

- [Provisioning Guide](PROVISION_STREAMLIT_DEMO_ON_OCI_UBUNTU.md)
- [Demo Test Runbook](DEMO_TEST_RUNBOOK.md)
- [Original Handoff Notes](OCI_OPENAI_RESPONSES_CONVERSATIONS_DEMO_HANDOFF.md)

OCI docs:

- [Enterprise AI Agents in OCI Generative AI](https://docs.oracle.com/en-us/iaas/Content/generative-ai/agents.htm)
- [OCI Responses API](https://docs.oracle.com/en-us/iaas/Content/generative-ai/responses-api.htm)
- [Function Calling](https://docs.oracle.com/en-us/iaas/Content/generative-ai/get-started-agents.htm#function-calling)
- [Code Interpreter](https://docs.oracle.com/en-us/iaas/Content/generative-ai/get-started-agents.htm#code-interpreter)
- [OCI IAM-based authentication](https://docs.oracle.com/en-us/iaas/Content/generative-ai/oci-genai-auth.htm)
