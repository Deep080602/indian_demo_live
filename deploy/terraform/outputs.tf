output "server_public_ip" {
  description = "The public IP address of your EC2 instance"
  value       = aws_instance.algopulse_server.public_ip
}

output "dashboard_url" {
  description = "The public URL to access your trading dashboard via Nginx reverse proxy"
  value       = "http://${aws_instance.algopulse_server.public_ip}"
}

output "dashboard_direct_url" {
  description = "The public URL to access your trading dashboard directly via Flask"
  value       = "http://${aws_instance.algopulse_server.public_ip}:8000"
}

output "ssh_command" {
  description = "Command to SSH into your server"
  value       = "ssh -i <path-to-your-pem-file> ubuntu@${aws_instance.algopulse_server.public_ip}"
}
