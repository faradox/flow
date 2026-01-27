# Newsletter feature toggle

The newsletter button and footer link are controlled by `newsletter_url` in `app/settings.py` (or the `.env` override for that setting).

## Current state

The default value is now empty, which hides the newsletter UI.

## Re-enable

Set a URL (example in `.env`):

```
NEWSLETTER_URL="https://mailchi.mp/your-list"
```

Or edit the default in `app/settings.py`:

```
newsletter_url: str = "https://mailchi.mp/your-list"
```

Once set, the newsletter link and CTA will appear again on the site.
