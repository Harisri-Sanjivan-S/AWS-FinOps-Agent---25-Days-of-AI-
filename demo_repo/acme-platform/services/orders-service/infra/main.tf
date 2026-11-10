# orders-service infrastructure (demo only, never applied). Runs in the shared VPC's private subnets.
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
  description = "Shared VPC ID (output of platform/network)."
  type        = string
}

variable "private_route_table_ids" {
  description = "Route tables of the private subnets the service runs in (output of platform/network)."
  type        = list(string)
}

resource "aws_security_group" "orders" {
  name        = "orders-service"
  description = "orders-service tasks"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { owner = "orders-team", project = "orders", service = "orders-service" }
}

resource "aws_cloudwatch_log_group" "orders" {
  name              = "/ecs/orders-service"
  retention_in_days = 30
  tags              = { owner = "orders-team", project = "orders" }
}
