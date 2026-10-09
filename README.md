# Dune 3 Tickets

Watches **ODEON Luxe Bristol (Cabot Circus)** for **Dune: Part Three in IMAX on
release day, Friday 18 December 2026**, and buzzes your phone through the
GitHub app the moment tickets appear, with links straight to seat picking.

Once a day you get a short "still watching" note, so silence never means it
has quietly broken.

## How it's split

ODEON's Cloudflare blocks GitHub's servers outright (a flat 403 for the website
and the booking API alike), so the checking can't happen on GitHub. Your home
connection is fine. So:

| Where | What | How often |
|---|---|---|
| **Your PC** | Checks ODEON and decides what's worth telling you | Every 5 minutes while it's awake (Task Scheduler) |
| **GitHub: Notify** | Turns the PC's alerts into issues from `github-actions[bot]`, which buzz your phone | When the PC asks |
| **GitHub: Watchdog** | Alerts you if the PC has stopped checking | Hourly |

Why the Notify step: GitHub never notifies you about your own actions, and the
PC acts as you. Issues it opened itself would arrive in silence.

**The weak spot is the laptop.** Asleep, lid shut, off or offline means no
checks. The watchdog tells you within about 3 hours (not overnight between
23:00 and 08:00; you'll hear in the morning if it's still off). Plugged in with
the lid open, it never sleeps. On battery it sleeps after 15 minutes idle.

## What you'll get

| Notification | When |
|---|---|
| 🎟️ **ON SALE** | Release-day IMAX showings can be booked. Links go straight to the seat picker. |
| ⏰ **Listed, on sale at …** | ODEON has loaded the showings but sales open later. Gives the exact time. |
| 🔑 **Members pre-sale** | A members-only window (ODEON Limitless / myODEON) has opened before general sale. |
| 🎬 **On sale, but not IMAX** | Release-day Dune is bookable at Bristol, but none of it in the IMAX screen. |
| 📅 **New Dune dates** | ODEON added Dune showings on another day, e.g. an early-access preview. |
| 🚦 **Queue is on** | ODEON switched on its website waiting room. It only does that for big on-sales. |
| ⚠️ **Can't reach ODEON** | Every check for an hour has failed. Closes itself when ODEON is back. |
| 💤 **PC stopped checking** | The watchdog hasn't seen a check for 3 hours. Closes itself on the next check. |
| ✅ **Still watching** | Daily, with the first check after 9am UK time: what ODEON has for Dune. |

**When you've booked, close the 🎟️ issue.** That tells the watcher you're
sorted: it switches off the PC task and the watchdog. Both also stop by
themselves after 18 December.

The 🫀 **Watcher heartbeat** issue is rewritten by your PC after every check,
for the watchdog to read. It never notifies you. Leave it open.

## Make sure your phone buzzes

1. Install the GitHub app and sign in as `conoregan1`.
2. In the app: your profile → ⚙️ Settings → Notifications. Turn push
   notifications on, including for things you're **assigned** to and
   **mentioned** in.
3. Send a test, from PowerShell in this folder:

   ```powershell
   python main.py --notify --test-alert
   ```

   Your phone should buzz within a minute.

## On your PC

The scheduled task is already set up. To repair it (say, after reinstalling
Python) or remove it:

```powershell
powershell -ExecutionPolicy Bypass -File tools\schedule.ps1           # set up / repair
powershell -ExecutionPolicy Bypass -File tools\schedule.ps1 -Remove   # take it away
```

It runs `pythonw main.py --notify --log`: hidden, only while you're logged
in, on battery too. Everything it does is appended to `.cache\watch.log`.

Other things you can run (Python 3.11+, standard library only):

```powershell
python main.py               # check now and print what it sees; sends nothing
python main.py --status-now  # ...and show the daily note it would send
python main.py -v            # list every request
python tools\selftest.py     # offline tests against saved ODEON data
```

It uses your `gh` login to talk to GitHub. If you ever log out of `gh`, run
`gh auth login` again or the alerts stop (the watchdog will notice).

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
date, the daily note's hour, how long an outage lasts before you're told, and
the watchdog's timings. Edit it in this folder (the PC reads it on every
check), then commit and push so the watchdog on GitHub agrees.

## How the check works

ODEON's website is built on Vista's booking system. Every page carries a public
API token that lasts about 12 hours, and the site's own JavaScript uses it to
fetch showtimes. Each check does the same:

1. Reuse the saved token, or fetch a fresh one from the Bristol cinema page.
2. Ask for every film, to find the Dune entries by title.
3. Ask which days Dune plays at site `315` (ODEON Luxe Bristol).
4. Read the release day's showtimes in full: which are IMAX, which are sold
   out, and the booking rule that says when each goes on sale.
5. If something is new, ask GitHub to raise an issue, and remember it in
   `state.json` so it's never announced twice.

That's about five small requests every five minutes, all to the endpoints the
public site uses. No login and no account. Delete `state.json` to start over.

The repo is public so the watchdog's hourly runs cost nothing (a private repo
has 2,000 free Actions minutes a month, shared with weekend-flights). Nothing
in it is secret.

## Files

```
main.py                         entry point (what Task Scheduler runs)
config.toml                     what to watch
dune_tickets/odeon.py           ODEON API client: token, films, showtimes, seats
dune_tickets/watch.py           is it on sale, in IMAX, on the day? what's new?
dune_tickets/messages.py        wording of every notification
dune_tickets/runner.py          one check: look, decide, notify, remember
dune_tickets/github.py          issues as notifications, the heartbeat
dune_tickets/state.py           state.json (local, not committed)
tools/schedule.ps1              set up / remove the Task Scheduler entry
tools/watchdog.py               GitHub's hourly "is the PC still checking?"
tools/selftest.py               offline tests; run before pushing
tools/check_workflows.py        catches workflow YAML that parses but is wrong
tools/fixtures/                 real ODEON responses from 10 Oct 2026
.github/workflows/notify.yml    raises issues for the PC, as the bot
.github/workflows/watchdog.yml  the hourly watchdog
.github/workflows/selftest.yml  runs the tests on every push
```
