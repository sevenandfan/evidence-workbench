# Security policy

## Supported scope

This repository is a local, read-only portfolio prototype. It is not a hosted multi-user service and does not provide authentication, tenant isolation, or a production SLA.

## Secrets

- Never commit API keys, `.env` files, DPAPI blobs, credentials, private datasets, or generated run directories.
- Configure keys only in the current process environment or reference a Windows current-user DPAPI file outside the repository.
- If a secret is committed, revoke or rotate it first; deleting the visible file is not sufficient because Git history and forks may retain it.

## Data

The public workflow uses licensed public datasets or synthetic examples. Do not upload company, customer, employee, research-participant, or other private data without a separate authorization and privacy review.

## Reporting a problem

Open a GitHub issue without including sensitive data. For a credential exposure, revoke the credential before reporting the affected path and commit.
