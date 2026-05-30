"""
Pydantic schemas (DTOs) used for request/response validation.
Keeps API shape separate from the ORM layer.
"""
from datetime import date, datetime
# Alias avoids field-name/type-name shadowing in Pydantic V2:
# when a field is named 'date' with default=None, Python assigns date=None
# in the class namespace before the annotation Optional[date] is evaluated,
# so _date keeps the correct type reference.
_date = date
from typing import Any, Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ---------- User settings ----------
class FixedItem(BaseModel):
    name: str
    amount: float
    day: int = 1         # day-of-month when this item is expected (1-31)
    is_income: bool = False


class UserSettings(BaseModel):
    """
    Typed blob stored in User.settings (JSON column).
    Extra keys are allowed so old clients stay forward-compatible.
    """
    currency: str = "CLP"
    locale: str = "es"
    income_target: float = 0.0
    fixed_expenses: list[FixedItem] = []
    fixed_incomes: list[FixedItem] = []
    notify_email: bool = False
    # extra fields (theme, onboarding flags, etc.) pass through untouched
    model_config = ConfigDict(extra="allow")


# ---------- Auth ----------
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    locale: Optional[str] = None  # "es" | "en" | "pt" — used to seed default currency


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: EmailStr
    monthly_budget: float
    settings: dict[str, Any] = {}
    email_token: Optional[str] = None   # used to build the forwarding address
    model_config = ConfigDict(from_attributes=True)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- Accounts ----------
ACCOUNT_TYPES = ("debit", "credit", "savings", "wallet", "cash")


class AccountBase(BaseModel):
    name: str
    bank: str = ""
    type: str  # "debit" | "credit" | "savings" | "wallet" | "cash"
    currency: str = "CLP"
    color: str = "#6366f1"
    icon: str = "card"
    card_image_url: str = ""
    credit_limit: float = 0.0
    anchor_date: Optional[date] = None
    anchor_balance: float = 0.0


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    bank: Optional[str] = None
    type: Optional[str] = None
    currency: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    card_image_url: Optional[str] = None
    credit_limit: Optional[float] = None
    anchor_date: Optional[date] = None
    anchor_balance: Optional[float] = None
    archived: Optional[bool] = None


class AccountOut(AccountBase):
    id: int
    archived: bool
    created_at: datetime
    # Computed fields, populated by the router:
    current_balance: float = 0.0    # for debit/savings/wallet/cash
    current_used: float = 0.0       # for credit (= what you owe)
    available_credit: float = 0.0   # for credit (= limit - used)
    model_config = ConfigDict(from_attributes=True)


# ---------- Transactions ----------
class TransactionBase(BaseModel):
    amount: float
    currency: str = "CLP"
    category: str = "Otros"
    date: date
    merchant: str = ""
    notes: str = ""
    is_income: bool = False
    account_id: Optional[int] = None
    is_transfer: bool = False
    linked_transaction_id: Optional[int] = None


class TransactionCreate(TransactionBase):
    # Items are included here (not as a separate router param) so FastAPI
    # keeps a single body param and avoids the "payload" wrapping bug.
    items: list["ParsedItem"] = []


class TransactionUpdate(BaseModel):
    amount: Optional[float] = None
    category: Optional[str] = None
    date: Optional[_date] = None
    merchant: Optional[str] = None
    notes: Optional[str] = None
    is_income: Optional[bool] = None
    account_id: Optional[int] = None
    is_transfer: Optional[bool] = None
    linked_transaction_id: Optional[int] = None


class TransactionOut(TransactionBase):
    id: int
    image_url: str
    status: str = "confirmed"   # "confirmed" | "pending_review"
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LinkTransferIn(BaseModel):
    a_id: int
    b_id: int


class OwnTransferCreate(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: float
    date: _date
    merchant: str = "Transferencia entre cuentas"
    notes: str = ""
    currency: str = "CLP"


# ---------- OCR / Upload ----------
class ParsedItem(BaseModel):
    name: str
    price: float
    quantity: int = 1


class ParsedReceipt(BaseModel):
    """A single transaction parsed from a screenshot."""
    amount: float
    date: date
    merchant: str
    category: str
    currency: str = "CLP"
    is_income: bool = False
    items: list[ParsedItem] = []
    raw_text: str = ""

    # Extra metadata the parser can surface so the UI can warn the user or
    # auto-link duplicates / transfers.
    cuota_actual: Optional[int] = None        # "1" in "1/6"
    cuotas_total: Optional[int] = None        # "6" in "1/6"
    is_cc_payment: bool = False               # True for "PAGO TARJETA", "ABONO", etc.
    description: str = ""                     # full original description (pre-cleanup)
    dupe_of: Optional[int] = None             # populated by /upload when a match is found


class ParsedUpload(BaseModel):
    """
    Response from /upload. May contain one or many transactions.
    - `transactions` is always a list (length 1 for a plain receipt, N for a
      bank statement table).
    - `image_url` is the stored image so the frontend can attach it on save.
    """
    type: str  # "single" | "list"
    image_url: str
    currency: str = "USD"
    transactions: list[ParsedReceipt]
    raw_text: str = ""
    suggested_account_id: Optional[int] = None  # best guess based on the image


# ---------- Cartola (bank statement PDF) ----------
class CartolaReport(BaseModel):
    """
    Result of uploading a monthly/annual bank statement PDF.

    The frontend shows this as a review screen:
      - Which rows are new (dupe_of is null)?
      - Which rows are already in the DB (dupe_of set)?
      - Does the closing balance in the PDF match the app's computed balance?
        If not → offer to re-anchor the account.
    """
    bank: str = ""
    account_type: str = ""
    last4: str = ""
    currency: str = "CLP"
    period_from: Optional[date] = None
    period_to: Optional[date] = None
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    transactions: list[ParsedReceipt] = []
    new_count: int = 0
    duplicate_count: int = 0
    suggested_account_id: Optional[int] = None
    app_balance: Optional[float] = None     # what LUCAS currently thinks
    drift: Optional[float] = None           # closing_balance - app_balance


class CartolaCommitIn(BaseModel):
    """User confirms a subset of transactions from the cartola to save."""
    account_id: int
    transactions: list[ParsedReceipt]
    reconcile_to_closing_balance: bool = False  # re-anchor after saving
    closing_balance: Optional[float] = None


class CartolaCommitOut(BaseModel):
    saved_count: int
    skipped_count: int
    drift: Optional[float] = None


# ---------- Voice input ----------
class VoiceParseIn(BaseModel):
    """User speaks a sentence; frontend transcribes with Web Speech API."""
    transcript: str
    today: Optional[date] = None     # frontend passes the user's local date


class VoiceParsed(BaseModel):
    """
    Structured transaction extracted from a voice transcript.
    Frontend shows this as a confirmation card before saving.
    """
    action: str                      # "add_expense" | "add_income" | "unclear"
    amount: float = 0.0
    currency: str = "CLP"
    category: str = "Otros"
    merchant: str = ""
    date: date
    is_income: bool = False
    account_hint: str = ""           # e.g. "débito", "CMR", "efectivo"
    suggested_account_id: Optional[int] = None
    confidence: float = 0.0          # 0..1; low values → ask user to repeat
    clarification: str = ""          # when action=="unclear", what to ask back
    transcript: str = ""             # echo of what we heard, for UI


# ---------- Account reconciliation ----------
class ReconcileIn(BaseModel):
    expected_balance: float          # what the bank/app says right now
    as_of_date: Optional[date] = None  # default = today


class ReconcileOut(BaseModel):
    account_id: int
    previous_anchor_balance: float
    previous_anchor_date: Optional[date]
    new_anchor_balance: float
    new_anchor_date: date
    drift: float                     # old-computed - expected


# ---------- People ----------
class PersonCreate(BaseModel):
    name: str
    color: str = "#6366f1"


class PersonOut(BaseModel):
    id: int
    name: str
    color: str
    is_me: bool = False
    model_config = ConfigDict(from_attributes=True)


# ---------- Receipt items / split v2 ----------
class ReceiptItemOut(BaseModel):
    id: int
    name: str
    price: float
    quantity: int
    assigned_to: Optional[int]
    model_config = ConfigDict(from_attributes=True)


class AssignItemIn(BaseModel):
    item_id: int
    person_id: Optional[int]  # None = unassign (legacy single-assign)


# --- v2 multi-assign ---
SPLIT_TYPES = ("equal", "percent", "amount")


class AssigneeIn(BaseModel):
    """One person's share of a single item."""
    person_id: int
    split_type: str = "equal"   # "equal" | "percent" | "amount"
    value: Optional[float] = None  # percent (0-100) or exact amount; None for equal


class ItemAssignIn(BaseModel):
    """Replace ALL assignees for one item with the provided list."""
    item_id: int
    assignees: list[AssigneeIn]  # empty list = unassign everyone


class AssigneeOut(BaseModel):
    person_id: int
    person_name: str
    person_color: str
    split_type: str
    value: Optional[float]
    computed_amount: float  # server-computed share of the item total


class ReceiptItemV2Out(BaseModel):
    id: int
    name: str
    price: float
    quantity: int
    line_total: float
    assignees: list[AssigneeOut]


class SplitPersonResult(BaseModel):
    person_id: int
    person_name: str
    person_color: str
    is_me: bool
    total: float


class SplitResultV2Out(BaseModel):
    transaction_id: int
    total_amount: float
    completion_pct: float
    unassigned_total: float
    items: list[ReceiptItemV2Out]
    people: list[SplitPersonResult]


class SettleIn(BaseModel):
    transaction_id: int
    payer_person_id: Optional[int] = None   # None = "me" (is_me person)
    account_id: Optional[int] = None        # which account to deduct if payer=me
    save_to_lucas: bool = False


class SettleDebtRow(BaseModel):
    person_id: int
    person_name: str
    person_color: str
    is_me: bool
    amount: float   # positive = owes payer, negative = payer owes them


class SettleOut(BaseModel):
    payer_person_id: Optional[int]
    payer_name: str
    my_total: float                   # what "me" personally pays
    debts: list[SettleDebtRow]        # everyone except the payer
    saved_transaction_id: Optional[int] = None


# ---------- Manual split (no receipt) ----------
class ManualSplitIn(BaseModel):
    """Start a split from a manually entered total (no receipt photo needed)."""
    merchant: str = ""
    total_amount: float
    currency: str = "CLP"
    date: date
    account_id: Optional[int] = None


# ---------- Legacy single-assign result (kept for backward compat) ----------
class SplitResultRow(BaseModel):
    person_id: Optional[int]
    person_name: str
    total: float


class SplitResultOut(BaseModel):
    transaction_id: int
    rows: list[SplitResultRow]
    unassigned_total: float
    completion_pct: float


# ---------- Dashboard ----------
class CategorySpend(BaseModel):
    category: str
    total: float


class AccountSummary(BaseModel):
    id: int
    name: str
    bank: str
    type: str                  # debit | credit | savings | wallet | cash
    color: str
    currency: str
    current_balance: float     # for debit/savings/wallet/cash
    current_used: float        # for credit (what you owe)
    credit_limit: float        # for credit
    available_credit: float    # for credit


class DashboardOut(BaseModel):
    month: str                          # "2026-04"
    monthly_budget: float               # legacy spending limit (kept for compat)
    total_spent: float
    total_income: float                 # alias for income_actual
    remaining: float
    daily_safe_spend: float             # legacy (= safe_spend_projected)
    predicted_end_of_month: float       # projected spending EOM
    status: str                         # "good" | "warning" | "danger"
    by_category: list[CategorySpend]
    alerts: list[str]
    accounts: list[AccountSummary] = []
    pending_transfers: int = 0          # count of unlinked credit-card payments
    # ── Variable-income fields ──
    income_actual: float = 0.0          # income confirmed in transactions this month
    income_target: float = 0.0          # user's projected income (settings or historical avg)
    historical_avg_income: float = 0.0  # avg monthly income last 3 months (suggestion)
    safe_spend_actual: float = 0.0      # conservative: (income_actual - fixed - spent) / dr
    safe_spend_projected: float = 0.0   # optimistic:   (variable_budget - spent) / dr
    days_remaining: int = 0
    days_elapsed: int = 0
    days_in_month: int = 30
    # ── Fixed vs variable budget ──
    fixed_expenses: list[dict] = []     # [{"name": str, "amount": float}]
    fixed_incomes: list[dict] = []      # [{"name": str, "amount": float}]
    fixed_total: float = 0.0            # sum of all fixed expenses
    variable_budget: float = 0.0        # income_target - fixed_total
    # ── Review queue ──
    pending_review_count: int = 0       # transactions awaiting user review


# ---------- Transaction review queue ----------
class TransactionReviewAction(BaseModel):
    """Action taken on a pending_review transaction."""
    action: str                          # "confirm" | "skip" | "not_expense" | "pending" | "confirm_cc_payment" | "confirm_own_transfer"
    category: Optional[str] = None       # override category on confirm
    merchant: Optional[str] = None       # override merchant on confirm
    amount: Optional[float] = None       # override amount on confirm
    remember: bool = False               # call remember_correction for future auto-categorization
    account_id: Optional[int] = None         # override the transaction's account on confirm
    target_account_id: Optional[int] = None  # for confirm_cc_payment: which credit card account
    source_account_id: Optional[int] = None  # for confirm_cc_payment: which debit account it came from


# ---------- Email inbound ----------
class EmailInboundPayload(BaseModel):
    """
    Normalized payload from the email webhook (SendGrid Inbound Parse format).
    We extract only the fields we need; the webhook may pass more.
    """
    from_email: str = Field(alias="from")
    to: str
    subject: str = ""
    text: str = ""
    html: str = ""
    model_config = ConfigDict(populate_by_name=True, extra="allow")
