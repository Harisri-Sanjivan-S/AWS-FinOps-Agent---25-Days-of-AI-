# Video script (3 minutes)

One story: one bad Tuesday, three cost drivers, one proven saving. Record on **Amazon Bedrock**, not offline mode.

## Before recording
- `uv run python scripts/reset_demo.py --yes --github` (fresh runs, no open demo PRs).
- `gh auth status`, `devin auth status`, and the sidebar shows the Bedrock spend well under the cap.
- Rehearse once on Bedrock; check the verdicts against `evals/answer_keys/tuesday.json`.
- Keep the measured results file ready (Results page).
- Browser at 1080p and a comfortable zoom. Sidebar: scenario `tuesday`, investigator `Amazon Bedrock`.

## Shot list

| # | Time | Screen and clicks | Say |
|---|---|---|---|
| 1 | 0:00–0:08 | Title card; label "Planted data shaped like AWS · real Bedrock model" | "Every cost spike explained by the next morning, with proof." |
| 2 | 0:08–0:25 | **Console**: badge "Source: AWS Cost Anomaly Detection · anom-001", $4,500 vs $3,260 (+38%), 5-Tuesday chart | "AWS Cost Anomaly Detection flagged it: Tuesday was $4,500 against an expected $3,260. That alert is our starting point. What it can't tell us is why, whether we meant to spend it, or what to do." |
| 3 | 0:25–0:40 | `docs/diagrams/poc-architecture.png` | "Three roles. Code does the maths and the verdict. The AI, Claude on Amazon Bedrock with Strands Agents, does the detective work. A person approves every fix." |
| 4 | 0:40–0:55 | Console drivers table: $820 / $260 / $130 = 98%, noise greyed. Tick the cost approval and click **Investigate** | "Code measures first: the spike matters, and three drivers explain 98% of it. The AI never touches the arithmetic." |
| 5 | 0:55–1:30 | **Investigation**: live tool trace for the GPU driver (bill → CloudTrail `ct-004` → tags (none) → ticket and exception search (none) → utilization ~1%) | "Now the AI investigates like an engineer. What changed? Eight GPU machines launched from the console on Monday night. Who? Ravi. Approved? No ticket. Used? About 1%. Every answer comes with an evidence ID." |
| 6 | 1:30–1:50 | **Case** TUE-01: Likely waste, 4 of 4 checks, $888.33 spent, $820/day avoidable; open a citation popover | "Code scores the evidence and decides the verdict. The AI explains it, and every claim links to its source. No citation, no claim." |
| 7 | 1:50–2:10 | Case TUE-02 Expected (quiet); TUE-03 Unbudgeted, 2 of 4, gaps "shared gateway, no exact ID match" | "The load test was approved and busy, so it stays quiet. The network charge has incomplete evidence, so it says so and asks the owner instead of guessing." |
| 8 | 2:10–2:35 | **Inbox**, View as `ravi.m`: message card → **Approve stop**. View as `orders-team` → Devin (CLI) → **Approve fix and open PR** → cut to the GitHub PR (green Terraform check, evidence IDs in the description) | "Nothing changes without the owner. The GPUs were made by hand, so the fix is a reversible stop. The network is managed in code, so Devin writes the fix and it arrives as a real pull request. A human merges, never us." |
| 9 | 2:35–2:45 | Back to `ravi.m`: **Next day: check the bill** → "Saving confirmed" | "And we don't claim a saving until the next day's bill proves it." |
| 10 | 2:45–3:00 | **Results** page (accuracy, citations, consistency, seconds and cents per investigation, vs the naive baseline), then `docs/diagrams/production-data-flow.png` | "Tested on seven scenarios, including a prompt-injection attempt. In production the same alert arrives from Cost Anomaly Detection, the agent runs on Bedrock AgentCore, and each planted file becomes a live AWS source. Every cost spike explained by the next morning, with proof." |

## Rules
- Say "planted data shaped like AWS" once; never claim it changes production on its own or guarantees savings.
- Only real output from the recorded Bedrock run. If the PR step fails on camera, use the template fixer and re-record shot 8.
- Devin takes minutes: cut from the click to the finished PR.
