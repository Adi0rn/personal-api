# Companion voice assistant adapter

Copy `personal_api_client.py` from this folder into your companion assistant's
module directory. Install `requests` in the companion's environment if needed.
Keep PAPI running on `http://127.0.0.1:8000`.

In the companion's command-handler module, import:

```python
from personal_api_client import PersonalAPIError, handle_api_command
```

After handling built-in help and before existing local commands or model fallback,
pass the original recognized text to the adapter:

```python
try:
    api_reply = handle_api_command(original)
except PersonalAPIError as exc:
    raise CommandError(str(exc)) from None
if api_reply is not None:
    return api_reply
```

Here `original` is the recognized text and `CommandError` is the companion's
existing user-visible command exception. Adapt these two names for other assistants.
The overlay and terminal should share this command handler. Restart an open overlay
after updating. Preserve its existing notes, shortcuts, audio, and model settings.

Optional environment setting: `PERSONAL_API_URL=http://127.0.0.1:8000`.
Only loopback HTTP addresses are accepted. Spotify credentials belong to PAPI,
not the companion. Recognized command failures should be shown to the user;
unrecognized commands return `None` and can continue through the existing handler.

Try "show my tasks", "add task Buy milk", "show my projects", "pause my music",
or "open dashboard". See the main README for supported commands and behavior.
This repository includes the adapter and installation instructions; the separate
voice-assistant project is not bundled.
