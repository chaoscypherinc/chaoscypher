---
id: privacy
title: Privacy Policy
description: What Chaos Cypher, Inc. does and does not collect across the ChaosCypher software, this website, and the Lexicon Hub.
---

# Privacy Policy

**Effective date:** 2026-09-03

Chaos Cypher, Inc. ("we", "us") makes ChaosCypher, an open-source, self-hosted knowledge-graph and GraphRAG platform. This policy explains what we collect and what we do not, across the three places you might interact with us: the software you run, this website, and the Lexicon Hub.

Questions or requests about your data: [denis@chaoscypher.com](mailto:denis@chaoscypher.com).

## The ChaosCypher software

You run ChaosCypher on your own computer or server. We do not host it for you, you do not need an account with us to use it, and it does not send usage data, telemetry, or crash reports to us. The documents you load, the graphs it builds, and the settings you choose stay in your instance.

Two things in your instance can send data elsewhere, and both are under your control:

- **Cloud LLM providers.** If you configure a hosted model provider (for example OpenAI, Anthropic, or Google), your instance sends document text and prompts to that provider using your own API key, under that provider's terms and privacy policy. Using a local provider such as Ollama keeps everything on your machine.
- **Lexicon Hub.** If you sign in to the Lexicon Hub from the CLI or the web UI, your instance talks to the hub as described below. Nothing is sent to the hub unless you log in and upload or pull a package.

## This website (chaoscypher.com)

The site is a static documentation site served through Cloudflare.

- **Analytics.** We use Cloudflare Web Analytics, which is cookieless and does not track you across sites or build a profile. It reports aggregate page views, referrers, and browser and country categories. We use it to see which pages are read.
- **Cookies.** We set no cookies. Your browser may store your light or dark theme preference locally; that never leaves your device.
- **Server logs.** Cloudflare processes requests to the site under its own privacy policy, including short-lived logs used for security and performance.

## Lexicon Hub (lexicon.chaoscypher.com)

The Lexicon Hub is a registry for sharing knowledge packages. If you use it:

- **Account.** When you sign in, the hub stores the account identifier returned by the sign-in provider you chose, a display name, and the access tokens it issues to your CLI or browser. We use these only to authenticate you and attribute your uploads.
- **Packages.** Packages you upload are stored with the metadata in their manifest (name, version, description, tags, author). A package you mark **public** is visible to everyone; a **private** package is visible only to you. You can delete your packages.
- **Logs.** The hub keeps short-lived request logs for security and abuse prevention.

We do not sell hub data, and we do not use uploaded packages for anything other than serving them back to the people allowed to see them.

## Other places you may reach us

- **GitHub.** Issues, discussions, and pull requests on the public repository are handled under GitHub's terms and privacy policy and are public.
- **Email.** If you email us, we keep the correspondence for as long as needed to handle it.
- **Package registries and social media.** PyPI, the GitHub Container Registry, and the social accounts where we post announcements each operate under their own policies. We only read aggregate engagement statistics for our own posts.

## Your choices and rights

You can use the software without ever contacting us. For hub accounts and email, you can ask us to correct or delete what we hold about you by writing to the address above; we will respond within 30 days. If you are in a jurisdiction that grants specific data-protection rights, those rights apply and you can exercise them through the same address.

## Changes

We will update this page when our practices change and adjust the effective date at the top. The page's history is in the public repository.
