# BiliHUD Live Control Design

## Context

BiliHUD is currently a PyQt6 overlay focused on Bilibili live danmaku display, danmaku sending, QR login, and game-mode click-through behavior. The reference repository in `temp/` implements live room control in a Tauri/React app.

The reference implementation does not fetch a stream key through a read-only endpoint. It starts a live session with `POST https://api.live.bilibili.com/room/v1/Room/startLive`, and the successful response contains RTMP/SRT stream addresses and keys.

Observed reference flow:

- `getHomePageLiveVersion` returns `curr_version` and `build`.
- App requests are signed with fixed `appkey/appsec`: sort params, append `appkey`, then add `sign=md5(query + appSec)`.
- `Room/startLive` receives `room_id`, `platform=pc_link`, `backup_stream=0`, `csrf`, `csrf_token`, `area_v2`, `version`, `build`, and `ts`.
- Success code `0` returns `data.rtmp.addr`, `data.rtmp.code`, and optional `data.protocols[]` entries for additional RTMP/SRT credentials.
- Code `60024` requires QR verification using `data.qr`.
- Code `60043` requires face verification.

## Goal

Add a complete but scoped live control feature to BiliHUD:

- Users can set live title and area.
- Users can start and stop live streaming.
- After a successful start, users can view and copy RTMP/SRT stream addresses and keys.
- The overlay remains focused on danmaku display; live controls live in a separate dialog opened from the system tray.

## Non-Goals

- Do not redesign the main danmaku overlay.
- Do not save stream keys or addresses to disk.
- Do not add GUI automation tests in this change.
- Do not auto-open browser URLs for verification.
- Do not auto-stop live streaming when the live control dialog closes.

## UX Design

Add a system tray menu action named "直播控制". It opens a new `LiveControlDialog`.

The dialog contains:

- Login status message.
- Room ID input, defaulting to the current BiliHUD room ID.
- Live title input.
- Two-level area selection: parent category and child area.
- Actions:
  - "更新标题"
  - "更新分区"
  - "开始直播"
  - "停止直播"
- A credentials section shown after start succeeds:
  - Rows for `rtmp-1`, additional `rtmp-n`, and `srt-n`.
  - Each row has address, key, and copy buttons.
- Verification state:
  - `60024`: show a QR code dialog using `data.qr`; user retries start after verification.
  - `60043`: show a face-auth URL derived from the logged-in UID if available, with a copy button.

The main overlay header stays unchanged except for any future room ID synchronization needed by shared config.

## Architecture

### New Module: `src/bilihud/live_api.py`

Responsibilities:

- Provide live control HTTP API wrappers.
- Own app signing helpers.
- Normalize stream credentials.
- Avoid any PyQt dependency.

Suggested public functions/classes:

- `app_sign(params: Mapping[str, str]) -> str`
- `parse_stream_credentials(start_live_data: Mapping[str, Any]) -> list[StreamCredential]`
- `get_area_list(session: aiohttp.ClientSession) -> list[ParentArea]`
- `get_live_version(session: aiohttp.ClientSession) -> LiveVersion`
- `update_room_title(session: aiohttp.ClientSession, room_id: int, title: str) -> None`
- `update_room_area(session: aiohttp.ClientSession, room_id: int, area_id: str) -> None`
- `start_live(session: aiohttp.ClientSession, room_id: int, area_id: str, version: str, build: str) -> StartLiveResult`
- `stop_live(session: aiohttp.ClientSession, room_id: int) -> None`

`start_live` should return the raw Bilibili `code`, `message`, and `data` because verification codes require custom UI handling instead of generic exception behavior.

### Auth Reuse

Move the existing cookie-loading logic from `DanmakuClient.start()` into `AuthManager`, for example:

- `AuthManager.load_auth_cookies(prefer_keyring: bool = True) -> tuple[dict[str, str], bool]`
- `AuthManager.create_authenticated_session() -> aiohttp.ClientSession`

The danmaku client and live control dialog both use the same authentication path. This keeps QR-login/keyring/browser-cookie behavior consistent.

The session must expose `SESSDATA` and `bili_jct` to live API requests. If `bili_jct` is unavailable, live start/update actions remain disabled.

### New UI: `src/bilihud/live_control_dialog.py`

Responsibilities:

- Load and save live control form configuration.
- Drive async API calls through `qasync`.
- Render and copy stream credentials.
- Present user-facing errors without crashing the window.

Saved config keys:

- `room_id`
- `live_title`
- `live_parent_area_id`
- `live_area_id`

Credentials stay in memory only.

## Data Flow

Dialog open:

1. Load config.
2. Load authenticated cookies/session.
3. Extract `bili_jct`; if missing, show login-required state.
4. Load area list.
5. Populate form fields.

Start live:

1. Validate login, title, room ID, and area.
2. Save current form config.
3. Update title.
4. Update area.
5. Get live version.
6. Call `start_live`.
7. If code is `0`, parse and render credentials.
8. If code is `60024`, show QR verification dialog.
9. If code is `60043`, show face-auth verification message/link.
10. Otherwise show the API message and code.

Stop live:

1. Validate login and room ID.
2. Call `stop_live`.
3. On success, mark not streaming and clear the credentials display.
4. On failure, keep current credentials visible and show the error.

## Error Handling

- Missing login or missing CSRF: disable mutating actions and show "请先扫码登录".
- Empty title: disable start and update-title actions.
- Missing area: disable start and update-area actions.
- Network/API failure: show a concise error in the dialog and log details with `logging`.
- `60024`: show QR verification; user retries manually.
- `60043`: show face-auth link and copy action.
- Dialog close does not stop live.

## Security And Privacy

- Do not write stream credentials to config, logs, or tests.
- Copy buttons copy exactly the selected address/key.
- Avoid printing raw start-live response data because it contains stream keys.
- Existing cookie privacy behavior remains unchanged.

## Testing

Add unit tests for logic that does not require live Bilibili network access:

- `app_sign()` output is stable and parameter sorting is deterministic.
- `parse_stream_credentials()` extracts the primary `data.rtmp` credential.
- `parse_stream_credentials()` extracts additional valid RTMP/SRT `data.protocols` entries.
- Invalid protocol entries with missing `addr` or `code` are skipped.
- Extracted stream labels are deterministic: `rtmp-1`, `rtmp-2`, `srt-1`, etc.

Add auth tests with mocks if cookie-loading is moved:

- Keyring cookies are preferred when present.
- Browser cookies are used when keyring cookies are absent.
- Missing cookies produce an empty auth state without crashing.

Manual verification:

- Open the dialog from tray.
- Login-required state appears when no valid cookies exist.
- Area list loads after login.
- Start-live button enables only with title and area.
- Verification codes display the right UI.
- Successful start shows copyable stream credentials.
- Stop live succeeds without closing the dialog.

## Implementation Notes

- Keep `live_api.py` independent from PyQt so tests stay simple.
- Keep the dialog layout compact; BiliHUD is a utility app, not a full streaming dashboard.
- Reuse the current `utils.load_config()` and `utils.save_config()` helpers.
- Preserve existing `temp/` reference repository as untracked input; do not commit it.
