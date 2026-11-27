# Search Launch

## Prepared In The Website

- `https://poliora.com/sitemap.xml` lists the final canonical routes.
- `robots.txt` allows crawling and advertises the sitemap.
- The homepage includes a title, description, canonical, social preview and
  structured data describing the actual free desktop beta.
- Privacy and support canonical URLs match Cloudflare's extensionless routes.
- A root `404.html` stops Cloudflare's homepage fallback for nonexistent URLs.
- The small Pages `_worker.js` redirects the exact `poliora.pages.dev` hostname
  to the primary domain, preserving path and query. It also handles `www` once
  that hostname is attached to this Pages project. Preview hostnames are left
  alone; normal requests use Cloudflare's static ASSETS handler unchanged.

## Google Search Console (Requires The Owner)

1. Open https://search.google.com/search-console using the Google account that
   should own this website's search property.
2. Add a Domain property named `poliora.com`, without a protocol or path.
3. Copy the exact TXT record Google supplies.
4. In Cloudflare > poliora.com > DNS > Records, add a TXT record at `@` with the
   supplied value and TTL Auto. Add a new record; do not replace other TXT, SPF,
   MX, DKIM or web records. DNS is managed by Cloudflare, not Namecheap now.
5. Return to Google and select Verify. DNS visibility may not be immediate.
6. In Sitemaps, submit `https://poliora.com/sitemap.xml`. Confirm Google's
   reported status; an accessible sitemap is not proof it has been submitted.
7. Use URL Inspection for `https://poliora.com/`, run Test Live URL, then Request
   Indexing if available. Confirm that Google's live test can fetch the page.
8. Keep the verification record in place after verification.

Never share a Google password, login code, session cookie or API token. The
Google site-verification TXT string is intended to be publicly visible in DNS.

## Fix The www Alias

On November 28 the apex served Poliora, but `www.poliora.com` still served the
registrar's parking page. In the existing Pages project, add `www.poliora.com`
under Custom domains. Review the DNS conflict and change only the `www` web
record to the Pages target Cloudflare supplies. Do not modify mail records.
Verify that `https://www.poliora.com/` returns a permanent redirect to
`https://poliora.com/` after activation. Code cannot correct an unrelated DNS
destination by itself.

## AI Search And Bing

There is no separate Google GEO registration or special AI schema requirement.
Useful, indexable text, accurate descriptions, working internal links, clear
compatibility limits and genuine third-party references are the foundation.
The existing `llms.txt` is an extra product summary, not a Google ranking signal
or a guarantee of AI citations.

The robots policy permits Googlebot, Bingbot and AI search crawlers under its
wildcard rule. Actual crawler access must also be allowed by Cloudflare; a
request with a crawler User-Agent is not proof of verified crawler access.
Use Google's Live URL test and Cloudflare security events to diagnose blocks.

After Google verification, Bing Webmaster Tools can import the verified Google
property. Submit the same sitemap there. IndexNow is optional for participating
search engines; it is not a Google indexing submission method.

Indexing, ranking and AI recommendations are not guaranteed. Track Search
Console impressions, clicks, query terms and indexed pages; do not interpret
absence from a `site:` search alone as a definitive indexing diagnosis.

## References

- https://support.google.com/webmasters/answer/9008080
- https://developers.google.com/search/docs/appearance/ai-features
- https://developers.google.com/search/docs/monitor-debug/search-console-start/
- https://developers.cloudflare.com/pages/configuration/serving-pages/
- https://developers.cloudflare.com/pages/functions/advanced-mode/
