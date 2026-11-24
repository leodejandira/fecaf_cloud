locals {
  nome = "pedidos-veloz-${var.ambiente}"
  azs  = slice(data.aws_availability_zones.disponiveis.names, 0, 3)
  tags = merge(var.tags, {
    projeto  = "pedidos-veloz"
    ambiente = var.ambiente
    gerencia = "terraform"
  })
}

data "aws_availability_zones" "disponiveis" {
  state = "available"
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.21"

  name = local.nome
  cidr = var.vpc_cidr
  azs  = local.azs

  private_subnets  = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets   = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, 48 + i)]
  database_subnets = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, 64 + i)]

  enable_nat_gateway     = true
  single_nat_gateway     = var.ambiente != "prod"
  one_nat_gateway_per_az = var.ambiente == "prod"

  create_database_subnet_group = true

  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1
  }

  tags = local.tags
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.37"

  cluster_name    = local.nome
  cluster_version = var.kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access           = true
  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = { before_compute = true, configuration_values = jsonencode({ enableNetworkPolicy = "true" }) }
    eks-pod-identity-agent = {}
    metrics-server         = {}
  }

  eks_managed_node_groups = {
    apps = {
      instance_types = var.node_instance_types
      min_size       = var.node_min
      max_size       = var.node_max
      desired_size   = var.node_desired
      capacity_type  = var.ambiente == "prod" ? "ON_DEMAND" : "SPOT"
    }
  }

  tags = local.tags
}

resource "aws_security_group" "postgres" {
  name        = "${local.nome}-postgres"
  description = "Acesso ao PostgreSQL a partir dos nodes do EKS"
  vpc_id      = module.vpc.vpc_id
  tags        = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "postgres_eks" {
  security_group_id            = aws_security_group.postgres.id
  referenced_security_group_id = module.eks.node_security_group_id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_db_instance" "postgres" {
  identifier     = local.nome
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_storage_gb
  max_allocated_storage = var.db_storage_gb * 4
  storage_type          = "gp3"
  storage_encrypted     = true

  username                    = "veloz_admin"
  manage_master_user_password = true

  db_subnet_group_name   = module.vpc.database_subnet_group_name
  vpc_security_group_ids = [aws_security_group.postgres.id]
  publicly_accessible    = false
  multi_az               = var.db_multi_az

  backup_retention_period   = var.ambiente == "prod" ? 14 : 3
  deletion_protection       = var.ambiente == "prod"
  skip_final_snapshot       = var.ambiente != "prod"
  final_snapshot_identifier = var.ambiente == "prod" ? "${local.nome}-final" : null

  performance_insights_enabled = true
  auto_minor_version_upgrade   = true

  tags = local.tags
}
