-- Job-alert emails (LinkedIn, Otta, No Fluff Jobs, ...) as fetched from the alerts inbox.
-- Kept so that improved parsers can re-read old alerts.
CREATE TABLE email_messages (
    id            bigserial PRIMARY KEY,
    mailbox       text NOT NULL,              -- user@host/folder
    uidvalidity   bigint NOT NULL,
    uid           bigint NOT NULL,
    message_id    text,
    from_address  text,
    subject       text,
    received_at   timestamptz,
    body_html     text,
    body_text     text,
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (mailbox, uidvalidity, uid)
);
CREATE INDEX email_messages_received_at ON email_messages (received_at);
