# Family Photos

[Immich](https://github.com/immich-app/immich) v3.4.0 holds the pictures. The timeline on port 2284 is the page the family scrolls. Photos stay in iCloud, and this server copies new ones in every hour.

```bash
./setup.sh
```

Open http://192.168.1.3:2283, create the admin (the first account), then an API key. The key needs `asset.read`, `asset.view`, `person.read`, `user.read`, `library.read`, `library.create`, and `library.update`. Permission `all` is fine on this private server. Put it in `.env` as `IMMICH_API_KEY`.

```bash
./import-icloud.sh you@icloud.com
```

That signs in (Apple asks for the password and a 2FA code only when the saved session is missing or expired), downloads originals including Live Photos from your library and every shared library, and creates the Immich external library itself. The first pass walks each library to the end. It then leaves `icloud-sync` running. An interrupted download does not count as finished, so the next run still walks whatever is left. After a library really finishes, later runs only pull new photos. `./import-icloud.sh --full you@icloud.com` forces another complete pass.

Timeline: http://192.168.1.3:2284

Use the Immich phone app to look at photos, with backup turned off. The hourly sync is already the camera-roll copy, and iCloud keeps the other one. Turning phone backup on as well duplicates every photo.

Before the Apple ID will work: Settings → Apple ID → iCloud → Access iCloud Data on the Web on, and Advanced Data Protection off.

If `docker compose ps` shows `icloud-sync` unhealthy:

```bash
docker compose logs icloud-sync
./import-icloud.sh you@icloud.com
```

`./setup.sh` writes `.env`, generates `DB_PASSWORD` from `/dev/urandom` (openssl is not required), creates the directories, and waits for Immich. The first boot imports map data before the health check answers, which can take several minutes. The wait is built for that.

Ports **2283** (Immich) and **2284** (timeline) stay off 80, 3000, 3001, and 8080. Change them in `.env` if you need to. Do not port-forward them. Use Tailscale or Teleport.

The rest of this file is the detail behind those two commands.

## What the timeline does

- Photos are grouped by the camera's local day, then by month and year. Scrolling is horizontal. Only the columns near the viewport are in the DOM, and more days load as you approach either end.
- The range under the strip scrubs from the earliest matching photo to the latest. "Go" jumps to a date. The chosen day sits at the left edge.
- Filters combine. People are AND (Mom and Dad means both). City, state, and country are the names Immich stored. From/To are inclusive local dates. Words ("beach") go to Immich's smart search and are ranked, then shown in date order. Smart search returns the top 200 matches, not the whole library. Person, place, and date browsing pages through everything.
- A photo opens larger, with its people, place, and date, and a link to that asset in Immich (`/photos/<id>`). Videos show the preview frame. Playback stays in Immich.
- Collage tiles are Immich thumbnails. The large view is the Immich preview. `/media/...` is this service, proxying those renditions with the API key. `size=original` and `size=fullsize` are rejected, including when Immich would redirect a thumbnail request at the original file.

Example: Mom at the beach in summer 2019. Select Mom, set From `2019-06-01` and To `2019-08-31`, type `beach`, and press Show. If Immich has a city for that trip, you can set the city instead of, or as well as, the word.

## Synced folders

`~/sync/pictures` and `~/sync/video` on this server (`/home/funky/sync/pictures` and `/home/funky/sync/video`) can be external libraries. Immich mounts them read-only, so it cannot modify or delete them. The sync tool keeps writing. New files are picked up by Immich's nightly library scan (midnight). Run `./add-sync-libraries.sh` again to scan immediately.

In `.env`:

```bash
SYNC_PICTURES_LOCATION=/home/funky/sync/pictures
SYNC_VIDEO_LOCATION=/home/funky/sync/video
```

Then:

```bash
docker compose up -d --force-recreate immich-server immich-machine-learning
./add-sync-libraries.sh
```

The script is safe to run again. It creates "Sync pictures" and "Sync video" if those container paths are not already libraries, turns the nightly scan on if it was off, and queues a scan. The API key needs the same library permissions as the iCloud import. Confirming the schedule also needs `systemConfig.read` and `systemConfig.update` (or permission `all`). Without those two, the libraries are still created and scanned; Immich's own default is already a midnight scan.

These directories are labeled with the shared `:z` flag, not the private `:Z` flag. Under enforcing SELinux a container cannot read `~/sync` (it is `user_home_t`) unless the files carry a container file type. `:z` is that shared label, so Immich can read them and the sync tool, running as your user, can still read and write. `:Z` would give the files a category only the Immich container may use, and the sync tool would start failing. `:ro` alone, with no label change, is denied by SELinux.

To remove a duplicate, use Immich's Utilities → Duplicates view to see which file it is, then delete that file yourself under `~/sync`. The sync tool propagates the delete to the other machines. Immich drops the asset on the next library scan. Do not delete it inside Immich expecting the sync folder to change; the mount is read-only.

Leave the two variables commented out on a machine that does not have these folders. Compose then mounts empty directories under `./data` and nothing else changes.

## Machine learning port

`IMMICH_PORT=2283` in `.env` is the server's listen port and the host port. Machine learning reads that same variable. Passed through the shared env file, it was listening on 2283 instead of 3003, so face detection never reached it. Compose now sets `IMMICH_PORT=3003` on the machine-learning service only (that overrides `.env`) and sets `IMMICH_MACHINE_LEARNING_URL=http://immich-machine-learning:3003` on the server. Recreate both containers after pulling this change. An `.env` that already contains `IMMICH_PORT=2283` does not need to be edited for this.

## Storage

| Path | What lands there |
| --- | --- |
| `UPLOAD_LOCATION` | Immich's upload library, plus JPEG thumbnails and previews for every asset, including external libraries, plus transcoded video |
| `DB_DATA_LOCATION` | Postgres. Local SSD only. Not NFS |
| External library mounts | Your existing files, read in place, not copied |
| `ICLOUD_DOWNLOAD_LOCATION` | iCloud originals. Mounted read-only into Immich at `/mnt/media/icloud` |
| `model-cache` volume | Machine-learning models, a few GB |

External-library originals are not copied into `UPLOAD_LOCATION`. Generated JPEG thumbnails and previews still are. Budget roughly 1–2 MB of generated files per photo, so 100,000 photos is on the order of 100–200 GB in the upload location, before video transcodes. Leave more headroom than that. Video transcoding can add another large copy.

Uploading the same tree with the CLI stores a second full copy of the originals inside `UPLOAD_LOCATION`. For a huge iCloud library, prefer an external library.

Postgres grows with the catalog and with smart-search embeddings, from a few GB into the tens of GB on a very large library. The `model-cache` volume is a few GB.

If the database disk is a spinning drive, uncomment `DB_STORAGE_TYPE: "HDD"` on the database service.

## SELinux

Private data directories use `:Z`. `/etc/localtime` is mounted read-only without a label change, because a private label on that file breaks the host timezone.

The iCloud download is the exception. icloudpd writes it and Immich reads it at the same time, so those mounts use the shared `:z` label. `./import-icloud.sh` does not ask you to point `EXTERNAL_LIBRARY_1` at that folder. Immich already sees it at `/mnt/media/icloud`.

The first start of a large photo tree spends a while relabeling files. Do not turn SELinux off, and do not add `security_opt: label:disable`. If a container reports "Permission denied" on a mount, check `sudo ausearch -m avc -ts recent`.

## First-time Immich

- `./setup.sh` opens Immich at the address in `IMMICH_PUBLIC_URL` (http://192.168.1.3:2283 unless you changed it).
- Register the admin. That first user owns the library the timeline reads. The API key has to belong to that user (or another user you want the timeline to see).
- User menu → API Keys → New key, as the admin. The timeline needs `asset.read`, `asset.view`, and `person.read`. `./import-icloud.sh` and the hourly sync also need `user.read`, `library.read`, `library.create`, and `library.update`, because creating an external library is an admin call. Permission `all` covers that list.
- Put the key in `.env`. `./import-icloud.sh` recreates the timeline container so it sees the key.
- `http://<server>:2284/healthz` returns `{"status":"ok","immich":"configured"}`. The portal probe uses this URL. It stays 200 when the process is up, including before the key is set (`immich` is `unconfigured` then). It does not fail the card when a background job is still running.

## Import

Pick one home for each file. The same bytes in the upload library and in an external library show up as a duplicate pair.

### iCloud

`./import-icloud.sh you@icloud.com` is the whole import. It uses icloudpd 1.32.3 to download original files and Live Photo movies into `ICLOUD_DOWNLOAD_LOCATION` (default `./data/icloud`), which Compose already mounts read-only at `/mnt/media/icloud`. The script then calls Immich's API: it creates an external library named iCloud, or reuses one that already has that path, and queues a scan. Nothing in the Immich library screen has to be filled in by hand.

`icloudpd --list-libraries` is how the script finds them: `PrimarySync` (your library) and every `SharedSync-*` shared library. Each one is downloaded into its own folder under the iCloud directory (`PrimarySync/2024/03/...`, `SharedSync-.../2024/03/...`) so the same file name in two libraries does not overwrite. Immich scans the parent folder, so one external library covers all of them. A photo that really is in both your library and a shared library can show up twice; Immich's duplicate review is the place to sort that out.

The same command starts `icloud-sync`, which repeats that check every hour (`ICLOUD_SYNC_INTERVAL`, default 3600) and queues another scan afterward. `restart: unless-stopped` brings it back after a reboot. A new shared library is downloaded in full the first time it appears. After a library's full pass exits successfully, a marker is written at `data/icloud-session/full-import-complete/<library>`. Only then do later runs pass `--until-found` (default 10), which stops once that many photos in a row are already on disk. A download that dies in the middle never writes the marker, so the next `./import-icloud.sh` or the hourly sync keeps walking that library. `./import-icloud.sh --full you@icloud.com` ignores the markers and walks every library again.

If year folders from an earlier download are sitting directly in `data/icloud/` and `PrimarySync/` does not exist yet, the script moves those year folders into `PrimarySync/` first. The 390 files from an interrupted run stay put, and the full pass continues with the rest.

Apple's web session is not an app-specific password. It expires every so often. When it does, the sync container logs this and stays up with a failed health check, instead of looping restarts:

```text
Apple iCloud session expired or was rejected.
Photos are not downloading. Immich will not see new iCloud photos until you re-authenticate.
Run this on the server:
  ./import-icloud.sh you@icloud.com
```

That command is also the re-auth. It stops the sync, prompts only if the session is dead, downloads what is still missing, scans, and starts the sync again. Libraries that already have a completion marker stay incremental. `./import-icloud.sh --full you@icloud.com` is the re-auth that also walks every library from the start.

`./import-icloud.sh --once you@icloud.com` downloads and scans without leaving the hourly service running.

Do not turn on icloudpd `--auto-delete` or `--set-exif-datetime`. The download is Apple's original file, which already has the capture date and GPS. `--set-exif-datetime` rewrites metadata. Deleting from iCloud should not delete the server copy.

There is a maintainer notice on the icloudpd project. The image tag is pinned to 1.32.3. Check the release notes before bumping it.

### iCloud, from a Mac

On a Mac signed into the same Apple ID, Photos → File → Export → **Export Unmodified Originals**. That writes the original HEIC (and the Live Photo MOV beside it) with the capture date and GPS still in the file.

The other item, Export, builds new JPEGs. Those lose the original capture time and location often enough that the timeline will file them under the export day. Don't use that one.

Copy the folder to the server (`rsync` over Tailscale is enough) and treat it as an external library or a CLI upload, same as any other collection.

### Load the files into Immich

The iCloud folder is already an external library after `./import-icloud.sh`. The steps below are for other collections, or for a one-time upload when Immich should own the file.

**External library (right choice for another existing collection).** The files stay where they are.

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
  ghcr.io/immich-app/immich-cli:v3.4.0 \
  upload --recursive --dry-run /import
```

Drop `--dry-run` when the list looks right. The CLI hashes each file and Immich rejects bytes it already has, so a second upload does not create a second timeline entry. From the server itself, `http://immich-server:2283/api` only works on the Compose network. From the host, use the published port.

**Immich on the phone, for this house.** Install the app and point it at `http://192.168.1.3:2283` (or the Tailscale name). Leave backup off. iCloud remains the phone's photo library, and `icloud-sync` is what copies it onto this server. Phone backup plus that sync imports the same camera roll twice.

**If you do not use iCloud Photos,** skip `./import-icloud.sh` and turn backup on in the Immich app instead. Those uploads land in `UPLOAD_LOCATION` and show up on the timeline. Do not also point an external library at an iCloud download of that same camera.

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

For an Intel iGPU, which is the usual Fedora desktop chip, Immich's OpenVINO or Quick Sync path is the one to read: [ML hardware acceleration](https://docs.immich.app/features/ml-hardware-acceleration). Download `hwaccel.ml.yml` and `hwaccel.transcoding.yml` from the **v3.4.0** tag into this directory (the same tag as `IMMICH_VERSION`). Uncomment the `extends` block on `immich-machine-learning` and, for the ML image, append the matching suffix (`-openvino`, `-cuda`, `-rocm`, `-armnn`, `-rknn`). Do the transcoding file separately if you also want GPU video transcode. A hwaccel file from a newer Immich tag does not belong with this pin.

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

`IMMICH_VERSION` is `v3.4.0` on purpose. Read the Immich release notes, dump the database, then move the pin:

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

CI on GitHub Actions runs the timeline tests, the iCloud library helper tests, a password-generator check, and `docker compose config`. The tests mock Immich. They cover the API client (including the refusal to follow a redirect to an original file), the grouping and filter rules, and creating or reusing the iCloud external library.
