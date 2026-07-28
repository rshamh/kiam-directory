# SEO

Human-authored. Propose changes; do not rewrite.

## Subdomain reality
Search engines treat `directory.kiamclinic.com` as a **separate site** from `kiamclinic.com`. It
does not inherit the main domain's authority. That cost is accepted deliberately — see
`docs/multi-project-architecture.md` §5. Mitigate with cross-linking, not with canonical tricks.

- Own `robots.txt`, `sitemap.xml`, `llms.txt`. Never shared with another subdomain.
- Register as its own Search Console property.
- Canonicals point **within** this subdomain. Never across.
- Cross-link deliberately: the main site's Directory nav item and relevant service pages link
  here; profiles and landing pages link back to the main site.

## Every page
Title, meta description, canonical, OG/Twitter tags, one `<h1>`, correct heading order, JSON-LD.
Use the `/new-page` scaffold so nothing is wired by hand.

## JSON-LD by page type
| Page | Type |
|---|---|
| Home | `WebSite` + `Organization` (shared NAP) |
| Profile | `Person` + `ProfilePage` + `BreadcrumbList` |
| Speciality/town landing | `CollectionPage` + `BreadcrumbList` |
| Static | `WebPage` |

**Do not emit `AggregateRating`** — there are no reviews and there never will be.
Be careful with `Physician`/`MedicalBusiness` on profiles: these practitioners are independent,
not a Kiam clinic location, and marking them up as such misrepresents the relationship. `Person`
is the honest type.

## Indexable vs not
- **Indexable:** home, profiles, curated `/[speciality]/[town]` landing pages, browse indexes,
  static pages.
- **`noindex, follow` + canonical to `/search/`:** every faceted search URL. Without this you
  generate millions of thin permutations and Google treats the subdomain as a doorway farm.

## Landing-page rule
Generate `/[speciality]/[town]` **only** where ≥3 published practitioners match **and** a unique
editorial intro exists and is signed off. No exceptions — auto-generated thin pages are the single
fastest way to damage this subdomain.

## AEO / AI search
`llms.txt` describes what the directory is, who it lists, and the independence relationship.
Profile and landing pages should answer the obvious question directly in the first paragraph
("Dr X is an independent consultant psychiatrist offering adult ADHD assessment in Epsom, in
person and online"), because that is what gets extracted.

The AI-based search box is a later phase. Nothing in this phase should block it: keep `intro`,
`services` and speciality labels as clean text so a `pgvector` column can be added later without
restructuring.

## Performance
SSR everything. Core Web Vitals budget on a throttled 4G connection. Headshots served responsive
and lazy below the fold. HTMX partial swaps must not break `hx-push-url` history or the back
button.
