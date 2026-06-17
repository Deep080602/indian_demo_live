terraform {
  required_version = ">= 1.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Query the default VPC
data "aws_vpc" "default" {
  default = true
}

# Query default subnets in the VPC
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Query the latest Ubuntu 24.04 LTS AMI
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Security group to allow traffic on SSH, HTTP, and port 8000
resource "aws_security_group" "algopulse_sg" {
  name        = "algopulse-security-group"
  description = "Allow inbound SSH, HTTP, and port 8000 traffic for AlgoPulse"
  vpc_id      = data.aws_vpc.default.id

  # SSH Access
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow SSH from anywhere"
  }

  # HTTP Web Traffic
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow HTTP web traffic"
  }

  # Flask Direct Access (Alternative port)
  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow Flask direct dashboard access"
  }

  # Outbound rules (required for system updates and dependency downloads)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = {
    Name = "AlgoPulse-SecurityGroup"
  }
}

# Create the EC2 instance
resource "aws_instance" "algopulse_server" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = "t2.micro" # Free Tier eligible
  key_name      = var.key_name

  vpc_security_group_ids = [aws_security_group.algopulse_sg.id]

  # User data to configure the server automatically on startup
  user_data = templatefile("${path.module}/user_data.sh.tpl", {
    github_repo      = var.github_repo
    flask_secret_key = var.flask_secret_key
  })

  tags = {
    Name = "AlgoPulse-Server"
  }

  lifecycle {
    ignore_changes = [ami]
  }
}
