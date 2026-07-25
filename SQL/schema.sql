-- Novel-GraphRAG 数据库表结构
-- MySQL 8.0+
-- 迁移时执行: mysql -u root -p novel_graphrag < SQL/schema.sql

CREATE DATABASE IF NOT EXISTS novel_graphrag
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE novel_graphrag;

-- ── 用户表 ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id          VARCHAR(36)     PRIMARY KEY,
    username    VARCHAR(64)     NOT NULL UNIQUE,
    password_hash VARCHAR(256)  NOT NULL,
    role        VARCHAR(16)     NOT NULL DEFAULT 'viewer',
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_active   BOOLEAN         NOT NULL DEFAULT TRUE,

    INDEX idx_username (username),
    INDEX idx_role (role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 审计日志表 ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    id          VARCHAR(48)     PRIMARY KEY,
    action      VARCHAR(32)     NOT NULL,
    username    VARCHAR(64)     NOT NULL DEFAULT '',
    resource    VARCHAR(256)    NOT NULL DEFAULT '',
    detail      TEXT,
    status      VARCHAR(16)     NOT NULL DEFAULT 'success',
    ip          VARCHAR(45)     NOT NULL DEFAULT '',
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_action (action),
    INDEX idx_username (username),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 默认管理员（密码: admin123，需替换为 bcrypt 哈希）──────────────
-- INSERT INTO users (id, username, password_hash, role)
-- VALUES ('u_admin', 'admin', '$2b$12$...', 'admin');
