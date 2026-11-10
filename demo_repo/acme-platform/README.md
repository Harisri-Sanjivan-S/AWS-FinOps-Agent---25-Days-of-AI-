# acme-platform (demo)

**Demo only. Never applied.** A fictional company's monorepo used by the *Agentic FinOps on AWS* proof of concept.
Nothing in this repository is deployed; there is no Terraform state and no AWS account behind it.

Cost-fix pull requests are opened here by the POC after a resource owner approves a fix. A human reviews and merges;
the POC never merges and never runs `terraform apply`.

| Path | What it is | Owner |
|---|---|---|
| `platform/network/` | Shared VPC, private subnets, NAT gateway | platform-team |
| `services/orders-service/` | Orders API (app config + infra) | orders-team |
| `services/checkout/` | Checkout API | payments-team |
| `services/search/` | Search API | search-team |
| `catalog.yaml` | Service catalog: service → path → owning team | platform-team |

CI (`.github/workflows/ci.yml`) runs `terraform fmt -check` and `terraform validate` on every pull request.
