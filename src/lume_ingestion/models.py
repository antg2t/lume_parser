from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class JsonModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )


class Warning(JsonModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Error(JsonModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class SourceFile(JsonModel):
    path: str
    name: str
    extension: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    short_hash: str = Field(min_length=12, max_length=64)
    media_type: str | None = None


class PageMetrics(JsonModel):
    page_number: int = Field(ge=1)
    characters: int = Field(ge=0)
    words: int = Field(ge=0)
    valid_character_ratio: float = Field(ge=0, le=1)
    image_count: int = Field(ge=0)
    has_images: bool
    has_useful_text: bool


class ArtifactPaths(JsonModel):
    raw_json: str | None = None
    normalized_json: str | None = None
    result_json: str | None = None
    page_texts: list[str] = Field(default_factory=list)


class IngestionResult(JsonModel):
    schema_version: Literal["1.0"] = "1.0"
    success: bool
    source: SourceFile | None = None
    source_format: str | None = None
    document_type: str | None = None
    parser: str | None = None
    parser_version: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    # The total duration is kept for backwards compatibility.  Stage times
    # make batch/regression reports useful without making a performance
    # promise before a baseline has been established.
    extraction_duration_ms: int = Field(default=0, ge=0)
    normalization_duration_ms: int = Field(default=0, ge=0)
    validation_duration_ms: int = Field(default=0, ge=0)
    classification: Literal["textual", "hybrid", "probable_image"] | None = None
    requires_ocr: bool = False
    outputs: ArtifactPaths = Field(default_factory=ArtifactPaths)
    warnings: list[Warning] = Field(default_factory=list)
    errors: list[Error] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class FieldProvenance(JsonModel):
    xpath: str | None = None
    derived_from: list[str] = Field(default_factory=list)
    transformation: str | None = None
    justification: str | None = None
    # XML uses XPath.  A rendered DANFSe has no element tree, so its
    # equivalent evidence is the page region and the exact rendered text.
    page_number: int | None = Field(default=None, ge=1)
    region: dict[str, float] | None = None
    excerpt: str | None = None


class FiscalAddress(JsonModel):
    municipality_code: str | None = None
    street: str | None = None
    number: str | None = None
    complement: str | None = None
    district: str | None = None
    state: str | None = None
    postal_code: str | None = None


class FiscalParty(JsonModel):
    tax_id: str | None = None
    tax_id_type: Literal["CNPJ", "CPF", "NIF", "unknown"] | None = None
    name: str | None = None
    trade_name: str | None = None
    municipal_registration: str | None = None
    phone: str | None = None
    email: str | None = None
    address: FiscalAddress | None = None


class FiscalDps(JsonModel):
    id: str | None = None
    number: str | None = None
    series: str | None = None


class FiscalLocations(JsonModel):
    issue_code: str | None = None
    issue_name: str | None = None
    service_code: str | None = None
    service_name: str | None = None
    incidence_code: str | None = None
    incidence_name: str | None = None


class FiscalService(JsonModel):
    national_tax_code: str | None = None
    municipal_tax_code: str | None = None
    nbs_code: str | None = None
    national_tax_description: str | None = None
    municipal_tax_description: str | None = None
    nbs_description: str | None = None
    description: str | None = None
    contributor_internal_code: str | None = None


class FiscalAmounts(JsonModel):
    currency: Literal["BRL"] = "BRL"
    gross: Decimal | None = None
    taxable_base: Decimal | None = None
    rate: Decimal | None = None
    iss: Decimal | None = None
    retained_total: Decimal | None = None
    unconditional_discount: Decimal | None = None
    conditional_discount: Decimal | None = None
    net: Decimal | None = None


class FiscalRetentions(JsonModel):
    iss_type: str | None = None
    pis_cofins_type: str | None = None
    pis: Decimal | None = None
    cofins: Decimal | None = None
    csll: Decimal | None = None
    inss: Decimal | None = None
    ir: Decimal | None = None


class IbsCbs(JsonModel):
    incidence_location_code: str | None = None
    incidence_location_name: str | None = None
    taxable_base: Decimal | None = None
    ibs_state_rate: Decimal | None = None
    ibs_state_effective_rate: Decimal | None = None
    ibs_municipal_rate: Decimal | None = None
    ibs_municipal_effective_rate: Decimal | None = None
    cbs_rate: Decimal | None = None
    cbs_effective_rate: Decimal | None = None
    invoice_total: Decimal | None = None
    ibs_total: Decimal | None = None
    ibs_state: Decimal | None = None
    ibs_municipal: Decimal | None = None
    cbs: Decimal | None = None
    declaration: dict[str, Any] | None = None


class FiscalTaxes(JsonModel):
    iss_taxation: str | None = None
    pis_cofins_status: str | None = None
    federal_total: Decimal | None = None
    state_total: Decimal | None = None
    municipal_total: Decimal | None = None
    ibs_cbs: IbsCbs | None = None


class FiscalMetadata(JsonModel):
    nfse_id: str | None = None
    application_version: str | None = None
    generation_environment: str | None = None
    emission_type: str | None = None
    processing_datetime: datetime | None = None
    generated_document_number: str | None = None
    signature_reference: str | None = None
    digest_value: str | None = None
    signature_value: str | None = None
    certificate: str | None = None
    unknown_groups: list[str] = Field(default_factory=list)


class FiscalDocument(JsonModel):
    document_type: Literal["nfse"] = "nfse"
    layout: Literal["national"] = "national"
    # A rendered DANFSe can omit a printed field.  Keep the common semantic
    # contract explicit with nulls instead of fabricating a value; the parser
    # emits a structured warning with the missing visual evidence.
    version: str | None = None
    key: str | None = None
    number: str | None = None
    dps: FiscalDps
    environment: str | None = None
    status: str | None = None
    issue_datetime: datetime | None = None
    competence_date: date | None = None
    provider: FiscalParty
    customer: FiscalParty
    intermediary: FiscalParty | None = None
    locations: FiscalLocations
    service: FiscalService
    amounts: FiscalAmounts
    retentions: FiscalRetentions
    taxes: FiscalTaxes
    metadata: FiscalMetadata
    provenance: dict[str, FieldProvenance]


class CashLedgerOrigin(JsonModel):
    """Localizacao verificavel de um lancamento no documento de origem."""

    source_format: Literal["xlsx", "pdf"]
    sheet: str | None = None
    row_number: int | None = Field(default=None, ge=1)
    cell_refs: list[str] = Field(default_factory=list)
    page_number: int | None = Field(default=None, ge=1)
    region: dict[str, float] | None = None
    excerpt: str | None = None


class CashLedgerEntry(JsonModel):
    date: date
    issue_date: date | None = None
    document: str | None = None
    counterparty: str | None = None
    notes: str | None = None
    inflow: Decimal
    outflow: Decimal
    balance: Decimal
    origin: CashLedgerOrigin


class CashLedger(JsonModel):
    """Lancamentos ordenados de uma unica conta ou centro de caixa."""

    company: str | None = None
    account: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    initial_balance: Decimal
    final_balance: Decimal
    entries: list[CashLedgerEntry] = Field(default_factory=list)


class CashLedgerCollection(JsonModel):
    """Um arquivo pode imprimir mais de uma conta no mesmo controle."""

    document_type: Literal["cash_ledger"] = "cash_ledger"
    currency: Literal["BRL"] = "BRL"
    ledgers: list[CashLedger] = Field(default_factory=list)


class SpreadsheetOrigin(JsonModel):
    """Localizacao de uma linha importada de uma planilha."""

    source_format: Literal["xlsx"] = "xlsx"
    sheet: str
    row_number: int = Field(ge=1)
    cell_refs: list[str] = Field(default_factory=list)


class ChartOfAccountsEntry(JsonModel):
    code: str
    description: str
    reduced_code: str | None = None
    origin: SpreadsheetOrigin


class ChartOfAccounts(JsonModel):
    document_type: Literal["chart_of_accounts"] = "chart_of_accounts"
    accounts: list[ChartOfAccountsEntry] = Field(default_factory=list)


class AccountingHistoryEntry(JsonModel):
    entry_number: str | None = None
    date: date
    debit_account: str
    credit_account: str
    amount: Decimal
    standard_history: str | None = None
    complement: str | None = None
    debit_cost_center: str | None = None
    credit_cost_center: str | None = None
    tax_id: str | None = None
    origin: SpreadsheetOrigin


class AccountingHistory(JsonModel):
    document_type: Literal["accounting_history"] = "accounting_history"
    entries: list[AccountingHistoryEntry] = Field(default_factory=list)


class BankStatementOrigin(JsonModel):
    """Evidencia espacial de uma linha impressa no extrato bancario."""

    source_format: Literal["pdf", "xlsx", "xls"] = "pdf"
    page_number: int | None = Field(default=None, ge=1)
    sheet: str | None = None
    row_number: int | None = Field(default=None, ge=1)
    cell_refs: list[str] = Field(default_factory=list)
    region: dict[str, float]
    excerpt: str


class BankStatementTransaction(JsonModel):
    date: date
    description: str | None = None
    counterparty: str | None = None
    counterparty_tax_id: str | None = None
    document: str | None = None
    amount: Decimal
    # This is deliberately mechanical: a non-negative printed amount is a
    # credit and a negative printed amount is a debit.  No accounting meaning
    # is inferred from the description.
    transaction_type: Literal["credit", "debit"]
    balance: Decimal | None = None
    origin: BankStatementOrigin
    continuation_origins: list[BankStatementOrigin] = Field(default_factory=list)


class BankStatementDailyBalance(JsonModel):
    date: date
    balance: Decimal
    origin: BankStatementOrigin


class BankStatement(JsonModel):
    """Extrato reconciliavel de uma unica conta bancaria."""

    document_type: Literal["bank_statement"] = "bank_statement"
    bank: str
    layout: str
    branch: str | None = None
    account: str | None = None
    holder: str | None = None
    holder_tax_id: str | None = None
    currency: Literal["BRL"] = "BRL"
    period_start: date | None = None
    period_end: date | None = None
    initial_balance: Decimal | None = None
    final_balance: Decimal | None = None
    total_balance: Decimal | None = None
    account_limit: Decimal | None = None
    limit_used: Decimal | None = None
    limit_available: Decimal | None = None
    transactions: list[BankStatementTransaction] = Field(default_factory=list)
    daily_balances: list[BankStatementDailyBalance] = Field(default_factory=list)
