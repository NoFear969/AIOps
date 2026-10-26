-- logagg Slice 1 MySQL schema
-- 用法：mysql -u$MYSQL_USER -p$MYSQL_PASSWORD < sql/schema.sql
-- 或：uv run python scripts/bootstrap_db.py

CREATE DATABASE IF NOT EXISTS logagg
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE logagg;

CREATE TABLE IF NOT EXISTS watermarks (
  system        VARCHAR(128)  NOT NULL,
  log_date      CHAR(8)       NOT NULL COMMENT 'YYYYMMDD Asia/Shanghai',
  path          VARCHAR(1024) NOT NULL DEFAULT '',
  remote_size   BIGINT        NOT NULL DEFAULT 0,
  mtime         DOUBLE        NULL,
  byte_offset   BIGINT        NOT NULL DEFAULT 0 COMMENT '下一轮读起点',
  last_partial  LONGBLOB      NULL COMMENT '跨轮次半行 UTF-8 缓冲',
  updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (system, log_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS error_events (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  system          VARCHAR(128)  NOT NULL,
  log_date        CHAR(8)       NOT NULL,
  fingerprint     CHAR(64)      NOT NULL,
  exception_type  VARCHAR(512)  NOT NULL DEFAULT 'Unknown',
  message         TEXT          NOT NULL,
  level           VARCHAR(32)   NOT NULL DEFAULT 'ERROR',
  logger          VARCHAR(255)  NOT NULL DEFAULT '',
  stack_top       TEXT          NULL,
  raw_excerpt     MEDIUMTEXT    NULL,
  event_ts        DATETIME(3)   NULL,
  created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_events_fp (system, log_date, fingerprint),
  KEY idx_events_date (log_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS error_clusters (
  system          VARCHAR(128)  NOT NULL,
  log_date        CHAR(8)       NOT NULL,
  fingerprint     CHAR(64)      NOT NULL,
  count           INT           NOT NULL DEFAULT 1,
  first_seen_at   DATETIME(3)   NULL,
  last_seen_at    DATETIME(3)   NULL,
  exception_type  VARCHAR(512)  NOT NULL DEFAULT 'Unknown',
  sample_message  TEXT          NULL,
  sample_stack    TEXT          NULL,
  updated_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (system, log_date, fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS analyses (
  cause_id          VARCHAR(255)  NOT NULL,
  system            VARCHAR(128)  NOT NULL,
  log_date          CHAR(8)       NOT NULL,
  title             VARCHAR(512)  NOT NULL,
  summary           TEXT          NOT NULL,
  severity          VARCHAR(32)   NOT NULL DEFAULT 'medium',
  confidence        DOUBLE        NOT NULL DEFAULT 0,
  analyzer          VARCHAR(32)   NOT NULL COMMENT 'stub|workbuddy',
  session_id        VARCHAR(255)  NULL,
  analysis_status   VARCHAR(64)   NOT NULL DEFAULT 'ok',
  raw_json          JSON          NULL,
  updated_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (cause_id, log_date),
  KEY idx_analyses_system (system, log_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cluster_causes (
  fingerprint  CHAR(64)      NOT NULL,
  cause_id     VARCHAR(255)  NOT NULL,
  system       VARCHAR(128)  NOT NULL,
  log_date     CHAR(8)       NOT NULL,
  PRIMARY KEY (fingerprint, log_date),
  KEY idx_cc_cause (cause_id, log_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS notifications (
  cause_id     VARCHAR(255)  NOT NULL,
  log_date     CHAR(8)       NOT NULL,
  notify_kind  VARCHAR(32)   NOT NULL COMMENT 'new_cause|worsened',
  status       VARCHAR(64)   NOT NULL COMMENT 'sent|failed|skipped_no_smtp|skipped_stub|…',
  detail       VARCHAR(1000) NOT NULL DEFAULT '',
  created_at   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  sent_at      DATETIME      NULL,
  PRIMARY KEY (cause_id, log_date, notify_kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
