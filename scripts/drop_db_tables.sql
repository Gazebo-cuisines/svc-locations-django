-- Drop Django auth/session tables only. Keeps loc_* and django_migrations.
-- Run: mysql -h HOST -u USER -p DB_NAME < scripts/drop_db_tables.sql

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS `auth_user_user_permissions`;
DROP TABLE IF EXISTS `auth_user_groups`;
DROP TABLE IF EXISTS `auth_group_permissions`;
DROP TABLE IF EXISTS `django_session`;
DROP TABLE IF EXISTS `auth_user`;
DROP TABLE IF EXISTS `auth_group`;
DROP TABLE IF EXISTS `auth_permission`;
DROP TABLE IF EXISTS `django_content_type`;

SET FOREIGN_KEY_CHECKS = 1;
