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
  "intent": "code_task",
  "repo": "dagent",
  "task": "Add tests for the worker approval flow.",
  "input_type": "voice",
  "metadata": {
    "flavor": "codex"
  }
}
```

`repo` can be any git project folder under
`/home/divyesh-nandlal-vishwakarma/Desktop/Divyesh`, for example `dagent`,
`fastpdf`, or `dLogs`. Code tasks intentionally require worker approval before
the agent runs.

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

The n8n editor root should remain protected by Cloudflare Access. The production
webhook path should have a narrow Cloudflare Access Bypass policy for
`/webhook/*`, because Apple Watch Shortcuts are not a good place to maintain
Cloudflare Access service-token credentials.

Every Watch request must still send the dAgent shared secret header:

```text
X-Dagent-Shortcut-Secret: <DAGENT_SHORTCUT_SECRET>
```

Verify the Cloudflare split after changing Zero Trust:

```bash
scripts/n8nctl public
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

## Watch Shortcut: Code On Workstation

Create a second shortcut named:

```text
Code On Workstation
```

Actions:

1. `Get Contents of URL`
   - URL:

     ```text
     https://n8n.divyeshvishwakarma.com/webhook/dagent-watch-capture
     ```

   - Method: `POST`
   - Headers:

     ```text
     Content-Type: application/json
     X-Dagent-Shortcut-Secret: <DAGENT_SHORTCUT_SECRET>
     ```

   - Request Body: `JSON`
   - JSON fields:

     ```text
     source: ios
     intent: list_projects
     scan: true
     include_new: true
     ```

   Add those as separate JSON fields. Do not add a single field named `Body`
   whose value is pasted JSON text.

2. `Get Dictionary Value`
   - Key: `options`
   - Dictionary: result from step 1

3. `Choose from List`
   - Prompt: `Select project`
   - List: `options`

4. `If`
   - Condition: `Chosen Item is New Project`

5. Inside the `If`: `Ask for Input`
   - Prompt: `Project name?`
   - Type: `Text`

6. Inside the `If`: `Get Contents of URL`
   - URL:

     ```text
     https://n8n.divyeshvishwakarma.com/webhook/dagent-watch-capture
     ```

   - Method: `POST`
   - Headers:

     ```text
     Content-Type: application/json
     X-Dagent-Shortcut-Secret: <DAGENT_SHORTCUT_SECRET>
     ```

   - Request Body: `JSON`
   - JSON fields:

     ```text
     source: ios
     intent: add_project
     name: <Provided Input magic variable>
     create_if_missing: true
     ```

7. Inside the `If`: `Get Dictionary Value`
   - Key: `repo`
   - Dictionary: result from step 6

8. Inside the `If`: `Set Variable`
   - `Project = Dictionary Value`

9. Inside `Otherwise`: `Set Variable`
   - `Project = Chosen Item`

10. `End If`

11. `Choose from Menu`
   - Prompt: `Task input?`

12. Menu item `Voice Task`: `Dictate Text`
   - Prompt: `Task?`
   - Stop Listening: `After Pause`

13. Menu item `Voice Task`: `Set Variable`
   - `Task = Dictated Text`

14. Menu item `Type Task`: `Ask for Input`
   - Prompt: `Task?`
   - Type: `Text`

15. Menu item `Type Task`: `Set Variable`
   - `Task = Provided Input`

16. `End Menu`

17. `Get Contents of URL`
   - URL:

     ```text
     https://n8n.divyeshvishwakarma.com/webhook/dagent-watch-capture
     ```

   - Method: `POST`
   - Headers:

     ```text
     Content-Type: application/json
     X-Dagent-Shortcut-Secret: <DAGENT_SHORTCUT_SECRET>
     ```

   - Request Body: `JSON`
   - JSON fields:

     ```text
     source: ios
     intent: code_task
     input_type: shortcut
     repo: <Project variable>
     task: <Task variable>
     dry_run: true
     metadata:
       flavor: codex
     ```

18. `Get Dictionary Value`
   - Key: `status`
   - Dictionary: result from `Get Contents of URL`

19. `Get Dictionary Value`
   - Key: `approval_code`
   - Dictionary: result from `Get Contents of URL`

20. `Show Result`
   - Text:

     ```text
     Project: <Project>
     Status: <status>
     Approval: <approval_code>
     ```

The first response should normally be `approval_required`. After approval, the
worker creates a dedicated worktree, runs Codex, sends ntfy when finished, and
includes the protected code-server URL.

Keep `dry_run: true` while testing the Shortcut. Remove it or set it to `false`
when you want Codex to actually run after approval.

`New Project` creates a new empty git repo under the first configured
`trusted_roots` entry. If you type the name of an existing git repo under the
trusted root, dAgent registers it instead. Existing non-git folders are rejected
so the code worker does not lose local context by creating an empty worktree.

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
