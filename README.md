# BillionTracker

Tracks aircraft by ICAO hex code. Each position is enriched with the local weather and the owner's details, streamed through Kafka, stored in Postgres, and shown live on a Google map.

```
producer (Python) ── ADS-B Exchange ─► flight.positions ─┐
                  ── OpenWeather ────► weather.observations ─┤  Kafka Streams (Java)
                                                             │  stream-table left joins
config/billionaires.csv ─► tracked.aircraft (compacted) ───┘          │
                                                                        ▼
                                                                 flight.enriched
                                                     ┌──────────────────┴──────────────┐
                                     Kafka Connect JDBC Sink                 FastAPI WebSocket
                                                     ▼                                   ▼
                                     Postgres flight_enriched ──► FastAPI REST ──► React app :8000
```
Every topic is Avro, with schemas in `schemas/` registered in Schema Registry. The same files generate the Java classes.

## Prerequisites
- Confluent Platform 8.3.2 extracted at `~/confluent-8.3.2`, or set `CONFLUENT_HOME` to wherever you extracted it
- Java 21, Gradle, Node, PostgreSQL 18 running on :5432 with a database named `billiontracker`
- `.env` with the keys listed in `.env.example`:
  - `x-rapidapi-key`
  - `openweather_api_key`
  - `google_maps_api_key`, with the **Maps JavaScript API** enabled. It's used only by the browser's live map; no map images are stored.
  - `post_gres_password`

## Configure people and aircraft
Edit `config/billionaires.csv`, which has one row per aircraft:
```csv
name,icon,icon_credit,icon_source_url,x_username,hex,registration,aircraft_type
Elon Musk,img/elon_musk.jpg,"Photo: <author>, CC BY-SA 4.0, via Wikimedia Commons",https://commons.wikimedia.org/wiki/File:<file>,elonmusk,a835af,N628TS,Gulfstream G650ER
```
`icon` is the photo used as the map marker. `icon_credit` and `icon_source_url` are shown under the owner's name on the page. Wikipedia and Wikimedia Commons photos are usually Creative Commons licensed, which requires crediting the author and license. Copy both from the photo's Commons page (click the image on Wikipedia, then **More details**). Wrap the credit in quotes if it contains commas. For a person with several planes, add one row per plane and repeat their name, icon and x_username. Only `hex` is required, and the file opens fine in Excel or Numbers. Restart the producer after editing. You can also add aircraft from the UI; to give one a photo or an X account, add it to this file.

## Update schedule
- **Positions and weather:** every hour (`POLL_SECONDS`, default 3600).
- **X posts:** once a day, just after midnight local time. The producer reads each person's original posts and quotes from the day that ended (no replies, no reposts) and publishes the one with the most likes + reposts + replies. That's one post per person per day, and each post is read only once. If the producer wasn't running at midnight, it picks the missed day when it starts.

The producer checks every minute whether anything is due. It records when each update last ran and is next due in `.state/updates.json` and `.state/x_posts.json`; the bar under the site's top bar shows these times (`GET /api/status`).

Each post is placed using the time it was **created**, not the time it was fetched: the jet's position at or before that moment. With hourly position updates that position is at most about an hour old while the jet is reporting (status `matched`); if the jet was parked or out of coverage, it's the last known position (`last_known`). Flights shorter than an hour can fall between two updates and won't show as a flight path.

## X posts: the top post per day
```
X API ─► x.posts (record time = post time)
           └─► Streams: leftJoin VERSIONED flight.enriched ("where was the jet at that time")
                 └─► x.posts.located ─► Connect ─► Postgres x_posts ─► API / WebSocket ─► map pins
```
- **Location:** the post is placed at the jet's last position at or before the post time. Kafka Streams looks this up in a versioned KTable that keeps 7 days of history.
  - `matched`: that position is less than 60 minutes old.
  - `last_known`: the position is older, for example because the jet was parked. The person is assumed to still be there.
  - `unknown`: no earlier position exists.
  
  This is the **jet's** position; the person may not be on board.
- **Spend cap:** X charges about $0.005 per post read, and one day of his posts is about 20–40 reads. Estimated spend is tracked in `.state/x_posts.json`. Polling stops for the day once `X_BUDGET_USD` (default $1.00 a day) is spent, and the budget resets at local midnight, just before the daily pick. The estimate is the app's own. X also enforces the **spend cap set in the X developer console**, which must allow at least about 30 × `X_BUDGET_USD` a month; if X reports that cap is reached, posts pause until it's raised or resets.
- **When X stops answering:** if X refuses requests (credits used up, the console's monthly spend cap reached, or a bad token), the producer logs it once, pauses X polling and tries again 24 hours later, also across restarts. Positions and weather keep updating, the latest stored posts stay on the site, and the posts panel says new posts are paused.
- After changing the Streams topology, reset the Streams app so the new stores rebuild from the start of the topics:
  ```bash
  kafka-streams-application-reset --bootstrap-server localhost:9092 --application-id billiontracker-enricher \
    --input-topics flight.positions,weather.observations,tracked.aircraft,flight.enriched --force
  rm -rf .streams-state/billiontracker-enricher
  ```

## Post history and backfill
Every post is kept permanently in Postgres (`x_posts`), together with where the person's jet was when it was posted. In **History → Post archive** visitors can scroll back through every stored post, filter by person, and click a post to see the jet's location on the map. The endpoint is `GET /api/history/posts?hex=…&before=…`, paged 50 at a time.

To fill the site before the first midnight pick, fetch each billionaire's most recent original post once:
```bash
python -m producer.backfill_posts      # ~$0.025 per X account; counts toward today's X_BUDGET_USD
```
A post's location is the jet's last position **at or before** the post time. If tracking started after the post, the location is shown as unknown rather than guessed from a later position.

## History (last 30 days)
The **History** tab lets visitors pick any of the last 30 days. For that day it shows:
- every tracked person's posts, each with the jet's location at the time it was posted
- each jet's flight path, as a coloured line
- where each jet was at the end of the day

Days follow the visitor's own time zone.

- **Data:** comes from Postgres, which keeps everything (Kafka itself only keeps 7 days). The endpoints are `GET /api/history/days?tz=…` and `GET /api/history/{YYYY-MM-DD}?tz=…`, and they only answer for the last `HISTORY_DAYS` (default 30).
- **No backfill:** history starts when tracking started. ADS-B Exchange's hex endpoint has no past positions, and backfilling 30 days of X posts would cost far more than the X budget.
- **Indexes:** `scripts/setup-web-db-user.sh` also creates the indexes the day queries use. Re-run it once both tables exist.

## Run
```bash
# 1. Infrastructure (each command starts a service in the background; logs go to logs/)
./scripts/start-kafka.sh
./scripts/start-schema-registry.sh
./scripts/start-connect.sh
./scripts/register-connector.sh          # registers connect/*.json; safe to re-run

# 2. Apps: one terminal each
source .virtual_env/bin/activate
python -m producer.producer              # polls APIs → Kafka
(cd streams && ./gradlew run)            # Kafka Streams enricher
uvicorn api.main:app                     # REST + WebSocket + web app

open http://localhost:8000
```
Frontend development with hot reload: `cd web && npm run dev`, then open http://localhost:5173. Vite proxies `/api` and `/ws` to :8000. After changing the frontend, run `npm run build` so :8000 serves the new version.

Stop the infrastructure with `./scripts/stop-all.sh`.

## Useful checks
```bash
source scripts/env.sh
kafka-topics --bootstrap-server localhost:9092 --list
curl localhost:8081/subjects
curl localhost:8083/connectors/postgres-sink-enriched/status
curl localhost:8083/connectors/postgres-sink-x-posts/status
kafka-avro-console-consumer --bootstrap-server localhost:9092 --topic flight.enriched \
  --formatter-property schema.registry.url=http://localhost:8081
psql -h localhost -U postgres billiontracker -c 'select hex, owner, ts, temp_c from flight_enriched order by ts desc limit 5'
```

## Discussion board
Below the map are two boards side by side (stacked on phones):
- **Discussion:** visitors write text posts and react to them (👍 ❤️ 😂 😮 😢, one of each per visitor; click again to undo). Posts are shown oldest first, 50 at a time.
- **Feature ideas:** a permanent board asking "What features would you like to see added to this app?". Visitors suggest ideas and upvote or downvote them, one vote per visitor per idea, which they can change or take back. Ideas are sorted by score (upvotes minus downvotes), highest first.

On both boards anyone can reply to a post or an idea. Replies are one level deep and shown oldest first under the post. No account is needed: reactions and votes are tied to an anonymous random cookie.

- **Rules** (`api/moderation.py`): text only, up to 1,000 characters. Posts and names are rejected if they contain links, web or e-mail addresses, image or HTML markup, swear words, sexual language or slurs. Disguised spellings like `f.u.c.k`, `sh1t` and `fuuuck` are caught too. The word list is `config/blocked_words.txt`; edit it and restart the API.
- **Bot check:** before posting, the browser must click **Verify I'm human**. This solves a small proof-of-work puzzle signed by the server, which takes about a second and works for one post only. There's also a hidden field that form-filling bots fill in, a minimum time before posting, and a limit of one post every 30 seconds and 10 per hour for each visitor. All of it runs on this Mac, with no third-party service.
- **Storage:** Postgres tables `discussion_posts` (both boards and their replies: `board`, `parent_id`), `discussion_reactions` and `discussion_votes`, created by `scripts/setup-web-db-user.sh`. The API's database user can write to these three tables only; everything else stays read-only.
- **Moderation:** hide a post, idea or reply (hiding a post hides its replies) with `curl -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" localhost:8000/api/discussion/<id>`.

## Settings (`.env`)
| Variable | Default | |
|---|---|---|
| `POLL_SECONDS` | 3600 | How often positions and weather are updated (1 hour) |
| `X_BUDGET_USD` | 1.00 | Cap on estimated X API spend per day (resets at local midnight) |
| `X_BLOCK_HOURS` | 24 | Length of each block that gets one top post per person (must divide 24); 24 = one pick at midnight |
| `DISCUSSION_POW_BITS` | 18 | Bot-check difficulty; each extra bit doubles the work |
| `DISCUSSION_SECRET` | random at startup | Key that signs bot-check challenges; set it to keep challenges valid across restarts |

## Go public (read-only, from this Mac)
The public site is **read-only**. Visitors can browse, but adding or removing aircraft returns 403 and the controls are hidden. You manage aircraft by editing `config/billionaires.csv` and restarting the producer. Only the API (`localhost:8000`) is exposed, through a Cloudflare Tunnel; Kafka, Schema Registry, Connect and Postgres stay private, and no router ports are opened.

**One-time setup**
```bash
brew install cloudflared
./scripts/setup-web-db-user.sh     # read-only Postgres user for the API (password saved to .env)
```
In Google Cloud Console, restrict the Maps key under **Application restrictions → Websites** to your public URL. Also set **daily quotas** for the Maps JavaScript API, because each visitor's map load is billed.

**Start**
Start the infrastructure, the producer and the Streams app (see **Run**), then:
```bash
./scripts/start-public.sh          # prints https://<random>.trycloudflare.com
```
The quick-tunnel URL changes every time you restart. For a permanent address on your own domain, use a Cloudflare account and a named tunnel:
```bash
cloudflared tunnel login
cloudflared tunnel create billiontracker
cloudflared tunnel route dns billiontracker tracker.example.com
# ~/.cloudflared/config.yml:  tunnel: billiontracker / ingress: hostname tracker.example.com → http://localhost:8000, then service: http_status:404
CLOUDFLARE_TUNNEL=billiontracker ./scripts/start-public.sh
```

**What protects the site** (`api/security.py`)
- `READ_ONLY=true` is the default. Changes are still possible from your own terminal if you set `ADMIN_TOKEN`:
  `curl -X POST localhost:8000/api/aircraft -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' -d '{"hex":"…"}'`
- Rate limits are per visitor IP (Cloudflare's `CF-Connecting-IP`): 120 requests a minute, at most 4 live connections per IP and 200 in total.
- The API docs are hidden, and security headers are set.
- The API connects to Postgres as `billiontracker_web`, which can only SELECT, uses read-only transactions, has a 3-second query timeout and a 20-connection cap.
- The site is only up while your Mac is awake and online. The script runs `caffeinate`, but closing the lid still puts the Mac to sleep.
