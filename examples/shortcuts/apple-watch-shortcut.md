# Apple Watch Shortcut Skeleton

Create a shortcut named `Workstation`.

Actions:

1. Choose from Menu:
   - Capture idea
   - Code on workstation
   - Repo status
2. Dictate Text.
3. Dictionary:
   - `source`: `apple_watch`
   - `input_type`: `voice`
   - `intent`: menu-dependent value
   - `repo`: optional, for coding/status
   - `task`: dictated text
4. Get Contents of URL:
   - URL: `https://n8n.divyeshvishwakarma.com/webhook/<random-router-path>`
   - Method: `POST`
   - Headers:
     - `Content-Type`: `application/json`
     - `X-Dagent-Shortcut-Secret`: your shortcut secret
   - Request Body: JSON dictionary
5. Show Result.

Keep the watch version short. Put richer file/image workflows on iPhone share sheet shortcuts.

