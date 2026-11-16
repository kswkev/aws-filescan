variable "region" {
  description = "AWS region used by the emulator."
  type        = string
  default     = "us-east-1"
}

variable "public_s3_base" {
  description = "Prefix that replaces the scheme+host of pre-signed URLs so the browser reaches S3 via the dev proxy. Set to \"\" on real AWS."
  type        = string
  default     = "/s3"
}

variable "url_expiry_seconds" {
  description = "Lifetime of pre-signed upload/download URLs."
  type        = number
  default     = 300
}

variable "emulator_endpoint" {
  description = "Endpoint of the local aws-emulator (HTTPS, own CA)."
  type        = string
  default     = "https://127.0.0.1:9297"
}
