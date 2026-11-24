variable "ambiente" {
  type = string
  validation {
    condition     = contains(["staging", "prod"], var.ambiente)
    error_message = "ambiente deve ser staging ou prod"
  }
}

variable "regiao" {
  type    = string
  default = "sa-east-1"
}

variable "vpc_cidr" {
  type = string
}

variable "kubernetes_version" {
  type    = string
  default = "1.33"
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t3.medium"]
}

variable "node_min" {
  type = number
}

variable "node_max" {
  type = number
}

variable "node_desired" {
  type = number
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_storage_gb" {
  type    = number
  default = 20
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
