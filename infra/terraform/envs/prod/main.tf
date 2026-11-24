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
    key          = "pedidos-veloz/prod/terraform.tfstate"
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

  ambiente            = "prod"
  regiao              = var.regiao
  vpc_cidr            = "10.30.0.0/16"
  node_instance_types = ["m6i.large"]
  node_min            = 3
  node_max            = 10
  node_desired        = 3
  db_instance_class   = "db.t4g.medium"
  db_multi_az         = true

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
