<p align="center">
  <img src="handset/web/logo.svg" width="120" alt="Pane logo - iPhone screen mirroring for Windows">
</p>

<h1 align="center">Pane — Mirror and Control Your iPhone from a Windows PC</h1>

<p align="center">
  <b>Free, open-source iPhone screen mirroring and remote control for Windows 10 &amp; 11.</b><br>
  See your iPhone screen live on your PC, tap and swipe with the mouse, and type with your computer keyboard —<br>
  over a USB cable <i>or</i> wirelessly over Wi-Fi. No jailbreak, no Mac, no subscription.
</p>

<p align="center">
  <a href="../../releases/latest"><b>⬇ Download Pane.exe for Windows</b></a>
  ·
  <a href="#features">Features</a>
  ·
  <a href="#quick-start">Quick start</a>
  ·
  <a href="#faq">FAQ</a>
</p>

---

**Pane** is like [scrcpy](https://github.com/Genymobile/scrcpy), but for **iPhone on Windows**. It streams the iPhone display to your desktop in real time and sends your mouse clicks, swipes and key presses back to the phone, so you can use iOS apps, reply to messages, play games, record demos or present your phone screen from your laptop or PC.

It is an alternative to paid tools such as ApowerMirror, LonelyScreen, Reflector, AirServer, iMyFone MirrorTo and Phone Link for iPhone — but free, private (everything stays on your own computer and network), and with full touch control instead of view-only AirPlay.

## Features

- 📱 **Live iPhone screen mirroring on Windows**: low-latency HEVC (H.265) video decoded in hardware.
- 🖱️ **Control the iPhone with your mouse**: tap, long-press, drag and swipe directly on the mirrored screen.
- ⌨️ **Type on your iPhone with the PC keyboard**: real keystrokes, including Ctrl+C / Ctrl+V / Ctrl+A / Ctrl+Z mapped to ⌘ shortcuts.
- 📋 **Shared text, image and video clipboard**: PC shortcuts, native iOS Copy sync, HEIC support and inline Notes video extraction.
- 📁 **Paired file sharing over Wi-Fi**: drag-and-drop, Safari companion, QR or camera-free code pairing, progress and cancellation.
- 📶 **Wireless mirroring over Wi-Fi**: pair once over USB, then go cable-free on the same network.
- 🔌 **USB cable mode**: the most stable, lowest-latency connection.
- 🏠 **Hardware buttons & gestures**: Home, App Switcher, Lock/Power, Siri, Volume up/down, Mute, Notification Center, Control Center, back/forward swipes.
- 🖥️ **Realistic iPhone frame** with fit-to-window, zoom, rotate and **full-screen touch mode**.
- 🔁 **One-click restart** if the stream ever freezes.
- 🪟 **Single portable `Pane.exe`**: no Python, no installer, no account.
- 🔒 **Private by design**: no cloud, no telemetry; it only talks to your iPhone.

## Quick start

1. **Download** `Pane.exe` from the [latest release](../../releases/latest).
2. Install Apple's USB driver by installing **[Apple Devices](https://apps.microsoft.com/detail/9np83lwlpz9k)** (or iTunes) from the Microsoft Store.
3. Plug the iPhone into the PC, unlock it and tap **Trust This Computer**.
4. Turn on **Developer Mode** on the iPhone: *Settings → Privacy & Security → Developer Mode → On* (the iPhone restarts).
   If the switch is missing, see the Developer Mode command in [Troubleshooting](#troubleshooting).
5. Run **Pane.exe** and click **Start mirroring**.

### Go wireless (Wi-Fi)

1. While the iPhone is connected by cable, switch Pane to **Wi-Fi** and click **Set up Wi-Fi** (one-time pairing).
2. Unplug the cable. Keep the PC and iPhone on the **same Wi-Fi network** and the iPhone unlocked.
3. Click **Start mirroring**.

> Tip: if the iPhone isn't found over Wi-Fi, set the Windows network profile to **Private** and make sure the router doesn't isolate wireless clients.

## Controls

| Action on PC | Result |
| --- | --- |
| Left click / drag on the mirrored screen | Tap / swipe |
| Hold the left mouse button | Native iOS selection / edit menu, where the app supports it |
| Swipe up from the bottom edge | Home; pause before releasing for App Switcher |
| Deliberate mouse right click | Home |
| Keyboard with forwarding enabled | Type into the focused iPhone field |
| Ctrl+C / Ctrl+X | Copy / cut selected iPhone content to the PC clipboard |
| Ctrl+V | Transfer PC clipboard content and request Paste on iPhone |
| Ctrl+A / Ctrl+Z | Select all / Undo on iPhone |
| Frame side buttons | Lock, Volume up/down, Mute |

Click the destination inside the mirrored iPhone before typing or pasting. Pane’s own inputs and buttons retain normal browser keyboard behavior. Keyboard forwarding can be turned off in the connection panel.

### Shared clipboard: text, images and videos

The Windows server bridges the clipboards directly, so browser clipboard permission is not required.

- **PC → iPhone:** copy text or image pixels on PC, then press **Ctrl+V** in the mirrored destination, or click **Paste PC clipboard**.
- **iPhone → PC:** select content on iPhone and press **Ctrl+C**, or click **Copy selection to PC**. If you already chose Copy in iOS’s native menu, click **Get iPhone clipboard** instead.
- **Native Copy sync:** turn **Sync iPhone → PC** on to transfer new native iOS copies automatically while mirroring is connected. The toggle remembers your choice in that browser. Sync ignores the existing clipboard when monitoring starts and changes Pane itself sent to the phone. Its status appears below the toggle; turn it off to stop automatic PC clipboard updates.
- **Native Paste menu:** click **Send PC clipboard** to update the phone clipboard without inserting anything, then hold on the destination and choose Paste. **Paste iPhone clipboard** requests Paste without replacing the phone clipboard with PC content.

Clipboard notifications appear at the browser’s bottom-right, outside the phone display, with a dismiss button. Completed messages clear after six seconds; warnings remain until dismissed. Notifications also work in full-screen mode.

#### Images

Screenshots and copied image pixels work with iPhone apps that accept images, such as Notes and Messages. PNG, Windows DIB/DIBV5, JPEG and native iPhone HEIC/HEIF images are normalized to PNG. Images returned to Windows also include a DIB fallback for PC apps without PNG clipboard support.

Copies from Messages bubbles and the Photos library are supported: Pane decodes rich (attributed/RTF/HTML) text and images published under any type, and resolves representations the app only promised. If a copy still cannot be read, the sync status lists the types the app offered.

Copy the image itself when possible. Notes/Safari can place pictures inside rich document representations; resolving those can take about a minute. The bridge transfers the first image rather than an entire rich document or multiple images. Copying an image file in Explorer is different from copying its pixels; use the Files panel for ordinary file transfer.

#### Direct video paste

1. In Windows Explorer, copy **one MP4, MOV or M4V file**.
2. Focus a destination inside the mirrored iPhone that accepts video attachments, then press **Ctrl+V** or **Paste PC clipboard**.
3. To use the iOS menu instead, choose **Send PC clipboard**, then native Paste.

The original movie bytes are transferred without transcoding. The destination app must accept the movie format and codec; a successful clipboard transfer cannot make an app support video paste.

**Photos videos:** Photos’ ••• → Copy on a video never hands the movie to a PC (iOS only promises it). When Pane sees such a copy, through native Copy sync, Ctrl+C or **Get iPhone clipboard**, it opens a picker of recent camera-roll videos with thumbnails. Pick the one you copied, and Pane streams the original file and puts it on the Windows clipboard. **Pick iPhone video…** opens the picker any time. iOS doesn’t tell Pane which video was copied, so the picker shows two views. **Recently added, downloaded or opened** lists videos recorded, saved, downloaded from iCloud or opened in Photos in the last day; an older video you just opened in Photos usually appears here. Below it, **all videos on the iPhone** are listed newest recording first and grouped by month, like the Photos library. The list includes camera-roll videos and iCloud Photos originals downloaded to the iPhone, and it refreshes while open. With Optimize iPhone Storage, copying an older video makes Photos download it, and it appears as soon as the download finishes. Videos that exist only in iCloud appear once Photos has downloaded them.

For **iPhone → PC**, copy the video itself and use Ctrl+C, **Get iPhone clipboard**, or native Copy with sync enabled. Pane extracts inline movies from Notes’ own rich-text archives and gives video data priority over thumbnails. Received clips are saved in `~/Downloads/Pane/Clipboard videos` and placed on the Windows clipboard as file references. Paste into Explorer to copy the file, or into a PC app that accepts video-file paste. Chat apps may require attaching or dragging in the saved file instead of Ctrl+V. Clips remain on disk after Pane exits.

Some apps provide only a device-local file reference or a share link instead of movie bytes. Pane does not follow those references; use the Files workflow for those clips.

#### Transfer limits

| Content | Limit |
| --- | --- |
| Text from PC to iPhone | 1 MiB of UTF-8 text |
| Clipboard image | 32 MiB after PNG conversion; 40 megapixels |
| Direct clipboard video | 32 MiB; one MP4, MOV or M4V at a time |
| Files panel | 1 GiB per file |

The interface labels these binary limits as MB/GB. PNG conversion can make a compressed photo larger than its original file size. Use Files for larger items.

## File sharing over Wi-Fi

1. In Pane’s **Files** panel, click **Start file sharing**.
2. Keep the PC and iPhone on the same trusted Wi-Fi network. Scan the QR code with the iPhone camera and open it in Safari.
3. **Without the camera:** mirroring can make the camera unavailable. Type the short address displayed in the Files panel into Safari, then enter the **8-digit pairing code**. You can do this through the mirrored screen. Failed pairing attempts are limited to five per minute.
4. **PC → iPhone:** drag files into the Files panel or click **Choose files**. In Safari, tap **Save to iPhone**. Find the download in Safari’s Downloads, or use **Share → Save to Files** if a preview opens.
5. **iPhone → PC:** in Safari, choose files, photos or videos. Completed uploads appear in Pane and are saved under `~/Downloads/Pane/Received`.
6. Click **Stop file sharing** to close the network listener and revoke the pairing link/code. Starting again creates new credentials.

Uploads show progress and can be cancelled. Keep Safari open during transfer. Each file can be up to 1 GiB. Mirroring and file sharing work independently.

File sharing uses **HTTP on local port 8767**, without TLS. Use Wi-Fi you trust. If Safari cannot connect, choose the PC’s Wi-Fi address in the panel and allow Python/Pane through Windows Firewall on that network. The listener binds to the PC’s network interfaces only while sharing is enabled; anyone with the current pairing link or code can pair.

Only selected files are shared. The phone-facing listener cannot access mirroring or device controls. Shared copies are kept in `~/Downloads/Pane/Shared`; received files and shared copies remain on disk when sharing stops. The file list belongs to the current Pane process and resets when Pane restarts.

## Requirements

- Windows 10 or Windows 11 (64-bit)
- Google Chrome or Microsoft Edge for mirroring (WebCodecs HEVC support)
- iPhone/iPad running iOS/iPadOS **17.4 or newer**, with Developer Mode enabled
- Apple Devices or iTunes for the USB driver and connection process
- For source use: Python **3.12 recommended**
- For the regression tests: Node.js **22 or newer**

## Run from source

```powershell
git clone https://github.com/heyviet/pane.git
cd pane
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m handset.server
```

Open **http://127.0.0.1:8765** in Chrome or Edge. Keep the server process running. To use the dedicated desktop window instead, run:

```powershell
.\.venv\Scripts\python.exe -m handset.app
```

### Troubleshooting

- **Windows sees the iPhone, but Pane does not:** open Apple Devices and keep it running; reconnect and unlock the phone, then accept Trust. Store versions can use an Apple connection process rather than a service with the traditional name.
- **Developer Mode is missing:** with the trusted phone connected and unlocked, run the following command, then close/reopen Settings → Privacy & Security, scroll to Developer Mode, enable it, restart and confirm Turn On:

  ```powershell
  .\.venv\Scripts\python.exe -m pymobiledevice3 amfi reveal-developer-mode
  ```

  This reveals the switch; it does not enable Developer Mode or restart the phone automatically.
- **Switching from USB to Wi-Fi:** choose Wi-Fi and click Start mirroring. Pane closes the previous stream/tunnel before opening the selected transport. A completed Wi-Fi pairing does not by itself change an active USB stream.
- **Native Copy is not reaching PC:** enable Sync iPhone → PC, or use Get iPhone clipboard. Check the sync status below the toggle. A video returned to PC is a file, not pasted text; use an attachment/file drop if the destination app requires one.
- **After updating frontend files:** refresh Pane with Ctrl+Shift+R. Python server changes require restarting the server; reconnect mirroring and re-enable sharing afterward.
- **Frozen picture:** use Restart screen. Keep the phone unlocked and connected to the chosen transport.

### Tests

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/keys.test.cjs tests/mirror-input.test.cjs
```

The suite covers transport replacement, acknowledged HID dispatch, native clipboard sync, image conversion, video/Notes extraction, Windows file clipboard formats, pairing/revocation, access boundaries, file integrity, size limits, interrupted uploads, keyboard focus and long-press behavior. Mocked device/clipboard tests do not require a connected phone. GitHub Actions runs the suite on Windows with Python 3.12 and Node 22.

`pymobiledevice3` is pinned to the version used for live device validation. The stream/clipboard adapters use some internal library hooks, so upgrades need the regression suite and a physical-device check.

### Build the Windows EXE

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\Activate.ps1
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The output is `dist/Pane.exe`. The packaging recipe includes the web assets, QR generator, Windows clipboard modules and HEIC decoder/native libraries.

## How it works

Pane uses [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) for a secure RemoteXPC/RSD tunnel to the iPhone over USB or Wi-Fi. It starts iOS screen capture, streams HEVC to the browser through a loopback proxy, and sends mouse/keyboard events back as HID reports. Clipboard shortcuts wait for device-side key dispatch instead of treating enqueueing as completed input. The pasteboard bridge handles text, images, and supported inline movies.

Mirroring controls stay on **127.0.0.1:8765**, with the internal video service on **127.0.0.1:8766**. The optional, paired file-transfer listener uses **port 8767** on the local network. No cloud service is involved.

## FAQ

**Can I mirror and control my iPhone for free?**
Yes. Pane is open source under GPL-3.0, with mouse/keyboard control over USB or Wi-Fi.

**Do I need a jailbreak, Mac or Xcode?**
No. You need a supported iPhone, Windows, Apple’s USB software, Trust and Developer Mode.

**Why Developer Mode?**
iOS exposes the required developer display/input services only with Developer Mode enabled. You can turn it off afterward.

**Does enabling Wi-Fi mirroring also enable file sharing?**
No. File sharing has its own Start/Stop control, pairing credentials and Safari companion page. It does not require the mirroring stream to be active.

**Is anything uploaded to a cloud service?**
No. Transfers remain between your PC and iPhone on the local connection/network. LAN file sharing uses HTTP, so choose a trusted network.

**Windows warns about an unsigned EXE.**
The portable executable is not code-signed. You can run the source or build it yourself using the steps above.

## Keywords

iPhone screen mirroring Windows · mirror iPhone to PC · control iPhone from PC · iPhone remote control Windows 11 · iOS screen mirror Windows · scrcpy for iPhone · scrcpy iOS alternative · iPhone to laptop screen share · use iPhone on computer · type on iPhone with PC keyboard · wireless iPhone mirroring Wi-Fi · USB iPhone mirror · free ApowerMirror alternative · open source iPhone mirroring · no jailbreak

## License

Pane is licensed under the [GNU General Public License v3.0](LICENSE).
Not affiliated with or endorsed by Apple Inc. iPhone, iOS and AirPlay are trademarks of Apple Inc.
