from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Blood Donation API"

    database_url: str = "sqlite:///./blood_donation.db"
    # SQL statement logging. Very noisy — keep off unless debugging queries.
    db_echo: bool = False

    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Google OAuth
    google_client_id: str = ""

    # Gemini AI
    gemini_api_key: str = ""

    # FCM (Firebase Cloud Messaging).
    # Path to the Firebase service-account JSON. Relative paths are resolved
    # against the backend/ directory. Push notifications are simply disabled
    # (not fatal) when this is unset or the file is missing.
    fcm_credentials_path: str = ""

    # CORS — comma-separated list of allowed origins, or "*" for any.
    # "*" is only honoured outside production; it also forces
    # allow_credentials=False because "*" + credentials is invalid per spec.
    cors_origins: str = "*"

    # Environment
    environment: str = "development"

    # ── Email/password accounts ──────────────────────────
    #
    # bcrypt caps input at 72 bytes, so the maximum lives in core.security
    # rather than here.
    password_min_length: int = 8

    # ── Email verification ───────────────────────────────
    email_verification_code_digits: int = 6
    email_verification_ttl_minutes: int = 15
    # Wrong guesses allowed per issued code before it is burned. Keeps a
    # six-digit code from being brute-forceable.
    email_verification_max_attempts: int = 5
    # Minimum gap between two "resend" requests for the same account.
    email_verification_resend_cooldown_seconds: int = 60

    # ── Outbound mail ────────────────────────────────────
    #
    # "auto" uses SMTP when smtp_host is set and the local outbox otherwise, so
    # a development machine with no mail server still exercises the real
    # backend-controlled verification flow. Set MAIL_BACKEND=smtp in production
    # to make a misconfiguration fail loudly instead of silently writing
    # verification codes to disk.
    mail_backend: str = "auto"  # auto | smtp | outbox
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    # Envelope sender. Falls back to smtp_user when unset.
    mail_from: str = ""
    mail_from_name: str = "Blood Donation"
    # Where the outbox backend writes messages. Relative to backend/.
    mail_outbox_dir: str = "dev_outbox"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )


settings = Settings()

def validate_runtime_settings(current: Settings | None = None) -> None:
    """Fail closed on production settings that would weaken authentication/privacy."""
    current = current or settings
    if current.environment.strip().lower() != "production":
        return

    problems: list[str] = []
    secret = (current.secret_key or "").strip()
    if not secret or secret == "change-me-in-production":
        problems.append("SECRET_KEY must be set to a strong, non-default value")

    origins = [o.strip() for o in current.cors_origins.split(",") if o.strip()]
    if not origins or "*" in origins:
        problems.append("CORS_ORIGINS must be an explicit production allow-list")

    backend = (current.mail_backend or "").strip().lower()
    if backend != "smtp":
        problems.append("MAIL_BACKEND must be 'smtp' in production")
    if not (current.smtp_host or "").strip():
        problems.append("SMTP_HOST must be configured in production")

    if problems:
        raise RuntimeError("Invalid production configuration: " + "; ".join(problems))
