# checkout infrastructure (demo only, never applied).
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

variable "vpc_id" {
  type = string
}

resource "aws_security_group" "checkout" {
  name        = "checkout"
  description = "checkout tasks"
  vpc_id      = var.vpc_id
  tags        = { owner = "payments-team", project = "checkout" }
}
