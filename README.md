# Family Photos

A self-hosted photo library for the home server. [Immich](https://github.com/immich-app/immich) v3.3.5 holds the pictures, faces, places, and dates. A small timeline service in front of it is the page the family actually opens: a horizontal collage you scroll through time, with a date scrubber and filters for person, place, and date together.

The timeline is a FastAPI service plus a short browser script. A virtualized sideways strip is a poor fit for HTMX round-trips, and the Python side still matches the family portal.

Immich is reached only from the timeline container. The browser gets thumbnails and previews from this service. The API key never leaves the server, and originals are never requested.

## Run this on the server, in this order

Do this on the Fedora box, from a checkout of this repo. Use rootful Docker (`sudo docker`, or your user in the `docker` group). SELinux stays enforcing. You want Compose v2 (`docker compose`, the `docker-compose-plugin` package). The `name:` key in the compose file is not valid in Compose v1.

1. **Configure.**

   ```bash
   cp .env.example .env
   openssl rand -hex 24
   ```

   Put that hex string in `DB_PASSWORD` (letters and digits only). Set `TZ` to the household timezone before any import. Point `UPLOAD_LOCATION` at a large disk and `DB_DATA_LOCATION` at a local SSD, not a network share. Set `IMMICH_PUBLIC_URL` to the address a phone will use for the Immich UI later (LAN IP or Tailscale name). Leave `IMMICH_API_KEY` empty for now.

   Ports `80`, `3000`, `3001`, and `8080` are already taken (portal, AdGuard Home, Number Knockout, Minecraft manager). This stack publishes Immich on **2283** and the timeline on **2284**. Change `IMMICH_PORT` and `TIMELINE_PORT` if those are taken too. The machine-learning container listens on 3003 inside the Docker network only, so it does not clash with AdGuard or Number Knockout.

2. **Create the directories.**

   ```bash
   mkdir -p data/library data/postgres data/icloud data/icloud-session
   ```

   `data/postgres` must be empty the first time. Postgres refuses to initialize into a directory that already has files.

3. **Start Immich and the timeline.** This does not start the iCloud downloader.

   ```bash
   docker compose up -d --build
   ```

   Wait until `family_photos_immich_server` is healthy (`docker compose ps`). The first start relabels the bind-mounted directories for SELinux. On a large existing photo tree that walk takes a while.

4. **Create the Immich admin.** Open `http://<server>:2283`. The first account registered is the admin. Do that before anyone else can reach the port.

5. **Create an API key.** In Immich, open the user menu and create an API key with **asset.read**, **asset.view**, and **person.read**. Put it in `.env` as `IMMICH_API_KEY`. Confirm `IMMICH_PUBLIC_URL` is a URL the phones can open, then recreate the timeline container so it sees the key:

   ```bash
   docker compose up -d --build timeline
   ```

6. **Import the pictures.** Follow [Import](#import) below. Use either an external library or the upload CLI for a given folder, not both.

7. **Wait for Immich's jobs, then name people.** Administration → Jobs. Metadata extraction, thumbnail generation, face detection, and smart search all have to catch up. A large library on CPU can sit here for a long time. Then name people ([below](#name-people)). City, state, and country fill in from GPS during the metadata job. Photos with no GPS still appear, just without a place.

8. **Open the timeline** at `http://<server>:2284`. It lands on the newest photos. Scroll sideways for older ones, or drag the date line.

9. **Add the portal card** ([snippet](#family-portal-card)). Do that in the family portal repo, not in this one.

Remote access stays on Tailscale or Teleport. Do not port-forward 2283 or 2284 on the router.

## What the timeline does

- Photos are grouped by the camera's local day, then by month and year. Scrolling is horizontal. Only the columns near the viewport are in the DOM, and more days load as you approach either end.
- The range under the strip scrubs from the earliest matching photo to the latest. "Go" jumps to a date. The chosen day sits at the left edge.
- Filters combine. People are AND (Mom and Dad means both). City, state, and country are the names Immich stored. From/To are inclusive local dates. Words ("beach") go to Immich's smart search and are ranked, then shown in date order. Smart search returns the top 200 matches, not the whole library. Person, place, and date browsing pages through everything.
- A photo opens larger, with its people, place, and date, and a link to that asset in Immich (`/photos/<id>`). Videos show the preview frame. Playback stays in Immich.
- Collage tiles are Immich thumbnails. The large view is the Immich preview. `/media/...` is this service, proxying those renditions with the API key. `size=original` and `size=fullsize` are rejected, including when Immich would redirect a thumbnail request at the original file.

Example: Mom at the beach in summer 2019. Select Mom, set From `2019-06-01` and To `2019-08-31`, type `beach`, and press Show. If Immich has a city for that trip, you can set the city instead of, or as well as, the word.

## Storage

| Path | What lands there |
| --- | --- |
| `UPLOAD_LOCATION` | Immich's upload library, plus JPEG thumbnails and previews for every asset, including external libraries, plus transcoded video |
| `DB_DATA_LOCATION` | Postgres. Local SSD only. Not NFS |
| External library mounts | Your existing files, read in place, not copied |
| `ICLOUD_DOWNLOAD_LOCATION` | Originals from icloudpd, if you use that |
| `model-cache` volume | Machine-learning models, a few GB |

External-library originals are not copied into `UPLOAD_LOCATION`. Generated JPEG thumbnails and previews still are. Budget roughly 1–2 MB of generated files per photo, so 100,000 photos is on the order of 100–200 GB in the upload location, before video transcodes. Leave more headroom than that. Video transcoding can add another large copy.

Uploading the same tree with the CLI stores a second full copy of the originals inside `UPLOAD_LOCATION`. For a huge iCloud library, prefer an external library.

Postgres grows with the catalog and with smart-search embeddings, from a few GB into the tens of GB on a very large library. The `model-cache` volume is a few GB.

If the database disk is a spinning drive, uncomment `DB_STORAGE_TYPE: "HDD"` on the database service.

## SELinux

Every data bind mount uses `:Z`, which gives that container a private label. `/etc/localtime` is mounted read-only without `:Z`, because a private label on that file breaks the host timezone.

`:Z` on a directory two containers use at once makes them lock each other out. icloudpd and Immich are set up so they are not both running against the download directory: the iCloud service is a one-shot `docker compose run`. If you later leave `icloudpd --watch-with-interval` running while Immich is up, change both mounts of that path from `:Z` to `:z` (shared label).

The first start of a large external library spends a long time relabeling files. Do not turn SELinux off, and do not add `security_opt: label:disable`. If a container reports "Permission denied" on a mount, check `sudo ausearch -m avc -ts recent` and confirm the compose line still has `:Z`.

## First-time Immich

- `http://<server>:2283` after step 3.
- Register the admin. That first user owns the library the timeline reads. The API key has to belong to that user (or another user you want the timeline to see).
- User menu → API Keys → New key. Permissions: `asset.read`, `asset.view`, `person.read`. If a timeline request says the key is missing a permission, add it. An all-access key is fine on this private server if you would rather not pick through the list.
- Put the key in `.env` and run `docker compose up -d --build timeline`.
- `http://<server>:2284/healthz` returns `{"status":"ok","immich":"configured"}`. The portal probe uses this URL. It stays 200 when the process is up, including before the key is set (`immich` is `unconfigured` then). It does not fail the card when a background job is still running.

## Import

Pick one home for each file. The same bytes in the upload library and in an external library show up as a duplicate pair.

### iCloud, on the server (icloudpd)

The downloader is pinned to `icloudpd/icloudpd:1.32.3` and is not started by `docker compose up`. Apple's web session needs an interactive 2FA code the first time, and again whenever Apple expires it (weeks or months later). An app-specific password does not replace that session.

On the Apple ID, before this will work:

- Settings → Apple ID → iCloud → Access iCloud Data on the Web: on
- Settings → Apple ID → iCloud → Advanced Data Protection: off

Advanced Data Protection blocks the photos web API. Do not put the Apple password on the command line. icloudpd prompts for it, and a `--password` flag would land in shell history and in `docker inspect`.

Authorize, then download originals, including Live Photo videos:

```bash
docker compose --profile icloud run --rm -it icloud \
  icloudpd \
  --username you@icloud.com \
  --directory /data \
  --cookie-directory /cookies \
  --auth-only

docker compose --profile icloud run --rm -it icloud \
  icloudpd \
  --username you@icloud.com \
  --directory /data \
  --cookie-directory /cookies \
  --folder-structure '{:%Y/%m}' \
  --size original \
  --live-photo-size original
```

Cookies stay in `ICLOUD_COOKIE_LOCATION`. Re-run the `--auth-only` command when a later download says the session is no longer valid.

Do not pass `--auto-delete` unless you want files removed from the server when they disappear from iCloud. Do not pass `--set-exif-datetime` on the first import. The default download is Apple's original file, and that file already has the capture date and GPS. `--set-exif-datetime` rewrites metadata. Use it only to repair files that have no EXIF date, and do that before Immich scans them.

Rerunning the download skips files already saved. icloudpd dedupes by file name in the download directory. That is separate from Immich's checksum dedupe.

To keep pulling new iCloud photos, add `--watch-with-interval 3600` and read the SELinux note above if that container stays up next to Immich.

There is a maintainer notice on the icloudpd project. The commands above match v1.32.3. Check the release notes before bumping the image tag.

### iCloud, from a Mac

On a Mac signed into the same Apple ID, Photos → File → Export → **Export Unmodified Originals**. That writes the original HEIC (and the Live Photo MOV beside it) with the capture date and GPS still in the file.

The other item, Export, builds new JPEGs. Those lose the original capture time and location often enough that the timeline will file them under the export day. Don't use that one.

Copy the folder to the server (`rsync` over Tailscale is enough) and treat it as an external library or a CLI upload, same as any other collection.

### Load the files into Immich

**External library (right choice for the big historical dump).** The files stay where they are.

In `.env`, point `EXTERNAL_LIBRARY_1` (or 2, or 3) at the host directory. The defaults `./external/library1` and so on are empty placeholders so Compose has something to mount. Restart the server after changing them:

```bash
docker compose up -d immich-server
```

In Immich: Administration → External Libraries → Create library → add the **container** path, not the host path:

| Host (`.env`) | Path inside Immich |
| --- | --- |
| `EXTERNAL_LIBRARY_1` | `/mnt/media/library1` |
| `EXTERNAL_LIBRARY_2` | `/mnt/media/library2` |
| `EXTERNAL_LIBRARY_3` | `/mnt/media/library3` |

Save, then Scan. The mounts are read-only (`:ro`), so Immich cannot delete the originals. Trashing an item in the UI is a database flag.

Do not nest these directories. An import path inside another import path makes Immich see the same file twice. One library can have several import paths, and a file that appears in two paths of the **same** library is added once. Two libraries that both contain the same bytes become a duplicate group.

Turn on a scan interval on the library if files will keep arriving. Otherwise scan again after each copy.

**Upload with the Immich CLI (right choice when Immich should own the file).** This copies bytes into `UPLOAD_LOCATION`.

```bash
docker run --rm -it \
  -v /path/on/the/host:/import:ro,Z \
  -e IMMICH_INSTANCE_URL=http://<server>:2283/api \
  -e IMMICH_API_KEY=<the same key> \
  ghcr.io/immich-app/immich-cli:v3.3.5 \
  upload --recursive --dry-run /import
```

Drop `--dry-run` when the list looks right. The CLI hashes each file and Immich rejects bytes it already has, so a second upload does not create a second timeline entry. From the server itself, `http://immich-server:2283/api` only works on the Compose network. From the host, use the published port.

**Phone backup from here on.** Point the Immich mobile app at `http://<tailscale-host>:2283` and let it back up the camera roll into the upload library. Those photos join this timeline. Don't also keep downloading that same camera roll with icloudpd, or every new photo arrives twice.

### Duplicates

- CLI upload: checksum match. The second copy is skipped.
- One external library: a path that is listed twice still imports the file once. Nested paths do not get that protection in the way people expect. Keep the three mounts side by side.
- Upload library plus external library, same bytes: Immich groups them as duplicates. Review that queue in Immich and keep one side.
- Live Photo pairs are not duplicates of each other. See below.

### HEIC and Live Photos

Keep the original HEIC. Immich builds a JPEG thumbnail and a JPEG preview. The timeline only asks for those, so a phone that cannot decode HEIC still shows the collage.

A Live Photo is a still plus a MOV that share a content identifier. Leave both files next to each other. icloudpd `--live-photo-size original` and "Export Unmodified Originals" both do that. Immich links them and hides the movie from the main timeline, so the strip shows the still once, with a Live badge. Converting HEIC to JPEG before import drops that link and can drop GPS.

The large view does not play the motion clip. Open the photo in Immich for that. The collage never requests the original file.

### Dates and places

Immich reads the capture time from EXIF (`DateTimeOriginal`, then the other creation tags) and the GPS coordinates from the same metadata. The file's modification time is only a fallback, and for a file just written by a download that fallback is "today". That is why the originals have to stay unmodified, and why `TZ` should be set before the first scan.

The collage groups by the camera's local clock, which is what Immich stores as `localDateTime`. A From/To filter keeps those local days. Immich's `takenAt` field is a UTC instant, so the timeline widens the query by 36 hours and then drops anything outside the local window. A picture taken late on 31 August still counts as 31 August.

City, state, and country come from Immich's own geodata during the metadata job. Nothing else has to be installed for that. No GPS means no place, and the photo is still searchable by person and date.

## Name people

Face detection only produces clusters. You give them names.

1. Wait until the face-detection job has made a dent. CPU on a large library is slow. That is normal.
2. In Immich, open Explore → People.
3. Open a cluster and set the name the family uses (Mom, Dad, and so on).
4. If the same person appears as two clusters, merge them from that person's page.
5. Hide people you do not want in the filter row.

The timeline's people row lists named people only, with their face thumbnail proxied through this service. The count of unnamed faces is a hint, not a list. After you name someone, reload the timeline.

Selecting several people keeps photos that contain every one of them.

## Auth

There is no account on the timeline. If you can open the port, you can see the library. That is acceptable on the home LAN, on Tailscale, or behind Teleport. It is not acceptable on a port forwarded from the router.

- **Tailscale.** Leave the binds at `0.0.0.0` and use the tailnet IP or MagicDNS name in `IMMICH_PUBLIC_URL` and in the portal's `tailscale_url`. Tailscale does not need those ports opened in firewalld to the public zone. Do not add a router forward.
- **Teleport.** Run an application service at `http://127.0.0.1:2284` (and 2283 for Immich admin). Set `TIMELINE_BIND` and `IMMICH_BIND` to `127.0.0.1` so the ports are not on the LAN. The timeline process is started with `--proxy-headers`. Teleport is the login. You do not need a second one.
- **Optional basic auth.** Set both `TIMELINE_BASIC_AUTH_USER` and `TIMELINE_BASIC_AUTH_PASSWORD`, then recreate the timeline container. Every page and `/api` route asks for that password. `/healthz` stays open so the portal probe does not have to send a password. This is a latch, not a substitute for keeping the port off the public internet. The Immich API key is still only in the container environment.

## Hardware acceleration

Machine learning defaults to CPU. That is the image tag with no suffix, and it is what you should run until the library is importing cleanly.

For an Intel iGPU, which is the usual Fedora desktop chip, Immich's OpenVINO or Quick Sync path is the one to read: [ML hardware acceleration](https://docs.immich.app/features/ml-hardware-acceleration). Download `hwaccel.ml.yml` and `hwaccel.transcoding.yml` from the **v3.3.5** tag into this directory (the same tag as `IMMICH_VERSION`). Uncomment the `extends` block on `immich-machine-learning` and, for the ML image, append the matching suffix (`-openvino`, `-cuda`, `-rocm`, `-armnn`, `-rknn`). Do the transcoding file separately if you also want GPU video transcode. A hwaccel file from a newer Immich tag does not belong with this pin.

NVIDIA needs the NVIDIA container toolkit installed on the host before the `cuda` image will see the GPU.

## Family portal card

Paste this into the portal's `config/apps.yaml` (that edit belongs in the portal repo). Match `TIMELINE_PORT`. `healthcheck_url` is called by the portal on the server, so it stays on localhost. `tailscale_url` is where a visitor already on Tailscale is sent. If the portal config has a single `tailscale_host`, this card's host should be that name.

```yaml
# tailscale_host: your-server.your-tailnet.ts.net
- id: photos
  name: Family Photos
  url: http://127.0.0.1:2284
  tailscale_url: http://your-server.your-tailnet.ts.net:2284
  healthcheck_url: http://127.0.0.1:2284/healthz
  icon: "📷"
  audience: everyone
```

If the other cards use an icon name instead of an emoji, use that same style. `📷` is only there so the field is set.

## Upgrade the pin

`IMMICH_VERSION` is `v3.3.5` on purpose. Read the Immich release notes, dump the database, then move the pin:

```bash
docker exec family_photos_postgres pg_dump -U postgres immich > immich-backup.sql
```

Set the new version in `.env`, keep the timeline's client assumptions in mind (it speaks the v3.3 search filter shape: `filter`, `orderBy`, `cursor`), then:

```bash
docker compose pull
docker compose up -d --build
```

## Development

```bash
cd timeline
pip install -e ".[dev]"
pytest
```

CI on GitHub Actions runs that test suite and `docker compose config`. The tests mock Immich. They cover the API client (including the refusal to follow a redirect to an original file) and the grouping and filter rules, including Mom plus a place plus summer 2019.
