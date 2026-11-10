"""Deterministic fix: a fixed Terraform block. Default for tests and the fallback when Devin is unavailable."""

from __future__ import annotations

from pathlib import Path

from detective.fixers.base import CodeFixer
from detective.models import FixBrief

S3_ENDPOINT_TF = """# Added by Agentic FinOps case {case_id} after owner approval.
# S3 traffic from the private subnets goes through this gateway endpoint (no charge) instead of the shared NAT gateway.
resource "aws_vpc_endpoint" "s3" {{
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${{var.region}}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.private_route_table_ids
  tags              = {{ owner = "{team}", project = "orders", service = "orders-service", finops_case = "{case_id}" }}
}}
"""


class TemplateFixer(CodeFixer):
    name = "template"

    def apply(self, work: Path, brief: FixBrief, brief_path: Path) -> tuple[str, str]:
        target = work / brief.target_folder / "s3_gateway_endpoint.tf"
        target.write_text(S3_ENDPOINT_TF.format(case_id=brief.case_id, team=brief.owner_team))
        summary = f"""## What changed
Adds an S3 gateway VPC endpoint to `{brief.target_folder}/`, attached to the service's private route tables.

## Why (cost)
Since the v2.14 deploy, image and asset pulls from S3 leave the private subnets through the shared NAT gateway, which
charges per GB processed. A gateway endpoint carries S3 traffic at no charge.

## Risk and rollback
Low: S3 requests use the endpoint instead of the NAT. Roll back by reverting this PR.

## How to verify
CI runs `terraform fmt -check` and `terraform validate`. After merge and apply, NAT bytes and the NAT data charge should drop.
"""
        return summary, f"wrote {target.relative_to(work)}"

    def label(self) -> str:
        return "template (deterministic)"
