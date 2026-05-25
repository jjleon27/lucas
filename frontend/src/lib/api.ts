/**
 * Minimal typed fetch wrapper around the LUCAS backend.
 * Holds the JWT in localStorage and attaches it as Bearer token automatically.
 */

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TOKEN_KEY = "lucas_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  window.localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export function resolveImageUrl(url: string): string {
  if (!url) return "";
  if (url.startsWith("http")) return url;
  return `${API}${url}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, { ...init, headers });
  if (!res.ok) {
    // Stale JWT (e.g. DB was reset but browser still has an old token).
    // Auto-logout and bounce to the landing page so the user can sign in fresh.
    if (res.status === 401 && token && typeof window !== "undefined") {
      clearToken();
      window.location.href = "/";
      throw new Error("session expired");
    }
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// -------- Auth --------
export interface User {
  id: number;
  email: string;
  monthly_budget: number;
  settings: Record<string, unknown>;
}
export interface TokenOut {
  access_token: string;
  token_type: string;
  user: User;
}

function currentLocale(): string {
  if (typeof window === "undefined") return "es";
  return window.localStorage.getItem("lucas_locale") || "es";
}

export async function signup(email: string, password: string): Promise<TokenOut> {
  return request("/auth/signup", {
    method: "POST",
    body: JSON.stringify({ email, password, locale: currentLocale() }),
  });
}

export async function quickLogin(email: string): Promise<TokenOut> {
  return request("/auth/quick", {
    method: "POST",
    body: JSON.stringify({ email, locale: currentLocale() }),
  });
}

export async function googleLogin(credential: string): Promise<TokenOut> {
  return request("/auth/google", { method: "POST", body: JSON.stringify({ credential }) });
}

export async function login(email: string, password: string): Promise<TokenOut> {
  const body = new URLSearchParams({ username: email, password });
  const res = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function me(): Promise<User> {
  return request("/auth/me");
}

export async function updateMe(patch: Partial<User>): Promise<User> {
  return request("/auth/me", { method: "PATCH", body: JSON.stringify(patch) });
}

// -------- Upload --------
export interface ParsedItem {
  name: string;
  price: number;
  quantity: number;
}
export interface ParsedReceipt {
  amount: number;
  date: string;
  merchant: string;
  category: string;
  currency: string;
  is_income: boolean;
  items: ParsedItem[];
  raw_text: string;
  // Extra metadata the parser surfaces:
  cuota_actual?: number | null;      // "1" in "1/6"
  cuotas_total?: number | null;      // "6" in "1/6"
  is_cc_payment?: boolean;           // "PAGO TARJETA" etc. → transfer, not expense
  description?: string;              // full original description pre-cleanup
  dupe_of?: number | null;           // existing tx id that looks like the same movement
}

export interface ParsedUpload {
  type: "single" | "list";
  image_url: string;
  currency: string;
  transactions: ParsedReceipt[];
  raw_text: string;
  suggested_account_id?: number | null;  // best guess account for this image
}

export async function uploadImage(file: File): Promise<ParsedUpload> {
  const form = new FormData();
  form.append("file", file);
  const token = getToken();
  const res = await fetch(`${API}/upload`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// -------- Accounts --------
export type AccountType = "debit" | "credit" | "savings" | "wallet" | "cash";

export interface Account {
  id: number;
  name: string;
  bank: string;
  type: AccountType;
  currency: string;
  color: string;
  icon: string;
  card_image_url: string;
  credit_limit: number;
  anchor_date: string | null;
  anchor_balance: number;
  archived: boolean;
  created_at: string;
  current_balance: number;
  current_used: number;
  available_credit: number;
}

export interface AccountInput {
  name: string;
  bank?: string;
  type: AccountType;
  currency?: string;
  color?: string;
  icon?: string;
  card_image_url?: string;
  credit_limit?: number;
  anchor_date?: string | null;
  anchor_balance?: number;
}

export async function listAccounts(): Promise<Account[]> {
  return request("/accounts");
}
export async function createAccount(body: AccountInput): Promise<Account> {
  return request("/accounts", { method: "POST", body: JSON.stringify(body) });
}
export async function updateAccount(
  id: number, body: Partial<AccountInput> & { archived?: boolean },
): Promise<Account> {
  return request(`/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}
export async function deleteAccount(id: number): Promise<void> {
  await request(`/accounts/${id}`, { method: "DELETE" });
}
export async function uploadCardImage(id: number, file: File): Promise<Account> {
  const form = new FormData();
  form.append("file", file);
  const token = getToken();
  const res = await fetch(`${API}/accounts/${id}/card-image`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
export async function linkTransfer(a_id: number, b_id: number): Promise<void> {
  await request("/accounts/transfer/link", {
    method: "POST", body: JSON.stringify({ a_id, b_id }),
  });
}
export async function unlinkTransfer(tx_id: number): Promise<void> {
  await request(`/accounts/transfer/unlink/${tx_id}`, { method: "POST" });
}

// -------- Transactions --------
export interface Transaction {
  id: number;
  amount: number;
  currency: string;
  category: string;
  date: string;
  merchant: string;
  notes: string;
  image_url: string;
  is_income: boolean;
  account_id: number | null;
  is_transfer: boolean;
  linked_transaction_id: number | null;
  created_at: string;
}

export async function listTransactions(
  opts?: { account_id?: number; pending_transfers?: boolean },
): Promise<Transaction[]> {
  const q = new URLSearchParams();
  if (opts?.account_id != null) q.set("account_id", String(opts.account_id));
  if (opts?.pending_transfers) q.set("pending_transfers", "true");
  const qs = q.toString();
  return request(`/transactions${qs ? "?" + qs : ""}`);
}

export async function suggestTransferMatches(tx_id: number): Promise<Transaction[]> {
  return request(`/accounts/transfer/suggest/${tx_id}`);
}

export async function createTransaction(
  body: Omit<Transaction, "id" | "image_url" | "created_at" | "is_transfer" | "linked_transaction_id"> & {
    image_url?: string;
    items?: ParsedItem[];
    is_transfer?: boolean;  // set true for CC payments so they auto-link
  },
): Promise<Transaction> {
  const { image_url = "", ...tx } = body;
  const query = new URLSearchParams({ image_url });
  return request(`/transactions?${query.toString()}`, {
    method: "POST",
    body: JSON.stringify(tx),
  });
}

export async function updateTransaction(
  id: number,
  patch: Partial<Omit<Transaction, "id" | "image_url" | "created_at">>,
): Promise<Transaction> {
  return request(`/transactions/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function deleteTransaction(id: number): Promise<void> {
  await request(`/transactions/${id}`, { method: "DELETE" });
}

// -------- Dashboard --------
export interface AccountSummary {
  id: number;
  name: string;
  bank: string;
  type: AccountType;
  color: string;
  currency: string;
  current_balance: number;
  current_used: number;
  credit_limit: number;
  available_credit: number;
}

export interface DashboardData {
  month: string;
  monthly_budget: number;
  total_spent: number;
  total_income: number;
  remaining: number;
  daily_safe_spend: number;
  predicted_end_of_month: number;
  status: "good" | "warning" | "danger";
  by_category: { category: string; total: number }[];
  alerts: string[];
  accounts: AccountSummary[];
  pending_transfers: number;
  // Variable-income fields
  income_actual: number;
  income_target: number;
  historical_avg_income: number;
  safe_spend_actual: number;
  safe_spend_projected: number;
  days_remaining: number;
  days_elapsed: number;
  days_in_month: number;
  // Fixed vs variable budget
  fixed_expenses: { name: string; amount: number }[];
  fixed_total: number;
  variable_budget: number;
  // Review queue
  pending_review_count: number;
}

export async function getDashboard(): Promise<DashboardData> {
  return request("/dashboard");
}

// -------- Chat --------
export interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

export async function chat(message: string, history: ChatMsg[]): Promise<{ reply: string }> {
  return request("/chat", { method: "POST", body: JSON.stringify({ message, history }) });
}

export interface ActionOut {
  reply: string;
  action_type: "add_transaction" | "start_split" | "navigate" | "null" | null;
  action_data: Record<string, unknown> | null;
}

export async function chatAction(
  message: string,
  history: ChatMsg[],
): Promise<ActionOut> {
  return request("/chat/action", {
    method: "POST",
    body: JSON.stringify({ message, history }),
  });
}

// -------- Cartola (bank statement PDF) --------
export interface CartolaReport {
  bank: string;
  account_type: string;
  last4: string;
  currency: string;
  period_from: string | null;
  period_to: string | null;
  opening_balance: number | null;
  closing_balance: number | null;
  transactions: ParsedReceipt[];
  new_count: number;
  duplicate_count: number;
  suggested_account_id: number | null;
  app_balance: number | null;
  drift: number | null;
}

export async function uploadCartola(file: File): Promise<CartolaReport> {
  const form = new FormData();
  form.append("file", file);
  const token = getToken();
  const res = await fetch(`${API}/cartola/upload`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface CartolaCommitOut {
  saved_count: number;
  skipped_count: number;
  drift: number | null;
}

export async function commitCartola(body: {
  account_id: number;
  transactions: ParsedReceipt[];
  reconcile_to_closing_balance?: boolean;
  closing_balance?: number | null;
}): Promise<CartolaCommitOut> {
  return request("/cartola/commit", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// -------- Voice --------
export interface VoiceParsed {
  action: "add_expense" | "add_income" | "unclear";
  amount: number;
  currency: string;
  category: string;
  merchant: string;
  date: string;
  is_income: boolean;
  account_hint: string;
  suggested_account_id: number | null;
  confidence: number;
  clarification: string;
  transcript: string;
}

export async function parseVoice(transcript: string, today?: string): Promise<VoiceParsed> {
  return request("/voice/parse", {
    method: "POST",
    body: JSON.stringify({ transcript, today: today || null }),
  });
}

// -------- Reconcile --------
export interface ReconcileOut {
  account_id: number;
  previous_anchor_balance: number;
  previous_anchor_date: string | null;
  new_anchor_balance: number;
  new_anchor_date: string;
  drift: number;
}

export async function reconcileAccount(
  account_id: number,
  expected_balance: number,
  as_of_date?: string,
): Promise<ReconcileOut> {
  return request(`/accounts/${account_id}/reconcile`, {
    method: "POST",
    body: JSON.stringify({ expected_balance, as_of_date: as_of_date || null }),
  });
}

// -------- Split --------
export interface Person {
  id: number;
  name: string;
  color: string;
  is_me?: boolean;
}
export interface SplitResult {
  transaction_id: number;
  rows: { person_id: number; person_name: string; total: number }[];
  unassigned_total: number;
  completion_pct: number;
}

// -------- Split v2 --------
export interface AssigneeIn {
  person_id: number;
  split_type: "equal" | "percent" | "amount";
  value?: number | null;
}

export interface AssigneeOut {
  person_id: number;
  person_name: string;
  person_color: string;
  split_type: "equal" | "percent" | "amount";
  value: number | null;
  computed_amount: number;
}

export interface ReceiptItemV2 {
  id: number;
  name: string;
  price: number;
  quantity: number;
  line_total: number;
  assignees: AssigneeOut[];
}

export interface SplitPersonResult {
  person_id: number;
  person_name: string;
  person_color: string;
  is_me: boolean;
  total: number;
}

export interface SplitResultV2 {
  transaction_id: number;
  total_amount: number;
  completion_pct: number;
  unassigned_total: number;
  items: ReceiptItemV2[];
  people: SplitPersonResult[];
}

export interface SettleDebtRow {
  person_id: number;
  person_name: string;
  person_color: string;
  is_me: boolean;
  amount: number;
}

export interface SettleOut {
  payer_person_id: number | null;
  payer_name: string;
  my_total: number;
  debts: SettleDebtRow[];
  saved_transaction_id: number | null;
}

export interface SplitStartResult {
  transaction_id: number;
  items: ReceiptItemV2[];
}

export async function getMe(): Promise<Person> {
  return request("/split/me");
}
export async function listPeople(): Promise<Person[]> {
  return request("/split/people");
}
export async function createPerson(name: string, color: string): Promise<Person> {
  return request("/split/people", { method: "POST", body: JSON.stringify({ name, color }) });
}
export async function deletePerson(id: number): Promise<void> {
  await request(`/split/people/${id}`, { method: "DELETE" });
}

export async function startSplit(
  transaction_id: number,
  items: ParsedItem[],
): Promise<SplitStartResult> {
  const q = new URLSearchParams({ transaction_id: String(transaction_id) });
  return request(`/split/start?${q.toString()}`, {
    method: "POST",
    body: JSON.stringify(items),
  });
}

export async function startManualSplit(body: {
  merchant?: string;
  total_amount: number;
  currency?: string;
  date: string;
  account_id?: number | null;
}): Promise<SplitStartResult> {
  return request("/split/start-manual", { method: "POST", body: JSON.stringify(body) });
}

export async function assignItemV2(
  item_id: number,
  assignees: AssigneeIn[],
): Promise<ReceiptItemV2> {
  return request("/split/assign-item", {
    method: "POST",
    body: JSON.stringify({ item_id, assignees }),
  });
}

export async function addSplitItem(
  transaction_id: number,
  item: ParsedItem,
): Promise<ReceiptItemV2> {
  const q = new URLSearchParams({ transaction_id: String(transaction_id) });
  return request(`/split/add-item?${q.toString()}`, {
    method: "POST",
    body: JSON.stringify(item),
  });
}

export async function updateSplitItem(
  item_id: number,
  patch: { name?: string; price?: number; quantity?: number },
): Promise<ReceiptItemV2> {
  return request(`/split/items/${item_id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function deleteSplitItem(item_id: number): Promise<void> {
  await request(`/split/items/${item_id}`, { method: "DELETE" });
}

export async function splitResultV2(transaction_id: number): Promise<SplitResultV2> {
  const q = new URLSearchParams({ transaction_id: String(transaction_id) });
  return request(`/split/result?${q.toString()}`);
}

export async function settleSplit(body: {
  transaction_id: number;
  payer_person_id?: number | null;
  account_id?: number | null;
  save_to_lucas?: boolean;
}): Promise<SettleOut> {
  return request("/split/settle", { method: "POST", body: JSON.stringify(body) });
}

// -------- Email / Review queue --------
export interface ForwardingAddressOut {
  email: string;
  token: string;
  instructions: string;
}

export async function getForwardingAddress(): Promise<ForwardingAddressOut> {
  return request("/email/forwarding-address");
}

export async function listPendingTransactions(): Promise<Transaction[]> {
  return request("/email/pending");
}

export async function reviewTransaction(
  tx_id: number,
  payload: {
    action: "confirm" | "skip" | "not_expense" | "pending" | "confirm_cc_payment";
    category?: string;
    merchant?: string;
    amount?: number;
    remember?: boolean;
    target_account_id?: number;
    source_account_id?: number;
  },
): Promise<Transaction> {
  return request(`/email/review/${tx_id}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// Legacy single-assign (kept for backward compat)
export async function assignItem(item_id: number, person_id: number | null) {
  return request("/split/assign", {
    method: "POST",
    body: JSON.stringify({ item_id, person_id }),
  });
}
export async function splitResult(transaction_id: number): Promise<SplitResult> {
  const q = new URLSearchParams({ transaction_id: String(transaction_id) });
  return request(`/split/result?${q.toString()}`);
}
