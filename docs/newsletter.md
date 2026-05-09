# Newsletter Feature Toggle

The top navigation newsletter button is controlled by `newsletter_button_enabled`
and `newsletter_url` in `app/settings.py` or `.env`.

## Current state

The newsletter URL is configured, but `newsletter_button_enabled` defaults to
`False`, which hides the top navigation newsletter button.

## Re-enable

Set both values (example in `.env`):

```
newsletter_button_enabled=true
newsletter_url="https://mailchi.mp/your-list"
```

Or edit the defaults in `app/settings.py`:

```
newsletter_button_enabled: bool = True
newsletter_url: str = "https://mailchi.mp/your-list"
```

Once enabled, the newsletter button will appear in the top navigation. Leave the
flag false to keep only the footer newsletter link.
