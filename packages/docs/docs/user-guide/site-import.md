---
id: site-import
title: Import a Docs Site
description: Paste a documentation site's address once and Chaos Cypher finds every page, imports each as its own source under one site, and later checks the site for updates.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Import a Docs Site

Most of what people want an assistant to know is documentation: a product's docs, a project wiki, a manual. Instead of adding pages one at a time, paste the site's address once. Chaos Cypher finds the site's pages, shows how many there are, and imports them all. Each page becomes its own [source](sources.md), so answers cite the page they came from and link to it; together they show as one site with one progress bar. Later, **Check for updates** fetches the site again and re-imports only the pages that changed.

## Import a site

<Tabs>
<TabItem value="web-ui" label="Web UI">

1. Click **Add Source** and paste the site's address into the **URL** field, for example `https://docs.example.com`.
2. Once you stop typing, the dialog checks the address. When it finds more than one page it offers two choices:
   - **Import the whole site: 71 pages (found by llms.txt)**
   - **Just this page** (the default)
3. Choose the whole site. **Show pages** lists every page found, all selected; untick any you do not want.
4. Click **Import site**.

The address check answers within a few seconds: it has one deadline for the whole check, reads only a few sitemaps and small documents, and ignores a site's `Crawl-delay` for its few requests. When it stops early it says **at least 25 pages**; the import itself then finds the rest, and the pages you unticked stay out (the dialog sends the pages you left out, never a list of the ones it saw). The site remembers the pages you left out: every **Check for updates** keeps them out (see [Pages left out](#pages-left-out)). If the address is refused (see [Safety](#safety)) or no other pages are found, the dialog says so and **Just this page** still works. A site that is already imported is not imported twice: the dialog says so, and **Check for updates** on its row is the way to bring it up to date.

</TabItem>
<TabItem value="cli" label="CLI">

```bash
chaoscypher source add https://docs.example.com --site
```

```
Example Docs (https://docs.example.com/): 71 pages found by llms.txt.
Import every page as one site? [Y/n]: y
Fetching pages…
Fetched 71 pages: 71 new.
[1/71] OK https://docs.example.com/introduction.md
[2/71] OK https://docs.example.com/install.md
…
Example Docs: 71 of 71 pages done
```

`--yes` imports without asking. Every other `source add` option (`--quick`, `--pack`, `--skip-extract`, `--index-only`, …) applies to each page, except `--skip-duplicates` (see [Each page](#each-page)). A site already imported is refused with the command that checks it for updates. See [Source Commands](../reference/cli/sources.md#import-a-docs-site).

</TabItem>
<TabItem value="mcp" label="MCP">

In write mode an assistant calls `add_site`:

```
add_site(url="https://docs.example.com")
```

```
Importing https://docs.example.com/ (the site's title: "Example Docs"): 71 pages found by llms.txt. The pages are fetched first, then indexed for search one by one; refresh_site checks the site for updates later.
```

The pages are fetched and then indexed for search in the background. The site's title is the site's own text, so the answer labels and quotes it (one short line, markup removed) rather than repeating it as part of the answer. See [MCP Server](mcp.md#importing-a-docs-site).

</TabItem>
<TabItem value="api" label="API">

```bash
# What would be imported (nothing is imported)
curl -X POST http://localhost/api/v1/sources/sites/previews \
  -H "Content-Type: application/json" \
  -d '{"url": "https://docs.example.com"}'

# Import every page (202; poll GET /api/v1/sources/sites/{id} for progress)
curl -X POST http://localhost/api/v1/sources/sites \
  -H "Content-Type: application/json" \
  -d '{"url": "https://docs.example.com", "options": {"analysis_depth": "quick"}}'
```

`exclude` (a list of addresses from the preview) leaves those pages out; every other page the import finds is imported. The site remembers them (`excluded_pages` in its JSON), and every refresh keeps them out. `options` are the [URL import](sources.md#url-import)'s. A site already imported (the same address scope in the same database) answers **409** with that site in `details.site`; refresh it instead.

</TabItem>
</Tabs>

## How the pages are found

Chaos Cypher tries three ways, in order, and uses the first that finds pages under the address you gave:

1. **llms.txt**: the list of pages many documentation tools publish for assistants (Mintlify, GitBook, Docusaurus plugins and others), looked for at the address you gave, then at the site's root. Its links often point at Markdown versions of the pages, which are imported as they are.
2. **Sitemap**: the `Sitemap:` lines of the site's `robots.txt`, `sitemap.xml` at the address and at the root, and the sitemap indexes they lead to.
3. **Crawling**: following the links of the page itself, breadth first, three links deep.

Whichever way is used, the import never leaves the address you gave: the same scheme and host, and pages under the same path (`https://example.com/docs/` imports only pages under `/docs/`; `https://docs.python.org/3.12` only pages under `/3.12`, while `https://example.com/docs/index.html` names `/docs`). An `http://` address of a site that answers on `https://` is followed there first. Fragments are dropped, a trailing slash does not make a second page, and files that are not pages (images, archives, PDFs) are skipped.

## Each page

Every page is fetched once, converted to clean text and imported as its own source, with the choices you made in the dialog (or the CLI's options): entity extraction on or off, quick or full analysis, the packs. When the packs are left to detection, each page is extracted with the packs detected for it, without stopping to ask: confirming a site page by page would mean one confirmation per page. To use particular packs, choose them before you import.

**Skip duplicates** does not apply to a site's pages: a page whose text you already imported is still imported as the site's own page, and your earlier source is left as it was (handing it to the site would let deleting the site with its pages delete it).

A page that cannot be fetched is marked failed on its own row, and so is one that a redirect takes off the site or onto a path its `robots.txt` disallows; the rest of the site carries on. During **Check for updates** a page already imported that cannot be fetched (or now redirects off the site) keeps its content and is reported as unreachable, and a page whose earlier fetch failed is tried again.

A large or slow site is imported in runs: each run works for up to `web.site_run_max_seconds`, remembers how far it got and hands the rest to a new run. A run that is stopped part-way (a restart, a crash) is resumed from that point by the next **Check for updates**, without fetching the pages already done again.

## The site in the sources list

A site is one row in **Sources**: its title and address, **43 of 71 pages** with a progress bar, and its state (**Queued**, **Importing**, **Checking for updates**, **Imported**, **Failed** with the reason). Counts of failed pages and pages gone from the site show beneath it, and so does **2 pages left out** when the site leaves pages out (point at it to see which). Expand the row to see its pages; each is an ordinary source row and opens like one.

Searching or filtering the sources list searches every source, a site's pages included.

## Check for updates

**Check for updates** (the CLI's `source site refresh`, the MCP server's `refresh_site`) finds the site's pages again and fetches every one:

- a page whose text is unchanged is left alone;
- a page whose text changed is re-imported in place: same source, its old entities and relationships released, the new text extracted again;
- a new page is added;
- a page still being processed is left for the next check;
- a page the site no longer lists is marked **Gone from site**. It is never deleted: it stays searchable until you remove it, and the mark clears if the site lists it again.

A check cannot start while an import or another check of the same site is queued or running; the menu says why. A run whose worker was stopped (a restart, a crash) does not keep the site locked: the site is marked **Failed** ("The import was interrupted") when the worker starts again, and in any case a run older than the operations worker's timeout no longer counts as running. A check that stopped early (the page limit) marks nothing gone, and so does a check that found the pages another way than the last one (a sitemap after `llms.txt`): it matches each page however it is spelled (`/a.md` and `/a` are one page), so nothing is imported twice.

From the CLI, `source site refresh` runs the new and changed pages with the choices the site was imported with; a flag you pass (`--quick`/`--full`, `--skip-extract`/`--extract`) overrides that one choice for this check.

### Pages left out

A site remembers the pages you left out, and every check keeps them out:

- the pages you unticked when you imported it;
- the pages you delete from the site afterwards (deleting one of its pages from **Sources**, the CLI's `source delete` or the MCP server): otherwise the next check would import it again.

A page the site gains later is still added; only the pages you left out are kept out, matched however the site spells them (`/faq`, `/faq/` and `/faq.md` are one page, and an `http://` spelling of an `https://` site's page names that page). A left-out page that the site already has (for example one you left out after importing it) keeps what it has and is not checked for changes; its row says **Left out** (the CLI's `source site show`: "left out, not checked for updates"). A left-out page the site no longer lists does no harm.

Each page to leave out is given by its full address on the site (`https://docs.example.com/faq`). An entry that names no page of the site (no `https://`, a bare path, another host, a path outside the site) is refused with the entries named, never dropped: a typo cannot empty the list and bring every page back.

To bring pages back, check for updates with a new list: in the web UI, **Include the left-out pages** in the site's menu asks first (**Bring back 3 pages?**: pages you deleted from the site come back too), then checks for updates with every page brought back; the CLI and the MCP server name the pages a shorter list brings back; the CLI's `source site refresh --exclude URL` (repeatable) replaces the list and `--include-all` empties it; the MCP server's `refresh_site` takes `exclude`; the API's `POST /api/v1/sources/sites/{id}/refreshes` takes `{"exclude": [...]}` (`[]` brings every page back; no body keeps the list).

## Delete a site

**Delete site** asks whether to keep the site's pages as separate sources (the default) or delete them too. Kept pages stay searchable and are no longer checked for updates. Like **Check for updates**, it waits while an import or a check of the site is running.

## Politeness and limits

The import fetches the way a well-behaved crawler does. Every number is a setting in the `web` group of `settings.yaml` (see [Configuration](../getting-started/configuration.md)):

| Setting | Default | What it does |
|---|---|---|
| `web.user_agent` | `ChaosCypher/<version> (+https://chaoscypher.com)` | Sent with every request; robots.txt rules are matched against it |
| `web.respect_robots_txt` | `true` | Pages robots.txt disallows are skipped; a larger `Crawl-delay` is used |
| `web.crawl_delay_max_seconds` | `5` | Largest robots.txt `Crawl-delay` honoured; a larger one is cut to this |
| `web.crawl_delay_seconds` | `0.5` | Pause between two requests to the site |
| `web.crawl_concurrency` | `2` | Requests to the site in flight at once |
| `web.site_max_pages` | `500` | Most pages one site import takes |
| `web.site_crawl_max_depth` | `3` | How many links deep a crawl goes |
| `web.site_max_sitemaps` | `50` | Most sitemap files one discovery reads |
| `web.site_document_max_bytes` | 50 MiB | Largest robots.txt, llms.txt, sitemap or page read during discovery |
| `web.site_run_max_seconds` | `1800` | How long one import run works before it saves its place and continues as a new run |
| `web.site_title_max_chars` | `120` | Longest site title kept (one line, markup removed) |
| `web.site_pages_named_max` | `5` | Page addresses a message names (pages left out, pages brought back) before "and N more" |
| `web.site_preview_seconds` | `10` | Seconds the whole quick check may take before it answers with what it found |
| `web.site_preview_max_pages` | `25` | Pages the quick check may crawl before it answers |
| `web.site_preview_max_sitemaps` | `3` | Sitemap files the quick check reads |
| `web.site_preview_document_max_bytes` | 5 MiB | Largest document the quick check reads |
| `web.site_preview_debounce_ms` | `800` | How long the dialog waits after typing before it checks |

### Safety

Every address, the one you give and every page and redirect after it, passes the strict address check: private, loopback, link-local and cloud-metadata addresses are refused, and so is a name that does not resolve. Every redirect hop is checked again; discovery never counts a page that a redirect took off the site, and the import refuses a page whose final address is off the site or disallowed by its `robots.txt`.

## What it does not do

- Sites behind a login.
- Pages that only render in the browser with JavaScript (single-page apps that serve no HTML text).
- PDFs linked from a site. Import a PDF by its own address with [URL import](sources.md#url-import).
- Scheduled checks. **Check for updates** runs when you ask.
