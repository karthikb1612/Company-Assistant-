import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    company_db: Path
    audit_db: Path
    demo_user_id: str
    model: str = "cohere_chat/command-a-03-2025"

    def __post_init__(self) -> None:
        if self.company_db.resolve() == self.audit_db.resolve():
            raise ValueError("Company and audit databases must be separate files.")


def load_environment() -> None:
    """Load optional local configuration without overriding process environment."""
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"), override=False)


def get_settings() -> Settings:
    default_directory = Path(__file__).resolve().parent / "db"
    return Settings(
        company_db=Path(os.environ.get("COMPANY_DB_PATH", str(default_directory / "company.db")))
        .expanduser()
        .resolve(),
        audit_db=Path(os.environ.get("AUDIT_DB_PATH", str(default_directory / "audit.db")))
        .expanduser()
        .resolve(),
        demo_user_id=os.environ.get("DEMO_USER_ID", "").strip().upper(),
        model=os.environ.get("COHERE_MODEL", "cohere_chat/command-a-03-2025"),
    )
