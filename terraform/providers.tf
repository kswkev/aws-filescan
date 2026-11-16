terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# Targets the local aws-emulator. Credentials and the CA bundle are NOT set here:
# they come from the environment prepared by `. .\scripts\emulator-env.ps1`
# (AWS_SHARED_CREDENTIALS_FILE, AWS_PROFILE, AWS_CA_BUNDLE). The emulator issues
# new keys on every run, so they must never be hard-coded.
provider "aws" {
  region = var.region

  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true

  endpoints {
    s3       = var.emulator_endpoint
    sqs      = var.emulator_endpoint
    lambda   = var.emulator_endpoint
    dynamodb = var.emulator_endpoint
    iam      = var.emulator_endpoint
    sts      = var.emulator_endpoint
    logs     = var.emulator_endpoint
  }
}
