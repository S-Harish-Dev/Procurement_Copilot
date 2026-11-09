# IT Vendor Security Review Checklist

**Effective date:** 2026-01-10
**Owning department:** IT
**Classification:** INTERNAL

## 1. Purpose

This checklist defines how IT assesses the security of a vendor before Acme Corp buys its product or service. It gives requesters a clear list of the evidence vendors must supply, and gives reviewers a consistent standard for approving, conditionally approving or rejecting a vendor.

## 2. Scope

A security review is required before purchase for:

- every SaaS subscription, including free tiers and trials that will hold Acme data;
- any vendor that will connect to Acme's network or systems;
- any vendor that will store, process or have access to Acme confidential data or personal data;
- any new cloud infrastructure provider.

Vendors that supply only physical goods or on-site services with no system or data access do not need a security review.

## 3. Review Process

### 3.1 Requesting a review

The requester submits a security review request in the procurement system with the vendor name, a description of the service, the categories of data involved and the expected number of users. The review must be completed before a purchase request is approved.

### 3.2 Risk tiering

IT assigns a risk tier based on the data and access involved:

| Tier | Typical profile | Target review time |
|---|---|---|
| Low | No Acme data, or public data only | 5 business days |
| Medium | Internal data, or limited employee personal data | 10 business days |
| High | Customer data, confidential data, financial data or privileged system access | 15 business days |

Review time starts when the vendor has returned the completed questionnaire and evidence. High-risk vendors require Legal sign-off in addition to IT approval.

### 3.3 Outcomes

A review results in one of three outcomes: approved, approved with conditions (with a remediation deadline of no more than 90 days), or rejected. Rejected vendors may reapply after remediation.

## 4. Checklist

### 4.1 Assurance

- [ ] Current SOC 2 Type II report or ISO 27001 certificate, issued within the last 12 months.
- [ ] Independent penetration test performed within the last 12 months, with a summary of findings and remediation status.
- [ ] Software handling customer data must support SSO and provide a SOC 2 Type II report (required for High tier).

### 4.2 Identity and access

- [ ] Supports SSO through SAML 2.0 or OpenID Connect.
- [ ] Enforces multi-factor authentication for all administrative accounts.
- [ ] Supports role-based access and removal of users within 24 hours of deprovisioning.

### 4.3 Data protection

- [ ] Encrypts data in transit with TLS 1.2 or higher and at rest with AES-256 or equivalent.
- [ ] States where Acme data is stored and lists all subprocessors.
- [ ] Will sign the Data Processing Addendum if personal data is processed.
- [ ] Commits to notify Acme of a personal data breach within 72 hours.
- [ ] Returns or deletes Acme data within 30 days of contract end.

### 4.4 Operations and resilience

- [ ] Backs up Acme data at least daily, with a recovery point objective of 24 hours or less.
- [ ] Publishes an uptime commitment of at least 99.5% for production services.
- [ ] Remediates critical vulnerabilities within 15 days and high vulnerabilities within 30 days.
- [ ] Keeps security logs for at least 12 months.

### 4.5 Integration

- [ ] API access uses scoped tokens; no shared passwords.
- [ ] Integrations with Acme systems are approved by IT before go-live.

## 5. Re-review

- High-tier vendors are re-reviewed every 12 months.
- Medium and Low tier vendors are re-reviewed every 24 months.
- A new review is required whenever the scope of data or access increases, the vendor reports a security incident affecting Acme data, or the vendor is acquired.

## 6. Responsibilities

- **Employees** (requesters) submit the review request early and coordinate evidence from the vendor.
- **Department managers** confirm the business need and accept any residual risk for Low and Medium tier vendors.
- **IT** owns this checklist, performs reviews, sets risk tiers and tracks conditions to closure.
- **Legal** signs off High-tier vendors and the Data Processing Addendum.
- **Finance** does not issue purchase orders to vendors without an approved security review where one is required.

## 7. Exceptions

An exception to an individual checklist item may be granted by IT where compensating controls exist. Exceptions are documented in the review record, expire after 12 months at the latest, and cannot waive the requirement for a security review itself.

## 8. Related Policies

- Software Licensing and SaaS Policy
- Vendor Data Protection Requirements
- Vendor Onboarding Procedure
- IT Cloud Spend Guidelines
- Acme Procurement Policy
