# Procurement System Access Control Standard

**Effective date:** 2026-01-25
**Owning department:** Company-wide
**Classification:** RESTRICTED

## 1. Purpose

This standard defines how access to Acme's procurement system is requested, granted, reviewed and removed. It protects the integrity of purchasing and payment data, enforces separation of duties across the approval chain, and limits the risk of fraud or error from excessive or outdated access.

## 2. Scope

This standard applies to every account in the procurement system, including employee, department manager, Finance and Administrator roles, service accounts used for integrations, and temporary accounts for auditors or contractors. It covers the production environment and any test environment that holds copies of production data.

## 3. Access Rules

### 3.1 Roles

| Role | Typical permissions |
|---|---|
| Employee | Search vendors, view budgets, create and submit purchase requests, view own requests. |
| Department manager | All employee permissions, plus view pending approvals and approve or reject requests for their department. |
| Finance | Approve or reject requests at the Finance level, view vendor bank and tax details, manage payment runs. |
| Administrator | Approve requests at the Administrator level, manage users, roles, vendor status and system configuration. |

Personal data held about vendors, such as bank and tax details, is visible only to Finance and Administrator roles.

### 3.2 Granting access

- Employee access is granted automatically from the HR system when a person joins, based on their department.
- Department manager, Finance and Administrator roles require a written request from the person's department manager and approval by an Administrator who is not the requester.
- Administrator role requests also require approval from the Finance department manager.
- Access is granted on the principle of least privilege. Each person holds only one approval role.

### 3.3 Separation of duties

- No one may approve their own request, and one person may approve only one level of a request. The system enforces these rules automatically and the rule cannot be disabled by configuration.
- Users who can create or edit vendor records, including bank details, may not release payment runs.
- Administrators may not approve purchase requests that they created or that relate to vendors they set up in the last 90 days.

### 3.4 Authentication

- All human users sign in through Acme's single sign-on with multi-factor authentication.
- Administrator sessions time out after 15 minutes of inactivity; other sessions after 30 minutes.
- Service accounts use credentials stored in the corporate secrets vault, rotated at least every 90 days, and may not sign in interactively.

### 3.5 Access reviews

- Administrator access to the procurement system is reviewed quarterly.
- Finance and department manager roles are reviewed every 6 months by the relevant department manager or the Finance department manager.
- Employee access is reconciled monthly against the HR system.
- Reviewers must confirm or remove each account within 10 business days of the review starting. Unconfirmed accounts are suspended automatically.

### 3.6 Removal of access

- Access is removed within 1 business day of a person leaving Acme, and on the same day for involuntary terminations.
- When a person moves department, their old role is removed within 5 business days and the new role is requested in the normal way.
- Accounts with no sign-in for 90 days are disabled automatically.

### 3.7 Privileged activity monitoring

- All Administrator actions, including role changes, vendor status changes and configuration changes, are logged and retained in line with the Procurement Records Retention policy.
- Logs of Administrator actions are reviewed monthly by a Finance team member who does not hold Administrator access.
- Emergency (break-glass) Administrator accounts are sealed, may be used only during a declared incident, and every use is reviewed within 2 business days.

## 4. Responsibilities

- **Employees** protect their credentials and report suspected misuse.
- **Department managers** request appropriate roles and complete access reviews on time.
- **Finance** approves Administrator role requests, reviews privileged activity logs and completes Finance role reviews.
- **Administrators** provision and remove access, run the quarterly review of Administrator access and maintain audit logs.
- **IT** operates single sign-on, multi-factor authentication and the secrets vault.

## 5. Exceptions

Temporary elevated access may be granted for a maximum of 5 business days to resolve a production incident or support an audit. It must be approved by two Administrators, neither of whom is the recipient, and is removed automatically at expiry. Any access found during a review that conflicts with section 3.3 is removed immediately and reported to the Chief Compliance Officer.

## 6. Related Policies

- Purchase Approval Matrix
- Procurement Fraud Investigation Procedure
- Payment Run Procedure
- Procurement Records Retention
- Vendor Onboarding Procedure
- Software Licensing and SaaS Policy
