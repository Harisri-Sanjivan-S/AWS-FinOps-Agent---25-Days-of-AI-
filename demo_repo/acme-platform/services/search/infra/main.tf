# search infrastructure (demo only, never applied).
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

resource "aws_cloudwatch_log_group" "search_debug" {
  name              = "/app/search-debug"
  retention_in_days = 7
  tags              = { owner = "search-team", project = "search" }
}
