# Security Policy

## API keys and secrets

This project uses a Google Maps / Street View API key **only in the browser**.

- The key is **never committed** to this repository. The map prompts for it at
  runtime and stores it in the browser's local storage.
- `streetview-key.local.js` and `.env` are gitignored.
- If the map is published (e.g. GitHub Pages) with an embedded key, that key
  **must** be restricted in the Google Cloud Console:
  - **Application restriction**: HTTP referrers limited to the published domain
    (e.g. `https://<user>.github.io/*`).
  - **API restriction**: only the *Maps JavaScript API* and *Street View Static
    API*.
  - A daily quota cap, as a backstop.

## If a key is exposed

1. Delete or regenerate the key in the Google Cloud Console immediately.
2. Remove it from any committed files and from git history.
3. Issue a new, properly restricted key.

## Reporting a problem

Open a private issue or contact the repository owner directly. Please do not
disclose a leaked credential in a public issue.
