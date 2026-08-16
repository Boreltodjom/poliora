# Security Policy

## Supported Release

Security fixes are currently applied to the latest `0.1.x` private-pilot
release. Poliora is alpha software and must be evaluated before use with
sensitive production data.

## Reporting A Vulnerability

Use the repository's private **Report a vulnerability** channel or contact the
person who supplied your pilot build. Do not include credentials, customer
prompts, source code, or an unpatched exploit in a public issue.

Include the Poliora version, operating system, affected command or endpoint,
reproduction steps, and impact. We will acknowledge a private-pilot report
within three business days and coordinate disclosure after a fix is available.

## Local Dashboard Boundary

The dashboard is an unauthenticated local application. It binds to
`127.0.0.1` by default and is not designed to be exposed to a LAN or the public
internet. A hosted Poliora service will require separate authentication,
authorization, tenant isolation, encrypted secrets, audit logs, and rate
limiting.

Provider credentials must be supplied through environment variables or the
provider's supported credential store. They must not be written to source
files, `.poliora`, exported reports, or browser JavaScript.
