# Provision the OCI Enterprise AI Agents Streamlit Demo on Ubuntu

This guide provisions a small Ubuntu VM on OCI, grants it access to OCI
Generative AI, installs the demo, and exposes the Streamlit UI.

Official references:

- [Creating an OCI Compute instance](https://docs.oracle.com/iaas/Content/Compute/Tasks/launchinginstance.htm)
- [OCI Generative AI IAM policies](https://docs.oracle.com/en-us/iaas/Content/generative-ai/iam-policies.htm)
- [OCI Generative AI Agents IAM policies](https://docs.oracle.com/en-us/iaas/Content/generative-ai-agents/iam-policies.htm)
- [OCI security rules](https://docs.public.content.oci.oraclecloud.com/en-us/iaas/compute-cloud-at-customer/cmn/network/security-rules.htm)
- [OCI Enterprise AI Agents](https://docs.oracle.com/en-us/iaas/Content/generative-ai/agents.htm)

## 1. Choose Your Values

Decide these before starting:

```text
Compartment:        <your-compartment>
Compartment OCID:   ocid1.compartment...
Region:             us-chicago-1
VM name:            openai-demo
Ubuntu version:     Ubuntu 22.04 or 24.04
Shape:              VM.Standard.E4.Flex, VM.Standard.A1.Flex, or similar
Streamlit port:     8501
Allowed client IP:  <your-laptop-public-ip>/32
GenAI project OCID: ocid1.generativeaiproject...
Model:              openai.gpt-oss-120b
```

Use your actual Generative AI region and model. The demo defaults to
`us-chicago-1` and `openai.gpt-oss-120b`.

## 2. Create or Reuse a VCN

In the OCI Console:

1. Open **Networking > Virtual cloud networks**.
2. If you do not already have a public subnet, use **Start VCN Wizard**.
3. Choose **Create VCN with Internet Connectivity**.
4. Create the VCN in the same compartment as the VM.

The public subnet needs:

- internet gateway
- route rule to the internet gateway
- outbound egress allowed
- ingress for SSH from your IP
- ingress for Streamlit port `8501` from your IP

## 3. Create the Ubuntu Compute Instance

In the OCI Console:

1. Open **Compute > Instances**.
2. Click **Create instance**.
3. Name it `openai-demo`.
4. Select your compartment.
5. Choose an Ubuntu platform image.
6. Choose a small VM shape. For a lightweight demo, 1-2 OCPUs and 8-16 GB RAM is enough.
7. Place it in the public subnet.
8. Assign a public IPv4 address.
9. Add or upload your SSH public key.
10. Click **Create**.

After provisioning, note:

```text
Public IP: <vm-public-ip>
Private IP: <vm-private-ip>
```

SSH in:

```bash
ssh ubuntu@<vm-public-ip>
```

## 4. Open Network Access

Prefer a Network Security Group attached to the instance. A security list also
works.

Add stateful ingress rules:

```text
SSH:
  Source CIDR: <your-laptop-public-ip>/32
  IP Protocol: TCP
  Destination Port: 22

Streamlit:
  Source CIDR: <your-laptop-public-ip>/32
  IP Protocol: TCP
  Destination Port: 8501
```

Avoid `0.0.0.0/0` for Streamlit unless this is a short-lived sandbox demo.

## 5. Enable Instance Principal Auth

Create a dynamic group for the VM.

In **Identity & Security > Domains > Default domain > Dynamic groups**:

1. Create a dynamic group named `openai-demo-instances`.
2. Use a matching rule. The compartment-scoped version is convenient:

```text
ALL {instance.compartment.id = 'ocid1.compartment...'}
```

For a tighter rule, use the instance OCID after creating the VM:

```text
ALL {instance.id = 'ocid1.instance...'}
```

Create IAM policies for the dynamic group.

In **Identity & Security > Policies**, create a policy in the tenancy or relevant
compartment:

```text
allow dynamic-group openai-demo-instances to use generative-ai-family in compartment <your-compartment-name>
allow dynamic-group openai-demo-instances to use genai-agent-family in compartment <your-compartment-name>
```

For a sandbox, `manage` is sometimes easier during setup:

```text
allow dynamic-group openai-demo-instances to manage generative-ai-family in compartment <your-compartment-name>
allow dynamic-group openai-demo-instances to manage genai-agent-family in compartment <your-compartment-name>
```

Use least privilege for shared or production tenancies.

## 6. Create or Identify the Generative AI Project

In OCI Console:

1. Open **Analytics & AI > Generative AI**.
2. Select the target compartment and region.
3. Create or select a Generative AI project.
4. Copy the project OCID.
5. Confirm the model you want to call from the demo.

Set these values on the VM later:

```bash
export OCI_GENAI_REGION=us-chicago-1
export OCI_GENAI_PROJECT_OCID=ocid1.generativeaiproject...
export OCI_GENAI_MODEL=openai.gpt-oss-120b
export OCI_GENAI_AUTH=instance_principal
```

## 7. Bootstrap Ubuntu

On the VM:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git tmux
```

Clone or copy the demo repository:

```bash
git clone <your-repo-url> openai
cd openai
```

If you copied files manually, make sure the layout includes:

```text
enterprise_ai_agents_demo/
  __init__.py
  oci_enterprise_agent_demo.py
  streamlit_app.py
  knowledge_base.json
requirements.txt
README.md
```

Create the Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 8. Configure Environment Variables

Create a small env file:

```bash
cat > .env.demo <<'EOF'
export OCI_GENAI_REGION=us-chicago-1
export OCI_GENAI_PROJECT_OCID=ocid1.generativeaiproject...
export OCI_GENAI_MODEL=openai.gpt-oss-120b
export OCI_GENAI_AUTH=instance_principal
export OCI_AGENT_MEMORY_FILE=/home/ubuntu/openai/enterprise_ai_agents_demo/agent_memory.json
export OCI_AGENT_LOG_FILE=/home/ubuntu/openai/enterprise_ai_agents_demo/agent_demo.log
EOF
```

Load it:

```bash
source .env.demo
```

## 9. Smoke Test OCI Calls

Run the normal demo:

```bash
python enterprise_ai_agents_demo/oci_enterprise_agent_demo.py --reset-memory
```

Optional CLI tool smoke tests:

```bash
python enterprise_ai_agents_demo/oci_enterprise_agent_demo.py --function-tool-demo
python enterprise_ai_agents_demo/oci_enterprise_agent_demo.py --code-interpreter-demo
```

Run diagnostics if live OCI calls fail:

```bash
python enterprise_ai_agents_demo/oci_enterprise_agent_demo.py --diagnostics
```

If this fails with authorization errors, check:

- dynamic group rule matches the instance
- policies are in the right tenancy/compartment
- project OCID belongs to the selected region
- `OCI_GENAI_REGION` matches the endpoint region
- the model name is available in that region/project

## 10. Start Streamlit

The simplest way is to use the included helper script:

```bash
chmod +x run_streamlit_demo.sh
./run_streamlit_demo.sh start
./run_streamlit_demo.sh status
```

The script:

- loads `.env.demo`
- activates `.venv`
- starts Streamlit in the background
- writes a PID file to `streamlit_demo.pid`
- writes Streamlit stdout/stderr to `streamlit_demo.out.log`
- writes app logs to `enterprise_ai_agents_demo/agent_demo.log`

Follow logs:

```bash
./run_streamlit_demo.sh tail
```

Stop the app:

```bash
./run_streamlit_demo.sh stop
```

Manual startup also works.

Start the UI:

```bash
source .venv/bin/activate
source .env.demo
streamlit run enterprise_ai_agents_demo/streamlit_app.py \
  --server.address 0.0.0.0 \
  --server.port 8501
```

For a long-running demo session, use `tmux`:

```bash
tmux new -s oci-agent-demo
source .venv/bin/activate
source .env.demo
streamlit run enterprise_ai_agents_demo/streamlit_app.py \
  --server.address 0.0.0.0 \
  --server.port 8501
```

Detach from tmux with `Ctrl-b`, then `d`.

## 11. Open the UI

From your browser:

```text
http://<vm-public-ip>:8501
```

In the Streamlit sidebar:

1. Confirm the region, project OCID, model, and auth mode.
2. Use `instance_principal` auth.
3. Set a session id, for example `demo`.
4. Use **Add conv**, **Add conv same subject**, **Delete Conv**, and
   **Clear this session** to manage persisted chat history.

Try:

```text
Payments API looks slow in phx. What should I do, and do any policies apply?
```

Then follow up:

```text
Now make that an executive summary and include the owner.
```

Try Function Calling in chat:

```text
Who is on call for Payments API?
```

Try Code Interpreter in chat:

```text
Run a py code for "Hello World! The time is <<current_time>>"
```

## 13. Memory Behavior

The demo is designed to showcase OCI-managed memory, not local app memory.

```text
OCI Conversations API:
  short-term service-side continuity through conversation=<id>
  optional short-term compaction through metadata.short_term_memory_optimization=True
  optional long-term memory sharing through metadata.memory_subject_id=<subject>

Local JSON file:
  session id
  conversation id
  OCI memory subject id
  UI transcript for display
```

Default memory path:

```text
enterprise_ai_agents_demo/agent_memory.json
```

To reset memory from the UI, click **Clear this session**.

To demonstrate short-term memory:

1. Ask an initial question.
2. Ask a follow-up such as `Now make that shorter`.
3. Confirm the app trace shows the same OCI conversation id.

To demonstrate long-term memory:

1. First confirm diagnostics passes for basic conversation calls:
   `python enterprise_ai_agents_demo/oci_enterprise_agent_demo.py --diagnostics`
2. Set `OCI_AGENT_ENABLE_LONG_TERM_MEMORY=true`.
3. Use a stable **OCI memory subject id** in the sidebar, such as `demo-user-1`.
4. Tell the assistant a durable preference or context item.
5. Wait briefly for service-side memory extraction.
6. Click **Add conv same subject**.
7. Ask what it remembers.

The button clears the local UI transcript and creates a new OCI conversation, so
any recalled context is coming from OCI long-term memory. Long-term memory must
be enabled in the OCI Generative AI project. If the new conversation does not
recall prior context, check the project memory settings and give memory
extraction a little more time.

To reset memory from CLI:

```bash
python enterprise_ai_agents_demo/oci_enterprise_agent_demo.py --reset-memory
```

## 14. Optional systemd Service

Create a service file:

```bash
sudo tee /etc/systemd/system/oci-agent-demo.service >/dev/null <<'EOF'
[Unit]
Description=OCI Enterprise AI Agent Streamlit Demo
After=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/openai
Environment=OCI_GENAI_REGION=us-chicago-1
Environment=OCI_GENAI_PROJECT_OCID=ocid1.generativeaiproject...
Environment=OCI_GENAI_MODEL=openai.gpt-oss-120b
Environment=OCI_GENAI_AUTH=instance_principal
Environment=OCI_AGENT_MEMORY_FILE=/home/ubuntu/openai/enterprise_ai_agents_demo/agent_memory.json
ExecStart=/home/ubuntu/openai/.venv/bin/streamlit run enterprise_ai_agents_demo/streamlit_app.py --server.address 0.0.0.0 --server.port 8501
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now oci-agent-demo
sudo systemctl status oci-agent-demo
```

View logs:

```bash
journalctl -u oci-agent-demo -f
```

The app also writes its own rotating log:

```bash
tail -f /home/ubuntu/openai/enterprise_ai_agents_demo/agent_demo.log
```

## 15. Troubleshooting

Import error for `enterprise_ai_agents_demo`:

```bash
cd /home/ubuntu/openai
streamlit run enterprise_ai_agents_demo/streamlit_app.py --server.address 0.0.0.0 --server.port 8501
```

Cannot reach UI:

- confirm Streamlit is listening on `0.0.0.0:8501`
- confirm OCI ingress allows TCP `8501` from your IP
- confirm Ubuntu firewall is not blocking it: `sudo ufw status`

OCI auth fails:

- wait a minute after creating dynamic groups and policies
- confirm the instance principal dynamic group rule matches this VM
- confirm policies include `generative-ai-family`
- confirm region/project/model are correct

Code Interpreter fails:

- confirm the model and region support OCI agent tools
- run `python enterprise_ai_agents_demo/oci_enterprise_agent_demo.py --diagnostics`

Stop the demo:

```bash
pkill -f streamlit
```

Or, if using systemd:

```bash
sudo systemctl stop oci-agent-demo
```
