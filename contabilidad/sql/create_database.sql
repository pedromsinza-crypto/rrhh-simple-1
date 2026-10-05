-- Esquema PostgreSQL del Sistema Contable Multiempresa.
-- Generado desde los modelos SQLAlchemy del proyecto (migraciones hasta 0007_security_retention).
-- Seleccione la base contabilidad_app antes de ejecutar este archivo.
-- Debe ejecutarse una sola vez sobre una base vacia. No copia datos de otra base.
-- El superadministrador se crea al iniciar la aplicacion con BOOTSTRAP_ADMIN_EMAIL y BOOTSTRAP_ADMIN_PASSWORD.

BEGIN;


CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	active BOOLEAN NOT NULL, 
	is_superuser BOOLEAN NOT NULL, 
	auth_version INTEGER NOT NULL, 
	identity_number VARCHAR(12), 
	personal_rif VARCHAR(12), 
	personal_address TEXT, 
	profession VARCHAR(120), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (identity_number), 
	UNIQUE (personal_rif)
)

;

CREATE UNIQUE INDEX ix_users_email ON users (email);


CREATE TABLE system_settings (
	key VARCHAR(80) NOT NULL, 
	value VARCHAR(500) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (key)
)

;


CREATE TABLE access_licenses (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	subscribed_at DATE NOT NULL, 
	expires_at DATE NOT NULL, 
	max_companies INTEGER NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_access_license_status CHECK (status IN ('active','suspended','cancelled')), 
	CONSTRAINT ck_access_license_role CHECK (role IN ('accountant','admin')), 
	CONSTRAINT ck_access_license_company_limit CHECK (max_companies BETWEEN 1 AND 10), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(created_by) REFERENCES users (id)
)

;

CREATE UNIQUE INDEX ix_access_licenses_user_id ON access_licenses (user_id);


CREATE TABLE personal_transactions (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	transaction_date DATE NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	category VARCHAR(60) NOT NULL, 
	description VARCHAR(300) NOT NULL, 
	amount NUMERIC(18, 2) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_personal_transaction_kind CHECK (kind IN ('income','expense')), 
	CONSTRAINT ck_personal_transaction_status CHECK (status IN ('active','archived')), 
	CONSTRAINT ck_personal_transaction_amount CHECK (amount > 0), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)

;

CREATE INDEX ix_personal_transaction_user_date ON personal_transactions (user_id, transaction_date);


CREATE TABLE personal_plan_items (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	planned_date DATE NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	notes VARCHAR(1000) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_personal_plan_status CHECK (status IN ('open','done','archived')), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)

;

CREATE INDEX ix_personal_plan_user_date ON personal_plan_items (user_id, planned_date);


CREATE TABLE accountant_clients (
	id VARCHAR(36) NOT NULL, 
	accountant_user_id VARCHAR(36) NOT NULL, 
	rif VARCHAR(12) NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	phone VARCHAR(40) NOT NULL, 
	address TEXT NOT NULL, 
	notes VARCHAR(1000) NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_accountant_client_rif UNIQUE (accountant_user_id, rif), 
	FOREIGN KEY(accountant_user_id) REFERENCES users (id) ON DELETE CASCADE
)

;

CREATE INDEX ix_accountant_client_owner_name ON accountant_clients (accountant_user_id, name);


CREATE TABLE demo_access_requests (
	id VARCHAR(36) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	full_name VARCHAR(150) NOT NULL, 
	identity_number VARCHAR(12) NOT NULL, 
	rif VARCHAR(12) NOT NULL, 
	address TEXT NOT NULL, 
	profession VARCHAR(120) NOT NULL, 
	verification_hash VARCHAR(64), 
	verification_expires_at TIMESTAMP WITH TIME ZONE, 
	verification_sent_at TIMESTAMP WITH TIME ZONE, 
	verification_attempts INTEGER NOT NULL, 
	email_verified_at TIMESTAMP WITH TIME ZONE, 
	privacy_notice_version VARCHAR(30) NOT NULL, 
	privacy_accepted_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(20) NOT NULL, 
	reviewed_by VARCHAR(36), 
	reviewed_at TIMESTAMP WITH TIME ZONE, 
	review_notes VARCHAR(1000) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_demo_request_status CHECK (status IN ('pending_email','pending_review','approved','rejected')), 
	FOREIGN KEY(reviewed_by) REFERENCES users (id)
)

;

CREATE INDEX ix_demo_request_status_created ON demo_access_requests (status, created_at);

CREATE INDEX ix_demo_request_email_created ON demo_access_requests (email, created_at);


CREATE TABLE password_reset_challenges (
	id VARCHAR(36) NOT NULL, 
	email_digest VARCHAR(64) NOT NULL, 
	user_id VARCHAR(36), 
	token_digest VARCHAR(64), 
	expires_at TIMESTAMP WITH TIME ZONE, 
	used_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)

;

CREATE INDEX ix_password_reset_user_created ON password_reset_challenges (user_id, created_at);

CREATE INDEX ix_password_reset_email_created ON password_reset_challenges (email_digest, created_at);


CREATE TABLE license_payments (
	id VARCHAR(36) NOT NULL, 
	license_id VARCHAR(36) NOT NULL, 
	paid_at DATE NOT NULL, 
	amount NUMERIC(18, 2) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	method VARCHAR(30) NOT NULL, 
	reference VARCHAR(100) NOT NULL, 
	notes TEXT NOT NULL, 
	recorded_by VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_license_payment_positive CHECK (amount > 0), 
	FOREIGN KEY(license_id) REFERENCES access_licenses (id) ON DELETE RESTRICT, 
	FOREIGN KEY(recorded_by) REFERENCES users (id)
)

;

CREATE INDEX ix_license_payment_license_paid ON license_payments (license_id, paid_at);


CREATE TABLE companies (
	id VARCHAR(36) NOT NULL, 
	license_id VARCHAR(36), 
	rif VARCHAR(12) NOT NULL, 
	legal_name VARCHAR(150) NOT NULL, 
	tax_address TEXT NOT NULL, 
	mercantile_registry TEXT NOT NULL, 
	framework VARCHAR(10) NOT NULL, 
	special_taxpayer BOOLEAN NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	contact_email VARCHAR(254) NOT NULL, 
	contact_phone VARCHAR(40) NOT NULL, 
	logo_content_type VARCHAR(30), 
	logo_content BYTEA, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(license_id) REFERENCES access_licenses (id) ON DELETE RESTRICT
)

;

CREATE INDEX ix_companies_license_id ON companies (license_id);

CREATE UNIQUE INDEX ix_companies_rif ON companies (rif);


CREATE TABLE accountant_fees (
	id VARCHAR(36) NOT NULL, 
	accountant_user_id VARCHAR(36) NOT NULL, 
	client_id VARCHAR(36) NOT NULL, 
	reference VARCHAR(30) NOT NULL, 
	service_description VARCHAR(300) NOT NULL, 
	service_period VARCHAR(7) NOT NULL, 
	amount NUMERIC(18, 2) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	issued_at DATE NOT NULL, 
	due_date DATE NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	paid_at DATE, 
	payment_reference VARCHAR(100) NOT NULL, 
	notes VARCHAR(1000) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_accountant_fee_owner_reference UNIQUE (accountant_user_id, reference), 
	CONSTRAINT ck_accountant_fee_amount CHECK (amount > 0), 
	CONSTRAINT ck_accountant_fee_currency CHECK (currency IN ('VES','USD','EUR')), 
	CONSTRAINT ck_accountant_fee_status CHECK (status IN ('open','paid','void')), 
	FOREIGN KEY(accountant_user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(client_id) REFERENCES accountant_clients (id) ON DELETE CASCADE
)

;

CREATE INDEX ix_accountant_fee_owner_status_due ON accountant_fees (accountant_user_id, status, due_date);


CREATE TABLE company_memberships (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_user_company UNIQUE (user_id, company_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;


CREATE TABLE accounts (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	code VARCHAR(30) NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	account_type VARCHAR(20) NOT NULL, 
	parent_id VARCHAR(36), 
	monetary BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	created_by VARCHAR(36), 
	reviewed_by VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_account_company_code UNIQUE (company_id, code), 
	CONSTRAINT ck_account_status CHECK (status IN ('pending','approved')), 
	CONSTRAINT ck_account_type CHECK (account_type IN ('asset','liability','equity','revenue','expense')), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(parent_id) REFERENCES accounts (id), 
	FOREIGN KEY(created_by) REFERENCES users (id), 
	FOREIGN KEY(reviewed_by) REFERENCES users (id)
)

;


CREATE TABLE journals (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	entry_date DATE NOT NULL, 
	description VARCHAR(500) NOT NULL, 
	reference VARCHAR(100) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	posted_at TIMESTAMP WITH TIME ZONE, 
	source_type VARCHAR(30) NOT NULL, 
	source_id VARCHAR(36), 
	reversal_of_id VARCHAR(36), 
	PRIMARY KEY (id), 
	CONSTRAINT ck_journal_status CHECK (status IN ('draft','pending','posted','reversed')), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(created_by) REFERENCES users (id), 
	FOREIGN KEY(reversal_of_id) REFERENCES journals (id)
)

;

CREATE INDEX ix_journal_company_date ON journals (company_id, entry_date);


CREATE TABLE fiscal_periods (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	start_date DATE NOT NULL, 
	end_date DATE NOT NULL, 
	closed BOOLEAN NOT NULL, 
	closed_by VARCHAR(36), 
	closed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_company_period UNIQUE (company_id, start_date, end_date), 
	CONSTRAINT ck_period_dates CHECK (end_date >= start_date), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(closed_by) REFERENCES users (id)
)

;


CREATE TABLE contacts (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	rif VARCHAR(12) NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	phone VARCHAR(40) NOT NULL, 
	address TEXT NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_contact_company_rif UNIQUE (company_id, rif), 
	CONSTRAINT ck_contact_kind CHECK (kind IN ('customer','supplier','both')), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE INDEX ix_contact_company_name ON contacts (company_id, name);


CREATE TABLE audit_events (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36), 
	company_id VARCHAR(36), 
	action VARCHAR(100) NOT NULL, 
	entity_type VARCHAR(100) NOT NULL, 
	entity_id VARCHAR(36) NOT NULL, 
	ip_address VARCHAR(45) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(company_id) REFERENCES companies (id)
)

;

CREATE INDEX ix_audit_company_created ON audit_events (company_id, created_at);


CREATE TABLE company_documents (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	uploaded_by VARCHAR(36) NOT NULL, 
	filename VARCHAR(255) NOT NULL, 
	content_type VARCHAR(100) NOT NULL, 
	category VARCHAR(40) NOT NULL, 
	notes VARCHAR(1000) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	content BYTEA NOT NULL, 
	active BOOLEAN NOT NULL, 
	uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_company_document_size CHECK (size_bytes > 0 AND size_bytes <= 10485760), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(uploaded_by) REFERENCES users (id)
)

;

CREATE INDEX ix_company_document_company_uploaded ON company_documents (company_id, uploaded_at);


CREATE TABLE tax_workpapers (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	period VARCHAR(6) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	snapshot_json TEXT NOT NULL, 
	prepared_by VARCHAR(36) NOT NULL, 
	reviewed_by VARCHAR(36), 
	prepared_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	reviewed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_tax_workpaper_company_period UNIQUE (company_id, period), 
	CONSTRAINT ck_tax_workpaper_status CHECK (status IN ('draft','reviewed')), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(prepared_by) REFERENCES users (id), 
	FOREIGN KEY(reviewed_by) REFERENCES users (id)
)

;

CREATE INDEX ix_tax_workpaper_company_status ON tax_workpapers (company_id, status);


CREATE TABLE journal_lines (
	id VARCHAR(36) NOT NULL, 
	journal_id VARCHAR(36) NOT NULL, 
	account_id VARCHAR(36) NOT NULL, 
	description VARCHAR(300) NOT NULL, 
	debit NUMERIC(18, 2) NOT NULL, 
	credit NUMERIC(18, 2) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_line_nonnegative CHECK (debit >= 0 AND credit >= 0), 
	CONSTRAINT ck_line_one_side CHECK ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)), 
	FOREIGN KEY(journal_id) REFERENCES journals (id) ON DELETE CASCADE, 
	FOREIGN KEY(account_id) REFERENCES accounts (id)
)

;


CREATE TABLE tax_documents (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	direction VARCHAR(10) NOT NULL, 
	document_date DATE NOT NULL, 
	period VARCHAR(6) NOT NULL, 
	counterparty_rif VARCHAR(12) NOT NULL, 
	counterparty_name VARCHAR(150) NOT NULL, 
	contact_id VARCHAR(36), 
	invoice_number VARCHAR(30) NOT NULL, 
	control_number VARCHAR(30) NOT NULL, 
	commercial_description VARCHAR(300) NOT NULL, 
	base_amount NUMERIC(18, 2) NOT NULL, 
	exempt_amount NUMERIC(18, 2) NOT NULL, 
	vat_rate_percent NUMERIC(7, 4) NOT NULL, 
	vat_amount NUMERIC(18, 2) NOT NULL, 
	vat_withholding_percent NUMERIC(7, 4) NOT NULL, 
	vat_withheld NUMERIC(18, 2) NOT NULL, 
	total_amount NUMERIC(18, 2) NOT NULL, 
	payment_currency VARCHAR(3) NOT NULL, 
	payment_amount NUMERIC(18, 2), 
	exchange_rate NUMERIC(18, 6), 
	igtf_applicable BOOLEAN NOT NULL, 
	igtf_rate_percent NUMERIC(7, 4) NOT NULL, 
	igtf_amount NUMERIC(18, 2) NOT NULL, 
	journal_id VARCHAR(36), 
	status VARCHAR(10) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_tax_document_invoice UNIQUE (company_id, direction, counterparty_rif, invoice_number), 
	CONSTRAINT ck_tax_direction CHECK (direction IN ('purchase','sale')), 
	CONSTRAINT ck_tax_document_status CHECK (status IN ('pending','posted','reversed')), 
	CONSTRAINT ck_tax_document_amounts CHECK (base_amount >= 0 AND exempt_amount >= 0 AND vat_amount >= 0), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(contact_id) REFERENCES contacts (id), 
	FOREIGN KEY(journal_id) REFERENCES journals (id)
)

;

CREATE INDEX ix_tax_document_company_date ON tax_documents (company_id, document_date);


CREATE TABLE opening_balances (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	as_of_date DATE NOT NULL, 
	journal_id VARCHAR(36) NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (company_id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(journal_id) REFERENCES journals (id), 
	FOREIGN KEY(created_by) REFERENCES users (id)
)

;


CREATE TABLE professional_fees (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	contact_id VARCHAR(36) NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	reference VARCHAR(30) NOT NULL, 
	service_description VARCHAR(300) NOT NULL, 
	service_period VARCHAR(7) NOT NULL, 
	amount NUMERIC(18, 2) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	issued_at DATE NOT NULL, 
	due_date DATE NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	paid_at DATE, 
	payment_reference VARCHAR(100) NOT NULL, 
	notes VARCHAR(1000) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_professional_fee_company_reference UNIQUE (company_id, reference), 
	CONSTRAINT ck_professional_fee_amount CHECK (amount > 0), 
	CONSTRAINT ck_professional_fee_currency CHECK (currency IN ('VES','USD','EUR')), 
	CONSTRAINT ck_professional_fee_status CHECK (status IN ('open','paid','void')), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(contact_id) REFERENCES contacts (id), 
	FOREIGN KEY(created_by) REFERENCES users (id)
)

;

CREATE INDEX ix_professional_fee_company_status_due ON professional_fees (company_id, status, due_date);


CREATE TABLE document_extractions (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	reviewed_by VARCHAR(36), 
	provider VARCHAR(60) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	fields_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	reviewed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_document_extraction_status CHECK (status IN ('processing','pending_review','approved','rejected','failed')), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(document_id) REFERENCES company_documents (id) ON DELETE CASCADE, 
	FOREIGN KEY(created_by) REFERENCES users (id), 
	FOREIGN KEY(reviewed_by) REFERENCES users (id)
)

;

CREATE INDEX ix_document_extraction_company_created ON document_extractions (company_id, created_at);


CREATE TABLE payments (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	contact_id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	direction VARCHAR(12) NOT NULL, 
	payment_date DATE NOT NULL, 
	reference VARCHAR(100) NOT NULL, 
	method VARCHAR(20) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	amount_ves NUMERIC(18, 2) NOT NULL, 
	foreign_amount NUMERIC(18, 2) NOT NULL, 
	exchange_rate NUMERIC(18, 6), 
	igtf_rate_percent NUMERIC(7, 4) NOT NULL, 
	igtf_amount NUMERIC(18, 2) NOT NULL, 
	cash_account_id VARCHAR(36) NOT NULL, 
	igtf_account_id VARCHAR(36), 
	journal_id VARCHAR(36) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	posted_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_payment_direction CHECK (direction IN ('payable','receivable')), 
	CONSTRAINT ck_payment_status CHECK (status IN ('pending','posted','reversed')), 
	CONSTRAINT ck_payment_amounts CHECK (amount_ves > 0 AND foreign_amount >= 0 AND igtf_amount >= 0), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(contact_id) REFERENCES contacts (id), 
	FOREIGN KEY(document_id) REFERENCES tax_documents (id), 
	FOREIGN KEY(cash_account_id) REFERENCES accounts (id), 
	FOREIGN KEY(igtf_account_id) REFERENCES accounts (id), 
	FOREIGN KEY(journal_id) REFERENCES journals (id), 
	FOREIGN KEY(created_by) REFERENCES users (id)
)

;

CREATE INDEX ix_payment_company_date ON payments (company_id, payment_date);


CREATE TABLE bank_statement_lines (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	bank_account_id VARCHAR(36) NOT NULL, 
	statement_date DATE NOT NULL, 
	external_reference VARCHAR(100) NOT NULL, 
	description VARCHAR(300) NOT NULL, 
	amount NUMERIC(18, 2) NOT NULL, 
	matched_payment_id VARCHAR(36), 
	reconciled_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_bank_line_reference UNIQUE (company_id, bank_account_id, external_reference), 
	CONSTRAINT ck_bank_line_nonzero CHECK (amount <> 0), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(bank_account_id) REFERENCES accounts (id), 
	FOREIGN KEY(matched_payment_id) REFERENCES payments (id)
)

;

CREATE INDEX ix_bank_line_company_date ON bank_statement_lines (company_id, statement_date);
CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

INSERT INTO alembic_version (version_num) VALUES ('0007_security_retention');

COMMIT;
