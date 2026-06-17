variable "aws_region" {
  type        = string
  description = "AWS Region to deploy the resources (Mumbai is ap-south-1)"
  default     = "ap-south-1"
}

variable "key_name" {
  type        = string
  description = "The name of the existing AWS EC2 Key Pair you downloaded (.pem)"
  default     = "algopulse-key"
}

variable "github_repo" {
  type        = string
  description = "The HTTPS URL of your GitHub repository containing the ALGO_PULSE code"
  default     = "https://github.com/YOUR_GITHUB_USERNAME/ALGO_PULSE.git"
}

variable "flask_secret_key" {
  type        = string
  description = "A secure secret key for Flask sessions"
  default     = "something-very-secure-and-random-12345"
}
