"""
SQLAlchemy ORM models. Mirrors the data model described in the spec, with a
few pragmatic additions (Category, Budget, SplitSession) to keep the system
extensible without breaking changes later.
"""
from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Date, ForeignKey, JSON, Boolean, Text
)
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    # Nullable because users who sign in with Google / passwordless email have no password.
    hashed_password = Column(String(255), nullable=True)
    # Which provider the user last used: "password", "email", "google", "facebook", etc.
    auth_provider = Column(String(32), default="password", nullable=False)
    # Free-form settings: currency, locale, notification prefs, etc.
    settings = Column(JSON, default=dict, nullable=False)
    # Monthly budget in minor currency (e.g. cents) OR major (float) — we store major for simplicity.
    monthly_budget = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Unique token used to generate the user's personal forwarding email address.
    # Format: lucas-{email_token}@{EMAIL_DOMAIN}  (configured in .env)
    email_token = Column(String(64), unique=True, nullable=True, index=True)

    transactions = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")
    people = relationship("Person", back_populates="user", cascade="all, delete-orphan")
    categories = relationship("Category", back_populates="user", cascade="all, delete-orphan")
    accounts = relationship("Account", back_populates="user", cascade="all, delete-orphan")


class Account(Base):
    """
    A bank account, credit card, debit card, savings, or e-wallet.

    Two flavours:
      - Debit / checking / wallet → tracks current_balance (positive = money you have)
      - Credit card               → tracks current_used (how much you owe) +
                                    credit_limit (the cap)

    Manual-balance mode (the cheap option): the user sets `anchor_balance` at
    `anchor_date` to a known-correct value, and we compute the live balance as
    anchor + (everything that happened since). When they upload a fresh
    statement, they can re-anchor to keep things accurate.
    """
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(80), nullable=False)               # "CMR Falabella", "Santander Débito"
    bank = Column(String(80), default="", nullable=False)   # "Santander", "Falabella", ...
    type = Column(String(16), nullable=False)               # "debit" | "credit" | "savings" | "wallet" | "cash"
    currency = Column(String(8), default="CLP", nullable=False)

    # Visual
    color = Column(String(16), default="#6366f1", nullable=False)
    icon = Column(String(32), default="card", nullable=False)
    card_image_url = Column(String(512), default="", nullable=True)  # user photo or preset key

    # Credit limit (only relevant for type="credit")
    credit_limit = Column(Float, default=0.0, nullable=False)

    # Manual anchor: at `anchor_date` the balance/used was `anchor_balance`.
    # Live balance = anchor_balance ± (transactions since anchor_date).
    anchor_date = Column(Date, nullable=True)
    anchor_balance = Column(Float, default=0.0, nullable=False)

    archived = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="accounts")
    transactions = relationship("Transaction", back_populates="account")


class Category(Base):
    """User-scoped categories. Seeded with defaults on signup."""
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    color = Column(String(16), default="#6366f1", nullable=False)
    icon = Column(String(32), default="tag", nullable=False)
    monthly_budget = Column(Float, default=0.0, nullable=False)

    user = relationship("User", back_populates="categories")


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Which account this movement belongs to. Nullable for backwards compat
    # with transactions saved before accounts existed.
    account_id = Column(
        Integer,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    amount = Column(Float, nullable=False)             # positive = expense, negative = income
    currency = Column(String(8), default="CLP", nullable=False)
    category = Column(String(80), default="Otros", nullable=False, index=True)
    date = Column(Date, default=date.today, nullable=False, index=True)
    merchant = Column(String(255), default="", nullable=False)
    notes = Column(Text, default="", nullable=False)
    image_url = Column(String(1024), default="", nullable=False)
    raw_ocr = Column(Text, default="", nullable=False)  # full OCR text for debugging
    is_income = Column(Boolean, default=False, nullable=False)

    # Internal-transfer linking. When you pay your credit card from your debit,
    # the same money appears twice — once as -$ on debit, once as +$ on credit.
    # We mark both rows with `is_transfer=True` and point them at each other
    # via `linked_transaction_id` so the dashboard never double-counts them.
    is_transfer = Column(Boolean, default=False, nullable=False, index=True)
    linked_transaction_id = Column(
        Integer,
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Review queue: "confirmed" (normal) or "pending_review" (came from email/auto-import)
    status = Column(String(16), default="confirmed", nullable=False, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="transactions")
    account = relationship("Account", back_populates="transactions")
    items = relationship("ReceiptItem", back_populates="transaction", cascade="all, delete-orphan")
    linked = relationship(
        "Transaction",
        remote_side=[id],
        foreign_keys=[linked_transaction_id],
        post_update=True,
    )


class Person(Base):
    """A person the user splits bills with. is_me=True marks the app user themselves."""
    __tablename__ = "people"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    color = Column(String(16), default="#6366f1", nullable=False)
    # is_me=True → this person represents the app user themselves.
    # At most one per user. Created lazily on first split.
    is_me = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="people")
    assignments = relationship("ItemAssignment", back_populates="person", cascade="all, delete-orphan")


class ReceiptItem(Base):
    __tablename__ = "receipt_items"
    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    price = Column(Float, nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    # Legacy single-assign (kept for backward compat); new code uses ItemAssignment.
    assigned_to = Column(Integer, ForeignKey("people.id", ondelete="SET NULL"), nullable=True)

    transaction = relationship("Transaction", back_populates="items")
    person = relationship("Person", foreign_keys=[assigned_to])
    assignments = relationship("ItemAssignment", back_populates="item", cascade="all, delete-orphan")


class ItemAssignment(Base):
    """
    Maps one receipt item to one participant, with an optional split rule.
    An item can have many ItemAssignments (one per person sharing it).

    split_type:
      "equal"   → divide item_total / N equally among all assignees on this item
      "percent" → this person pays value% of the item total (0–100)
      "amount"  → this person pays exactly value CLP/currency; last person pays remainder
    """
    __tablename__ = "item_assignments"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("receipt_items.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False, index=True)
    split_type = Column(String(16), default="equal", nullable=False)
    # For "percent": 0–100. For "amount": exact amount. NULL for "equal".
    value = Column(Float, nullable=True)

    item = relationship("ReceiptItem", back_populates="assignments")
    person = relationship("Person", back_populates="assignments")


class MerchantCategoryRule(Base):
    """
    A per-user learned rule: "when merchant looks like X, category is Y".
    Written every time the user corrects a transaction's category. Read by the
    categorizer BEFORE falling back to the keyword rules / LLM, so the system
    gets smarter (and cheaper) per user.
    """
    __tablename__ = "merchant_category_rules"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    merchant_key = Column(String(255), nullable=False, index=True)   # normalised (lowercase, trimmed)
    category = Column(String(80), nullable=False)
    hits = Column(Integer, default=1, nullable=False)                # how many times it's been reinforced
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AiUsage(Base):
    """
    One row per LLM call. Lets us answer:
      - How many tokens did user X consume this month?
      - What's our average cost per active user?
      - Which `purpose` (chat / categorize / parse) eats the budget?
    """
    __tablename__ = "ai_usage"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(32), nullable=False)        # "openai" | "anthropic" | "gemini"
    model = Column(String(64), nullable=False)
    purpose = Column(String(32), nullable=False, index=True)  # "chat" | "categorize" | "parse"
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
