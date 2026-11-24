terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.95"
    }
  }

  backend "s3" {
    bucket       = "lojaveloz-terraform-state"
    key          = "pedidos-veloz/staging/terraform.tfstate"
    region       = "sa-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.regiao
}

variable "regiao" {
  type    = string
  default = "sa-east-1"
}

module "plataforma" {
  source = "../../modules/plataforma"

  ambiente            = "staging"
  regiao              = var.regiao
  vpc_cidr            = "10.20.0.0/16"
  node_instance_types = ["t3.medium"]
  node_min            = 2
  node_max            = 4
  node_desired        = 2
  db_instance_class   = "db.t4g.micro"
  db_multi_az         = false

  tags = {
    centro_custo = "ecommerce"
  }
}

output "cluster_name" {
  value = module.plataforma.cluster_name
}

output "postgres_endpoint" {
  value = module.plataforma.postgres_endpoint
}

output "kubeconfig_comando" {
  value = module.plataforma.kubeconfig_comando
}
