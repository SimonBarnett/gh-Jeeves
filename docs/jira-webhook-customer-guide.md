# Jira webhook setup guide (customer side)

**Date:** 2026-09-27
**Repo:** SimonBarnett/gh-Jeeves
**Status:** guide for the customer who owns the Jira instance

## Purpose

Simon needs to log hours against the correct Jira tickets, but he does not have a Jira license. The customer periodically pushes ticket data from their Jira instance to Simon's webhook, in Jira's native payload shape, so Simon's agent can ingest it and surface his tickets and descriptions.

No Jira license is required on the customer's side for this: the **Send web request** action in Jira Automation is included in every Jira Cloud plan (free or paid).

## What the customer needs

1. A Jira Cloud site (any plan).
2. Admin rights to create an automation rule (Project settings → Automation, or the global Automation page).
3. The webhook URL from Simon (see below).
4. The shared secret from Simon, if one is configured (see Authentication).

## Webhook URL

Simon provides the full HTTPS URL. Typical forms:

- `https://irc.ntsa.uk/bob/v1/jira` — public endpoint behind IIS on Simon's host
- `https://<simon-host>/bob/v1/jira` — if Simon's deployment uses a different hostname

The path is `/bob/v1/jira`. Method: **POST**. Content-Type: `application/json`.

Simon will confirm the exact URL and any secret before the customer configures the rule.

## Authentication

If Simon has configured a shared secret, every POST must include the header:

```http
X-Bob-Secret: <secret-value>
```

Missing or wrong secret → **401**. Simon never logs the secret value. If no secret is configured, the header is not required.

## Automation rule (Jira Cloud)

Build one rule in Jira Automation:

1. **Trigger:** Scheduled (e.g. every hour, or daily at a fixed time).
2. **JQL:** `assignee = "Simon"` — or whatever account identifier the customer uses for Simon (display name, accountId, or email). This selects Simon's tickets; the webhook itself does no filtering.
3. **Action:** Send web request.
   - URL: the webhook URL from Simon.
   - Method: POST.
   - Headers: `Content-Type: application/json`, plus `X-Bob-Secret` if configured.
   - Body: choose **Issue data (Jira format)**.

That body option emits Jira's native webhook shape, which is exactly what Simon's receiver expects. No custom JSON templating is needed.

### Optional: bulk send

If the customer prefers one POST containing several tickets, they can use a **Custom data** body shaped as:

```json
{
  "webhookEvent": "jira:issue_updated",
  "timestamp": {{now.jiraDate.jiraTimestamp}},
  "issues": [ {{#issue}}{ ...issue fields... }{{/issue}} ]
}
```

The per-issue object must still match the native shape below. Simon's parser accepts both a single `issue` object and an `issues` array.

## Native payload shape

What Jira sends (single-issue form). Simon's handler reads at minimum `webhookEvent`, `timestamp`, `issue.key`, and `issue.fields.summary`; unknown fields are ignored.

```json
{
  "timestamp": 1705424400000,
  "webhookEvent": "jira:issue_updated",
  "issue_event_type_name": "issue_generic",
  "user": {
    "accountId": "abc123",
    "displayName": "Jane Smith",
    "emailAddress": "jane@example.com",
    "active": true
  },
  "issue": {
    "id": "10042",
    "self": "https://your-domain.atlassian.net/rest/api/2/issue/10042",
    "key": "PROJ-123",
    "fields": {
      "summary": "Fix login timeout on mobile",
      "description": "Users are getting logged out after 30 seconds.",
      "issuetype": { "id": "10001", "name": "Bug" },
      "status": { "id": "3", "name": "In Progress" },
      "priority": { "id": "2", "name": "High" },
      "assignee": { "accountId": "abc123", "displayName": "Jane Smith" },
      "reporter": { "accountId": "def456", "displayName": "John Doe" },
      "project": { "id": "10000", "key": "PROJ", "name": "My Project" },
      "created": "2025-01-24T10:30:00.000+0000",
      "updated": "2025-01-24T14:15:00.000+0000",
      "labels": ["urgent", "mobile"],
      "components": [{ "id": "10201", "name": "Mobile App" }]
    }
  },
  "changelog": {
    "id": "10124",
    "items": [
      {
        "field": "status",
        "fieldtype": "jira",
        "from": "10000",
        "fromString": "To Do",
        "to": "3",
        "toString": "In Progress"
      }
    ]
  }
}
```

Key fields Simon relies on:

| Field | Role |
|-------|------|
| `webhookEvent` | Event type (`jira:issue_created`, `jira:issue_updated`, ...) |
| `timestamp` | Epoch milliseconds when the event fired |
| `issue.key` | Human-readable ticket id (e.g. `PROJ-123`) |
| `issue.fields.summary` | Ticket title |
| `issue.fields.description` | Ticket body — what Simon logs hours against |
| `issue.fields.status.name` | Current status |
| `issue.fields.assignee.displayName` | Who it is assigned to |
| `issue.fields.project.key` | Project key |
| `changelog.items` | What changed (present on `issue_updated` only) |

## What the automation does NOT do

- It does not send historical tickets. The scheduled rule only fires for issues matching the JQL at trigger time. If Simon needs his existing open tickets as a one-off, the customer can run the rule manually once, or Simon can pull them separately.
- It does not filter on Simon's side. Selection is entirely the JQL in the rule.

## Verification

After the rule is saved and enabled:

1. Create or update a test ticket assigned to Simon in Jira.
2. Wait for the next scheduled run (or trigger the rule manually).
3. Simon confirms the POST arrived (receiver log / digest) and that the ticket key, summary, and description are visible to his agent.

A quick independent check: point the rule's URL at a request inspector (e.g. https://webhook.site) first, confirm the body matches the shape above, then switch to Simon's URL.

## Notes

- Jira's native webhook payload is fixed JSON and cannot be customized; the automation "Issue data (Jira format)" option reproduces it.
- The automation-format variant includes a few extra fields (e.g. `statuscategorychangedate`, `namedValue`) that a real Jira webhook would not. Simon's parser ignores unknown fields, so this is safe.
- Jira Cloud allows up to 100 webhooks per site; this guide uses automation rather than a registered webhook, which avoids that limit and needs no app install.
- For Jira Data Center / Server the same native payload applies, but the automation action may differ by version — check the Data Center automation docs.

## Out of scope

- Receiver-side implementation in gh-Jeeves (separate change).
- Any change to Simon's existing `/bob/v1/git`, `/bob/v1/report`, or `/bob/v1/intake` endpoints.
