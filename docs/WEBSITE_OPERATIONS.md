# Website, Feedback And Analytics

The public site is static, served by the existing Cloudflare Pages project
`poliora`. Production deploys from `main`. It is separate from the local desktop
workspace. Do not add a login gate just to count users.

## Activate The Support Address

The public support page prepares drafts addressed to `support@poliora.com`.
Until the receiving address has been configured and tested, its email action is
hidden and a visible notice directs non-sensitive feedback to GitHub.

1. In Cloudflare, open Compute > Email Service > Email Routing. Select Onboard
   Domain and choose `poliora.com`. Some dashboard versions also show Email
   Routing under the domain's navigation.
2. Add and verify a destination inbox you control, such as your existing Gmail.
3. Review existing mail records before enabling routing. Do not replace records
   used by an existing mailbox provider.
4. Create the custom address `support` and route it to the verified destination.
5. Send a test message from a different email address and verify its arrival.
6. Only after delivery works, change `data-email-ready="false"` to `"true"` on
   the form in `site/support.html` and deploy.

Cloudflare Email Routing receives and forwards email; it is not a hosted inbox
and does not provide an SMTP server for replies. Without an additional outbound
service, replies will use the destination inbox's address. Do not promise a
support response from the branded address before outbound mail is configured.

Official setup reference:
https://developers.cloudflare.com/email-service/get-started/route-emails/

## Enable Website Analytics

In Cloudflare: Workers & Pages > poliora > Metrics > Web Analytics > Enable.
Cloudflare injects the analytics beacon on the next deployment. The site's CSP
already permits the Cloudflare beacon script and collection endpoint. Verify
the beacon on the published page after deploying; do not assume a toggle alone
is enough.

Use aggregate visits, referrers, countries, devices and performance to learn how
the public website is doing. Do not treat visitors as verified app users or
installer downloads as installations or retained users. GitHub release asset
download counts can be inspected separately; they include repeat downloads and
automated requests.

The desktop app has no automatic product-telemetry upload. There is no hidden
user ID, session replay, feedback content tracking or app usage collection in
the public site. Any future desktop telemetry needs a separate explicit opt-in,
a documented minimal schema, a secured receiver and a retention policy.

## Feedback Operations

The form validates user input, previews the exact draft, and offers copying,
email (once enabled), or a public GitHub draft. It never reports a successful
send, saves browser data, attaches files or submits an issue automatically.
GitHub requires a GitHub account, but no Poliora account is needed.

Review feedback daily during the beta. Track tool, OS, app version, whether
setup succeeded, the user's expected result and whether the product helped.
Do not ask users to send raw AI transcripts or credentials. Request redacted
screenshots only when needed.

## Checks

Run the static site smoke script against a local HTTP server before publishing:

```text
node scripts/site_smoke.cjs http://127.0.0.1:8950 artifacts/site-refresh
```

It checks six viewport sizes, calculator boundaries, FAQ behavior, command
copying, image loading, download destinations, SEO metadata, privacy/support
pages, draft encoding, HTML injection handling and absence of form uploads or
browser storage. Email delivery remains a separate real-world test.
