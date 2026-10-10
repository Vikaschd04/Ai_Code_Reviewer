resource "aws_db_instance" "orders" {
  engine                  = "postgres"
  instance_class          = "db.t3.medium"
  backup_retention_period = 7
  multi_az                = true
}
