variable "region" {
  description = "AWS region used by the emulator."
  type        = string
  default     = "us-east-1"
}

variable "emulator_endpoint" {
  description = "Endpoint of the local aws-emulator (HTTPS, own CA)."
  type        = string
  default     = "https://127.0.0.1:9297"
}
