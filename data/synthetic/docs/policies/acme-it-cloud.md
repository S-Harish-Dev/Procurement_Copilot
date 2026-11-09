# IT Cloud Spend Guidelines

**Effective date:** 2026-02-01
**Owning department:** IT
**Classification:** INTERNAL

## 1. Purpose

These guidelines set out how Acme Corp plans, buys, monitors and controls spend on public cloud infrastructure and platform services. They aim to keep cloud costs predictable, make every cloud resource traceable to an owning team, and capture the discounts available for long-running workloads.

## 2. Scope

These guidelines apply to all cloud accounts, subscriptions and projects held in Acme's name with any infrastructure or platform provider, and to every department that runs workloads in them (IT, HR, Finance, Legal, Operations and Marketing). They cover compute, storage, databases, networking, managed services and cloud marketplace purchases. SaaS applications are covered by the Software Licensing and SaaS Policy.

## 3. Rules

### 3.1 Accounts and ownership

- Cloud accounts may be created only by IT, under Acme's central cloud organisation. Accounts opened with a personal or departmental card are not permitted and will be closed.
- Every account has a named owning team and a department manager who is accountable for its spend.
- Each account receives a monthly allocation agreed between IT and the owning department manager at the start of the fiscal year.

### 3.2 Tagging

Every resource must carry three tags: cost centre, owning team and environment (production or non-production). IT reports untagged resources weekly. Resources that remain untagged 7 days after the report are stopped, and deleted after a further 14 days unless the owner responds.

### 3.3 Commitments and discounts

- Reserved instances or savings plans are required for workloads running more than 6 months.
- Commitments are purchased centrally by IT so that discounts are shared across accounts. Individual teams may not buy commitments directly.
- Commitment terms are limited to 1 year by default. A 3-year term may be used only for stable production workloads where Finance has reviewed the business case.
- Commitment purchases are raised as a purchase request and follow the Purchase Approval Matrix based on the total commitment value. Requests above USD 10,000 additionally require Finance approval, and requests above USD 100,000 additionally require approval by an Administrator.

### 3.4 Budgets, alerts and freezes

- Cloud budgets alert at 80% and freeze non-production spend at 100% of the monthly allocation.
- At 80%, the owning team and department manager receive an alert and must review spend within 2 business days.
- At 100%, IT stops non-production resources and blocks the creation of new non-production resources until the next month or until the allocation is increased.
- Production workloads are never stopped automatically. If production spend exceeds the allocation, IT and the department manager agree a remediation plan within 5 business days.

### 3.5 Cost hygiene

- Non-production environments must be scheduled to shut down outside business hours and at weekends unless IT approves an always-on exception.
- Unattached storage volumes and snapshots older than 90 days are deleted after notice to the owning team.
- New workloads must be right-sized using the provider's recommendations after 30 days of running.

### 3.6 Marketplace and new providers

Cloud marketplace purchases count as purchases of goods and services and must be raised as a purchase request in the procurement system. Using a new cloud provider requires vendor onboarding and an IT security review.

## 4. Reporting

IT publishes a monthly cloud cost report by department and account, including commitment coverage, untagged spend and forecast against allocation. Department managers review the report for their accounts. Finance uses it for month-end accruals.

## 5. Responsibilities

- **Employees** tag the resources they create and shut down resources they no longer need.
- **Department managers** own their accounts' spend, respond to 80% alerts and approve allocation changes for their department.
- **IT** creates accounts, buys commitments, runs alerts and freezes, and publishes the monthly report.
- **Finance** reviews 3-year commitments, approves requests above USD 10,000 and tracks cloud spend against budget.
- **Administrators** approve commitment requests above USD 100,000.

## 6. Exceptions

A team may request a temporary lift of the non-production freeze for a business-critical release. The request is approved by the department manager and IT, lasts no longer than 5 business days, and is recorded on the monthly report. Exceptions to the reserved instance or savings plan requirement must be justified in writing to IT.

## 7. Related Policies

- Software Licensing and SaaS Policy
- Purchase Approval Matrix
- Acme Procurement Policy
- IT Vendor Security Review Checklist
- Month-End Close: Accruals for Purchases
