# Shared network for all services (demo only, never applied).
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

resource "aws_vpc" "shared" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_hostnames = true
  tags                 = { Name = "shared-vpc", owner = "platform-team", project = "shared-network" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.shared.id
  cidr_block              = "10.20.0.0/24"
  map_public_ip_on_launch = true
  tags                    = { Name = "shared-public", owner = "platform-team" }
}

resource "aws_subnet" "private" {
  count      = 2
  vpc_id     = aws_vpc.shared.id
  cidr_block = "10.20.${count.index + 10}.0/24"
  tags       = { Name = "shared-private-${count.index}", owner = "platform-team" }
}

resource "aws_internet_gateway" "shared" {
  vpc_id = aws_vpc.shared.id
}

resource "aws_eip" "nat" {
  domain = "vpc"
}

# nat-0c1 in the planted data: shared by every service in the private subnets.
resource "aws_nat_gateway" "shared" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id
  tags          = { Name = "shared-nat", owner = "platform-team", project = "shared-network", shared = "true" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.shared.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.shared.id
  }
  tags = { Name = "shared-private-rt", owner = "platform-team" }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

output "vpc_id" {
  value = aws_vpc.shared.id
}

output "private_route_table_ids" {
  value = [aws_route_table.private.id]
}
