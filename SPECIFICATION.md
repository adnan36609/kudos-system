# Kudos System Specification

**Feature:** Kudos for the internal employee portal  **Stack:** Python 3, Flask, SQLite, Jinja templates
**Repository:** _paste your public GitHub/GitLab link here_

## 1. Functional Requirements

### User stories
1. As an employee, I can sign in so that kudos are attributed to me.
2. As a user, I can select a colleague from a dropdown list (excluding myself).
3. As a user, I can write a short message of appreciation (1-500 characters).
4. As a user, I can submit the kudos, which is stored in the database.
5. As a user, I can see a feed of recent kudos (newest first, paginated) on the main dashboard.
6. **As an administrator, I can hide or delete inappropriate kudos messages.** _(added in review)_
7. **As an administrator, I can restore a hidden kudos and see why it was hidden.** _(added in review)_

### Acceptance criteria
| Story | Criteria |
|---|---|
| 1 | Unauthenticated requests to `/` redirect to `/login`. Wrong credentials show an error and log a warning. |
| 2 | The dropdown lists every user except the current user. Submitting no recipient, an unknown recipient, or yourself is rejected with a clear message. |
| 3 | Message is trimmed and whitespace collapsed. Empty, whitespace-only, or over 500 characters is rejected (checked on server and by `maxlength` in the browser). |
| 4 | A valid kudos is saved with sender, recipient, message, and UTC timestamp. The user sees "Kudos sent." |
| 5 | Feed shows sender, recipient, date, and message, 20 per page, with Newer/Older links. Only visible kudos appear for normal users. Empty feed shows a prompt to send the first one. |
| 6 | Admins see Hide (with optional reason) and Delete on every item. Hide sets `is_visible = 0` and records who/when/why; hidden items disappear from non-admin feeds. Delete permanently removes the row. Non-admins get HTTP 403. |
| 7 | Admins see hidden items marked "Hidden from the feed" with the reason and a Restore button, which sets `is_visible = 1`. |

### Edge cases (moderation and abuse)
- **Spam:** max **10 kudos per user per hour**; excess is rejected with a message.
- **Duplicates:** the same sender sending an identical message (case-insensitive) to the same recipient within **10 minutes** is rejected (also catches double-clicks).
- **Inappropriate content:** handled after the fact via admin hide/delete. Hiding is preferred over deleting because it is reversible and auditable.
- **Self-kudos:** rejected.
- **Script/HTML in messages:** stored as plain text and escaped on output.
- **Hidden-by-mistake:** admin can restore.
- Out of scope (future): automatic profanity filter, user reporting button, editing/deleting own kudos, email notifications.

### Non-functional requirements
- Responsive layout (works from phone width up), visible keyboard focus, labelled form fields.
- Authentication required for all pages; "public feed" means visible to all signed-in employees.

## 2. Technical Design

### Database schema (SQLite)

**users**
| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | autoincrement |
| username | TEXT | unique, lower-case |
| display_name | TEXT | shown in dropdown/feed |
| password_hash | TEXT | Werkzeug salted hash |
| role | TEXT | `'user'` or `'admin'` (CHECK constraint) |
| created_at | TEXT | UTC ISO-8601 |

**kudos**
| Field | Type | Notes |
|---|---|---|
| id | INTEGER PK | autoincrement |
| sender_id | INTEGER FK -> users.id | not null |
| recipient_id | INTEGER FK -> users.id | not null |
| message | TEXT | not null, CHECK length 1-500 |
| created_at | TEXT | UTC ISO-8601 |
| **is_visible** | INTEGER (boolean) | **default 1**; 0 = hidden by moderator |
| **moderated_by** | INTEGER FK -> users.id | admin who last hid/restored; nullable |
| **moderated_at** | TEXT | time of last moderation action; nullable |
| **moderation_reason** | TEXT | optional reason (max 200 chars), cleared on restore |

Indexes: `(is_visible, created_at DESC)` for the feed; `(sender_id, created_at)` for rate-limit and duplicate checks.

### API endpoints
| Method & path | Auth | Purpose | Responses |
|---|---|---|---|
| GET `/login`, POST `/login` | none | Sign in (`username`, `password`) | 200 form, 302 to `/` on success, 200 with error |
| POST `/logout` | user | End session | 302 to `/login` |
| GET `/?page=N` | user | Dashboard: kudos form + paginated feed | 200; 302 if not signed in |
| POST `/kudos` | user | Create kudos (`recipient_id`, `message`) | 302 to `/` with success or error flash |
| POST `/admin/kudos/<id>/hide` | admin | Hide (`reason` optional) | 302; 403 non-admin; 404 unknown id |
| POST `/admin/kudos/<id>/restore` | admin | Make visible again | 302; 403; 404 |
| POST `/admin/kudos/<id>/delete` | admin | Permanently delete | 302; 403; 404 |

All POSTs require a `csrf_token` (400 if missing/invalid).

### Frontend components
- **base.html**: header (app name, current user, Sign out), flash messages, shared styles.
- **login.html**: username/password form.
- **index.html**: (a) Kudos form with recipient `<select>`, message `<textarea maxlength=500>`, Send button; (b) feed of kudos articles; (c) admin controls per item (Hide + reason / Restore / Delete); (d) Newer/Older pager.
- Interaction: form POST then redirect (Post/Redirect/Get) so refresh does not resubmit.

### Security considerations
- Passwords stored as salted hashes; session cookie is HttpOnly and SameSite=Lax; `SECRET_KEY` read from the environment (the default is for local development only).
- Authorization enforced server-side on every admin route (not just by hiding buttons).
- CSRF token on every POST.
- All SQL is parameterised; Jinja auto-escaping prevents stored XSS.
- Input validated on the server (type, length, existence, self-send).
- Demo accounts use a shared password and **must not be used outside local development**. Production should use the company SSO.

### Performance considerations
- Pagination (20 per page, fetches one extra row to detect a next page) so the feed never loads everything.
- Feed and rate-limit queries are covered by indexes.
- Future: cache the first feed page, move to PostgreSQL for concurrent writes.

### Error handling and logging
- User errors (validation, limits) show a flash message and never crash.
- 400/403/404 are returned for bad CSRF, forbidden, and unknown IDs.
- Logged: failed logins (WARNING), forbidden admin attempts (WARNING), kudos created and all moderation actions with actor id (INFO).

## 3. Implementation Plan
| # | Task | Depends on |
|---|---|---|
| 1 | Project skeleton, requirements, `.gitignore` | none |
| 2 | Database schema + init/seed command (including moderation fields) | 1 |
| 3 | Authentication (login/logout, session, CSRF, `login_required` / `admin_required`) | 2 |
| 4 | Create-kudos endpoint with validation, rate limit, duplicate check | 3 |
| 5 | Dashboard: form, paginated feed, responsive template | 4 |
| 6 | Moderation endpoints (hide/restore/delete) and admin UI | 3, 5 |
| 7 | Automated tests | 4-6 |
| 8 | README, commit and push | 7 |

**Testing strategy:** `unittest` with Flask's test client and a temporary database per test. Covers auth, CSRF, validation, duplicates, rate limit, XSS escaping, pagination, and the full admin hide/restore/delete flow including 403 for non-admins. Run: `python -m unittest discover -s tests -v`.

**Deployment considerations:** run behind a production WSGI server (e.g. gunicorn) with HTTPS, set `SECRET_KEY`, replace seeded demo users with SSO, switch to a managed database, back up the DB, ship logs to central logging.
