# Apple Shortcuts

## Role

Apple Watch and iPhone are capture and approval devices. They should send small normalized requests to n8n.

## Shortcut Pattern

```text
Dictate Text / Get Clipboard / Share Sheet input
  -> Choose command type
  -> Get Contents of URL
  -> POST JSON to n8n webhook
  -> Show result
```

## Minimal POST Body

```json
{
  "source": "apple_watch",
  "intent": "capture_idea",
  "task": "Research whether I can use local OCR for receipts.",
  "input_type": "voice"
}
```

## Coding Task Body

```json
{
  "source": "apple_watch",
  "intent": "codex_task",
  "repo": "dagent",
  "task": "Add tests for the worker approval flow.",
  "input_type": "voice",
  "require_approval": true
}
```

## Recommended Watch Commands

Keep the watch menu short:

- Capture idea
- Start research note
- Code on workstation
- Repo status
- Approve pending job

Everything else can be a phone/laptop shortcut or an n8n form.

## Shortcut Headers

Use headers like:

```text
Content-Type: application/json
X-Dagent-Shortcut-Secret: <shortcut-to-n8n-secret>
```

n8n validates this secret before it calls the local worker.

Because `n8n.divyeshvishwakarma.com` is protected by Cloudflare Access, the
Watch shortcut must also send the Cloudflare service token headers:

```text
CF-Access-Client-Id: <cloudflare-service-token-client-id>
CF-Access-Client-Secret: <cloudflare-service-token-client-secret>
```

Use the production n8n webhook URL on Apple Watch:

```text
https://n8n.divyeshvishwakarma.com/webhook/dagent-watch-capture
```

Do not use `/webhook-test/...` on Apple Watch. Test URLs only work while the
n8n editor is actively listening for a test event.

## First Watch Shortcut: Capture Idea

Create this on the iPhone Shortcuts app, then enable it for Apple Watch.

Shortcut name:

```text
Capture Idea
```

Actions:

1. `Dictate Text`
   - Prompt: `Idea?`
   - Stop Listening: `After Pause`

2. `Get Contents of URL`
   - URL:

     ```text
     https://n8n.divyeshvishwakarma.com/webhook/dagent-watch-capture
     ```

   - Method: `POST`
   - Headers:

     ```text
     Content-Type: application/json
     CF-Access-Client-Id: <cloudflare-service-token-client-id>
     CF-Access-Client-Secret: <cloudflare-service-token-client-secret>
     X-Dagent-Shortcut-Secret: <DAGENT_SHORTCUT_SECRET>
     ```

   - Request Body: `JSON`
   - JSON fields:

     ```text
     source: apple_watch
     intent: capture_idea
     input_type: voice
     task: <Dictated Text magic variable>
     ```

3. `Get Dictionary Value`
   - Key: `status`
   - Dictionary: result from `Get Contents of URL`

4. `Show Result`
   - Text:

     ```text
     dAgent: <status>
     ```

Shortcut details:

```text
Show on Apple Watch: on
```

Then run it from the Shortcuts app on Apple Watch, Siri, or a watch face
complication.

## File/Image Input

For images and files, prefer iPhone share sheet:

```json
{
  "source": "ios_share_sheet",
  "intent": "document_task",
  "task": "Summarize this PDF and create action items.",
  "input_type": "file",
  "files": [
    {
      "name": "paper.pdf",
      "url": "https://temporary-upload-or-cloud-link"
    }
  ]
}
```

For v0, store file inputs in a known folder or cloud location and pass a reference. Add binary upload handling later.
