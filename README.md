# Dune 3 Tickets

Watches **ODEON Luxe Bristol (Cabot Circus)** for **Dune: Part Three in IMAX on
release day, Friday 18 December 2026**, and buzzes your phone through the
GitHub app the moment tickets appear, with links straight to seat picking.

It checks every 5 minutes on GitHub Actions. Once a day you get a short "still
watching" note, so silence never means it has quietly broken.

## What you'll get

| Notification | When |
|---|---|
| 🎟️ **ON SALE** | Release-day IMAX showings can be booked. Links go straight to the seat picker. |
| ⏰ **Listed, on sale at …** | ODEON has loaded the showings but sales open later. Gives the exact time. |
| 🔑 **Members pre-sale** | A members-only window (ODEON Limitless / myODEON) has opened before general sale. |
| 🎬 **On sale, but not IMAX** | Release-day Dune is bookable at Bristol, but none of it in the IMAX screen. |
| 📅 **New Dune dates** | ODEON added Dune showings on another day, e.g. an early-access preview. |
| 🚦 **Queue is on** | ODEON switched on its website waiting room. It only does that for big on-sales. |
| ⚠️ **Can't reach ODEON** | Every check has failed for an hour. Closes itself when ODEON is back. |
| ✅ **Still watching** | Daily, around 9am UK time: what ODEON has for Dune and how many checks ran. |

Each one is a GitHub issue, assigned to you and @-mentioning you. That's what
makes the GitHub app push it.

**When you've booked, close the 🎟️ issue.** That tells the watcher you're
sorted: it stops, and it switches its own schedule off. It also stops by itself
after 18 December.

## Make sure your phone buzzes

1. Install the GitHub app and sign in as `conoregan1`.
2. In the app: your profile → ⚙️ Settings → Notifications. Turn push
   notifications on, including for things you're **assigned** to and
   **mentioned** in.
3. Send yourself a test: on github.com open this repo → **Actions** →
   **Check for Dune tickets** → **Run workflow** → choose
   **send a test alert** → **Run workflow**. Your phone should buzz within
   a minute.

The same menu has **send status note now**, which sends today's "still
watching" note immediately, and **check**, which runs a normal check.

## What it knew on day one (10 Oct 2026)

- ODEON lists Dune: Part Three as two films, `HO00009199` (UK) and `HO00009200`
  (Ireland). Both are watched, along with any new entry with the same title.
- **Tue 15 Dec: two IMAX previews at Bristol (19:00, 22:45), both sold out.**
  The only seats left are wheelchair and companion spaces.
- **Fri 18 Dec: no Dune showings yet.** Avengers: Doomsday is on sale that day,
  but nothing is booked into the IMAX screen (Screen 6, 144 seats). That looks
  like ODEON keeping it for Dune.

144 seats is small for an opening day, so treat the 🎟️ alert as urgent.

## Changing what it watches

Everything is in [`config.toml`](config.toml): the cinema, the film title, the
date, the daily note's hour, and how long an outage lasts before you're told.
Edit it on github.com with the pencil icon and commit; the next check uses it.

## How it works

ODEON's website is built on Vista's booking system. Every page carries a public
API token that lasts about 12 hours, and the site's own JavaScript uses it to
fetch showtimes. Each check does the same:

1. Reuse the token from the last run, or fetch a fresh one from the Bristol
   cinema page.
2. Ask for every film, to find the Dune entries by title.
3. Ask which days Dune plays at site `315` (ODEON Luxe Bristol).
4. Read the release day's showtimes in full: which are IMAX, which are sold
   out, and the booking rule that says when each goes on sale.
5. If something is new, raise an issue. Then commit `state.json` so the same
   thing is never announced twice.

That's about five small requests every five minutes, all to the endpoints the
public site uses. No login, no account, no secrets: notifications use the token
GitHub gives every Actions run.

### Things worth knowing

- **GitHub's 5 minutes is approximate.** Under load the scheduler delays runs,
  and sometimes skips them. Real gaps are 5 to 15 minutes. The daily note shows
  how many checks actually ran.
- **The repo is public on purpose.** Public repos get unlimited free Actions
  minutes. A private one gets 2,000 a month, and checking every 5 minutes would
  use them up in under a week. Nothing in the repo is secret.
- **GitHub switches off schedules in public repos after 60 days with no
  activity.** The daily `state.json` commit counts as activity, so that won't
  happen.

## Running it on your PC

Python 3.11 or newer, standard library only:

```powershell
python main.py               # check now and print what it sees; changes nothing
python main.py --status-now  # ...and show the daily note it would send
python main.py -v            # list every request
python tools/selftest.py     # offline tests against saved ODEON data
```

Nothing is sent from your PC: `--notify` only works inside GitHub Actions.

## Files

```
main.py                        entry point
config.toml                    what to watch
state.json                     what has been announced (written by the workflow)
dune_tickets/odeon.py          ODEON API client: token, films, showtimes, seats
dune_tickets/watch.py          is it on sale, in IMAX, on the day? what's new?
dune_tickets/messages.py       wording of every notification
dune_tickets/runner.py         one check: look, decide, notify, remember
dune_tickets/github.py         issues as notifications
dune_tickets/state.py          state.json
tools/selftest.py              offline tests; run before pushing
tools/check_workflows.py       catches workflow YAML that parses but is wrong
tools/fixtures/                real ODEON responses from 10 Oct 2026
.github/workflows/check.yml    the 5-minute check
.github/workflows/selftest.yml runs the tests on every push
```
