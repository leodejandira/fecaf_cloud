output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "postgres_endpoint" {
  value = aws_db_instance.postgres.address
}

output "postgres_secret_arn" {
  value = aws_db_instance.postgres.master_user_secret[0].secret_arn
}

output "kubeconfig_comando" {
  value = "aws eks update-kubeconfig --region ${var.regiao} --name ${module.eks.cluster_name}"
}
