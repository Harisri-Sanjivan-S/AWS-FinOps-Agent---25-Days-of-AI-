# Video script (3 minutes)

One story: one bad Tuesday, three cost drivers, one proven saving. Record with the investigator set to **Amazon Bedrock
(Claude)** (Results and settings), not Dry run. The app no longer shows data-source labels, so the narration carries them.

## Before recording
- `uv run python scripts/reset_demo.py --yes --github` (fresh runs, no open demo PRs).
- `gh auth status`, `devin auth status`, and the sidebar shows the Bedrock spend well under the cap.
- Rehearse once on Bedrock; check the verdicts against `evals/answer_keys/tuesday.json`.
- Keep the measured results file ready (Results page).
- Browser at 1080p and a comfortable zoom. Acting as FinOps team; console scenario `Tuesday`.

## Shot list

| # | Time | Screen and clicks | Say |
|---|---|---|---|
| 1 | 0:00–0:08 | Title card | "Every cost spike explained by the next morning, with proof." |
| 2 | 0:08–0:25 | **Console** (acting as FinOps team): $4,500 vs $3,260 (+38%), 5-Tuesday chart | "AWS Cost Anomaly Detection flagged it: Tuesday was $4,500 against an expected $3,260. That alert is our starting point. What it can't tell us is why, whether we meant to spend it, or what to do." |
| 3 | 0:25–0:40 | `docs/diagrams/poc-architecture.png` | "Three roles. Code does the maths and the verdict. The AI, Claude on Amazon Bedrock with Strands Agents, does the detective work. A person approves every fix." |
| 4 | 0:40–0:55 | Console drivers table: $820 / $260 / $130 = 98%, noise greyed. Tick the cost approval and click **Investigate** | "Code measures first: the spike matters, and three drivers explain 98% of it. The AI never touches the arithmetic." |
| 5 | 0:55–1:30 | **Investigation**: live tool trace for the GPU driver (bill → CloudTrail `ct-004` → tags (none) → ticket and exception search (none) → utilization ~1%) | "Now the AI investigates like an engineer. What changed? Eight GPU machines launched from the console on Monday night. Who? Ravi. Approved? No ticket. Used? About 1%. Every answer comes with an evidence ID." |
| 6 | 1:30–1:50 | **Case** TUE-01: Likely waste, 4 of 4 checks, $888.33 spent, $820/day avoidable; open a citation popover | "Code scores the evidence and decides the verdict. The AI explains it, and every claim links to its source. No citation, no claim." |
| 7 | 1:50–2:05 | Case TUE-02 Expected (quiet); TUE-03 Unbudgeted, 2 of 4, gaps "shared gateway, no exact ID match" | "The load test was approved and busy, so it stays quiet. The network charge has incomplete evidence, so it says so and asks the owner instead of guessing." |
| 8 | 2:05–2:30 | Sidebar **Acting as → Ravi M.**: Inbox shows only his case → message card → **Approve stop**. **Acting as → Orders team** → Devin → **Approve fix and open PR** → cut to the GitHub PR (green Terraform check, evidence IDs in the description) | "Nothing changes without the owner. The GPUs were made by hand, so the fix is a reversible stop. The network is managed in code, so Devin writes the fix and it arrives as a real pull request. A human merges, never us." |
| 9 | 2:30–2:37 | Acting as Ravi M. again: **Next day: check the bill** → "Saving confirmed" | "And we don't claim a saving until the next day's bill proves it." |
| 10 | 2:37–2:45 | **Results** page (accuracy, citations, consistency, seconds and cents per investigation, vs the naive baseline) | "Tested on seven scenarios, including a prompt-injection attempt, with the cost of every investigation measured." |
| 11 | 2:45–3:00 | `docs/diagrams/agentcore-production.png` (reference: `docs/agentcore-production.md`); highlight the AgentCore box, then Gateway, Policy, Observability | "In production, everything stays inside AWS. The alert comes from Cost Anomaly Detection, and the agent runs on Amazon Bedrock AgentCore: Gateway connects it to live billing and CloudTrail data, Identity and Policy guarantee it can never act without approval, and Observability records every step. Every cost spike explained by the next morning, with proof." |

### Why AgentCore in production (for questions after the demo)
- **Runtime:** hosts the agent as a managed, isolated, auto-scaling service instead of a laptop; triggered by the Cost Anomaly Detection alert.
- **Gateway:** live tools (Cost Explorer/CUR via Athena, CloudTrail, CloudWatch, Jira, GitHub) over MCP with authentication; replaces the planted files without changing tool signatures.
- **Identity:** scoped credentials per tool (read-only to billing and CloudTrail; a separate right to open PRs); no keys in code.
- **Policy (Cedar):** hard rules outside the prompt, e.g. "may open pull requests; may never stop a resource without an approval".
- **Observability and Evaluations:** every step traced in CloudWatch; live scoring of real investigations (the production version of our 7-scenario test).
- **Memory:** past investigations, owner preferences and recurring exceptions.
- **Net effect:** data, credentials, model calls, audit trail and the agent's own cost all stay in the customer's AWS account and bill.
- Not built in the POC: the account is on the AWS Free plan (AgentCore needs the Paid plan), and the idea is proven locally; the agent is already UI-independent, so moving is a wrap-and-deploy.

## Rules
- Say "planted data shaped like AWS" once in the narration (the app does not show it); never claim it changes production on its own or guarantees savings.
- Only real output from the recorded Bedrock run. If the PR step fails on camera, use the template fixer and re-record shot 8.
- Devin takes minutes: cut from the click to the finished PR.
