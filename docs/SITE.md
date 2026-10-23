# The project site

`site/` is a hand-written, single-page document: `index.html`, one stylesheet
(`styles.css`), and an SVG favicon. No JavaScript, no framework, no build step,
no external font or CDN request — the only network traffic is the page itself.
It respects `prefers-color-scheme`, uses the system font stack, and is readable
on a phone.

Published at <https://akshayharti.github.io/ntfs-mounter/>.

## What is published

Only the contents of `site/` are published. The repository's `docs/` directory
(specs, plans, release notes) stays in the repository: those are written for
contributors, not for the web.

## Preview locally

```bash
python3 -m http.server -d site
```

Then open <http://localhost:8000>. Serving from `site/` is what makes the
relative `styles.css` and `favicon.svg` links resolve; opening the file directly
with `file://` mostly works too, but the local server matches GitHub Pages.

## Deployment

`.github/workflows/pages.yml` builds and deploys on every push to `main` that
touches `site/**` or the workflow itself, and can be run by hand with
`workflow_dispatch`. The `build` job uploads `site/` as the Pages artifact; the
`deploy` job is the only one with `pages: write` and `id-token: write`. There
are no secrets and no custom actions.

## One-time enablement

GitHub Pages has to be switched to "GitHub Actions" as its source once per
repository; the workflow cannot do that for itself. A maintainer runs, once:

```bash
gh api -X POST repos/AkshayHarti/ntfs-mounter/pages -f build_type=workflow
```

Check the current state with:

```bash
gh api repos/AkshayHarti/ntfs-mounter/pages
```

A `404` means Pages is not enabled yet. Enabling it is the only manual step;
after that, every push to `main` touching `site/**` redeploys automatically and
the site is live at <https://akshayharti.github.io/ntfs-mounter/>.

## Editing

Keep the page honest: every claim on it should match `README.md`, `AGENTS.md`,
and the code. No JavaScript is a constraint, not an accident — a static page
cannot drift into a second implementation of the UI.
