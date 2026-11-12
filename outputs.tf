output "vpc_id" {
  description = "ID of the Payment Status Check DEV VPC."
  value       = aws_vpc.psc.id
}