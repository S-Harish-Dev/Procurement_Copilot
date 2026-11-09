# Vendor Data Protection Requirements

**Effective date:** 2026-02-15
**Owning department:** Company-wide
**Classification:** INTERNAL

## 1. Purpose

This policy defines the minimum data protection requirements that apply when Acme Corp shares personal data with a vendor, or allows a vendor to access systems that hold personal data. It protects employees, customers and business contacts, and helps Acme meet its legal obligations.

## 2. Scope

This policy applies to every vendor that collects, stores, hosts, transmits or otherwise processes personal data on behalf of Acme, including SaaS providers, payroll and benefits providers, recruitment agencies, marketing agencies, event organisers and IT service providers. It applies to all departments and to every employee who requests, approves or manages such a vendor.

Personal data means any information relating to an identified or identifiable person, such as names, contact details, employee records, customer account data or event attendee lists.

## 3. Requirements

### 3.1 Before access is granted

- Vendors processing personal data must sign the Data Processing Addendum before access is granted.
- The requester must identify the categories of personal data involved and record them on the purchase request.
- The vendor must complete the IT Vendor Security Review Checklist. All SaaS subscriptions require an IT security review before purchase.
- Software handling customer data must support SSO and provide a SOC 2 Type II report.
- No personal data may be sent to the vendor, including for a trial or proof of concept, until the Data Processing Addendum is signed and the security review is approved.

### 3.2 Data minimisation

Departments must share only the personal data the vendor needs to perform the service. Test environments must use anonymised or synthetic data unless IT approves otherwise in writing.

### 3.3 Security controls

Vendors must, at a minimum:

- encrypt personal data in transit using TLS 1.2 or higher and at rest using AES-256 or equivalent;
- enforce multi-factor authentication for all staff with access to Acme data;
- restrict access to named staff with a business need and review that access at least every 6 months;
- keep audit logs of access to Acme data for at least 12 months.

### 3.4 Subprocessors

Vendors must list their subprocessors in the Data Processing Addendum and give Acme at least 30 days' written notice before adding a new subprocessor. Acme may object, and Legal decides whether the contract continues.

### 3.5 International transfers

Personal data may be transferred outside the country where it was collected only with transfer safeguards approved by Legal, such as standard contractual clauses.

### 3.6 Breach notification

Vendors must notify Acme of a personal data breach within 72 hours. The notice must describe the nature of the breach, the categories and approximate volume of data affected, the likely consequences and the steps taken to contain it. The vendor must cooperate with Acme's investigation and provide updates at least every 24 hours until the incident is closed.

### 3.7 End of contract

Within 30 days of contract end, the vendor must return or securely delete all Acme personal data, at Acme's choice, and provide a written certificate of deletion. The certificate is kept with the contract under Procurement Records Retention.

## 4. Ongoing Monitoring

- Vendors processing sensitive data (health, financial or identity data) are reassessed every 12 months.
- All other vendors processing personal data are reassessed every 24 months or when the scope of processing changes.
- A vendor that fails a reassessment is given a remediation plan; if it is not completed within 60 days, the department manager and Legal decide whether to suspend the vendor. Suspended vendors may not receive new orders.

## 5. Responsibilities

- **Employees** who request a vendor declare whether personal data is involved and do not share data before the addendum is signed.
- **Department managers** confirm that the data shared is necessary and act as the business owner for the vendor relationship.
- **IT** performs security reviews and reassessments and verifies SSO and SOC 2 Type II evidence.
- **Legal** maintains the Data Processing Addendum template, approves transfer safeguards and manages breach response with the vendor.
- **Finance** does not release purchase orders for vendors that are flagged as missing a signed addendum.
- **Administrators** grant system access to vendor accounts only after the requirements in section 3.1 are met.

## 6. Exceptions

Exceptions require written approval from Legal and IT, are limited to 90 days, and must include compensating controls. No exception may waive the Data Processing Addendum or the 72-hour breach notification requirement.

## 7. Related Policies

- Software Licensing and SaaS Policy
- IT Vendor Security Review Checklist
- Vendor Onboarding Procedure
- Contract Management Procedure
- Procurement Records Retention
- Standard Contract Templates Guide
