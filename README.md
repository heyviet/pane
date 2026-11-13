<p align="center">
  <img src="handset/web/logo.svg" width="120" alt="Pane logo - iPhone screen mirroring for Windows">
</p>

<h1 align="center">Pane</h1>

<p align="center">
  <b>Mirror and control your iPhone from a Windows PC.</b><br>
  Free and open source. USB or Wi‑Fi. No jailbreak, no Mac, no account.
</p>

<p align="center">
  <a href="../../releases/latest"><b>⬇ Download Pane.exe</b></a>
</p>

---

Pane shows your iPhone's screen live on your PC. Click and drag to tap and swipe, type with your keyboard, and copy and paste between the two. Think [scrcpy](https://github.com/Genymobile/scrcpy), but for iPhone. Everything stays on your PC and your network.

## Features

- **Live mirroring** over USB or Wi‑Fi, in a realistic iPhone frame that turns with the phone
- **Full control:** mouse for touch, keyboard for typing, plus Home, Lock, Volume, Siri and gesture buttons
- **Shared clipboard:** Ctrl+C and Ctrl+V for text, images and videos
- **Screenshots** saved straight to your PC
- **File sharing** with the iPhone over Wi‑Fi
- **Zoom, rotate and full screen**
- **One portable `Pane.exe`:** no installer, no cloud, no telemetry

## Setup

1. Install **[Apple Devices](https://apps.microsoft.com/detail/9np83lwlpz9k)** (or iTunes) from the Microsoft Store. It provides the iPhone USB driver.
2. Plug in the iPhone, unlock it and tap **Trust**.
3. On the iPhone, turn on **Settings → Privacy & Security → Developer Mode** (it restarts).
4. Run **Pane.exe** and click **Start mirroring**.

**To go wireless:** while plugged in, choose **Wi‑Fi** and click **Set up Wi‑Fi** once. After that, unplug and mirror over the same Wi‑Fi network.

**Requirements:** Windows 10 or 11 (64‑bit), Chrome or Edge, an iPhone on iOS 17.4 or newer.

## Using Pane

The iPhone sits in the middle of the window. Everything else is in the **sidebar** (the button at the top right): connection, controls, files and device info. Hover over a **?** for help.

| On your PC | On the iPhone |
| --- | --- |
| Click / drag | Tap / swipe |
| Hold the mouse button | Long press |
| Drag up from the bottom edge | Home (pause before letting go for the App Switcher) |
| Right click | Home |
| Type | Typing goes to the iPhone while mirroring |
| Ctrl+C / Ctrl+V | Copy to PC / paste from PC |
| Ctrl+A / Ctrl+Z | Select all / undo |

The bottom‑left toolbar zooms, rotates and goes full screen.

<details>
<summary><b>Clipboard: text, images and videos</b></summary>

- **PC → iPhone:** copy on the PC, click into the iPhone field, press **Ctrl+V**.
- **iPhone → PC:** select on the iPhone and press **Ctrl+C**. To use iOS's own Copy menu, turn on **Sync iPhone → PC**.
- **Apps that ignore Ctrl+V:** click **Send PC clipboard**, then long‑press the field and choose Paste.
- **Videos:** copy one MP4, MOV or M4V file in Explorer and press Ctrl+V on the iPhone. Videos copied on the iPhone land in `Downloads\Pane\Clipboard videos` and on the PC clipboard.
- **Videos copied in Photos:** iOS won't hand these over directly, so Pane opens a picker. Choose the video you copied. **Pick iPhone video…** opens it any time.

| Limit | Size |
| --- | --- |
| Text | 1 MB |
| Image | 32 MB (40 megapixels) |
| Video | 32 MB, one file |

For anything bigger, use file sharing.
</details>

<details>
<summary><b>File sharing over Wi‑Fi</b></summary>

1. In the sidebar's **Files** section, click **Start file sharing**.
2. On the iPhone, scan the QR code, or type the address shown into Safari and enter the pairing code.
3. **To the iPhone:** drop files into Pane, then tap **Save to iPhone** in Safari.
4. **To the PC:** choose files in Safari. They're saved in `Downloads\Pane\Received`.
5. Click **Stop file sharing** when done. That also revokes the pairing.

Up to 1 GB per file. Sharing uses unencrypted HTTP on port 8767 while it's on, so only use it on Wi‑Fi you trust.
</details>

<details>
<summary><b>Troubleshooting</b></summary>

- **iPhone not found:** keep Apple Devices open, unplug and replug the phone, unlock it and tap Trust.
- **No Developer Mode switch:** with the phone plugged in and unlocked, run the command below from source, then reopen Settings → Privacy & Security.
  ```powershell
  .\.venv\Scripts\python.exe -m pymobiledevice3 amfi reveal-developer-mode
  ```
- **Wi‑Fi can't find the phone:** set the Windows network to **Private**, keep the phone unlocked, and check the router doesn't isolate wireless devices.
- **Picture frozen or black:** click **Restart screen**. The refresh icon reconnects the window and re‑checks the connection.
- **Safari can't reach file sharing:** allow Pane through Windows Firewall on your Wi‑Fi network.
- **Windows warns about an unsigned EXE:** it isn't code‑signed. Run from source instead if you prefer.
</details>

<details>
<summary><b>Run from source, test and build</b></summary>

Needs Python 3.12 (and Node.js 22+ for the browser tests).

```powershell
git clone https://github.com/heyviet/pane.git
cd pane
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m handset.app
```

`handset.app` opens Pane in its own window. To use a browser tab instead, run `-m handset.server` and open http://127.0.0.1:8765. After changing Python code, quit and reopen Pane; web page changes only need Ctrl+Shift+R.

**Tests** (no phone needed):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/keys.test.cjs tests/mirror-input.test.cjs
```

**Build `dist\Pane.exe`:**

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\Activate.ps1
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

`pymobiledevice3` is pinned. Pane relies on some of its internals, so test with a real phone before upgrading it.
</details>

<details>
<summary><b>How it works</b></summary>

Pane uses [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) to open a secure tunnel to the iPhone over USB or Wi‑Fi. It starts iOS screen capture, decodes the HEVC video in your browser, and sends mouse and keyboard input back as hardware events. Developer Mode is what makes those iOS services available; you can turn it off afterwards.

Pane only listens on your own PC: the app on `127.0.0.1:8765` and the video on `127.0.0.1:8766`. File sharing, when you turn it on, adds port 8767 on your local network. No cloud service is involved.
</details>

<details>
<summary>Keywords</summary>

iPhone screen mirroring Windows · mirror iPhone to PC · control iPhone from PC · scrcpy for iPhone · type on iPhone with PC keyboard · wireless iPhone mirroring · free ApowerMirror alternative · open source iPhone mirroring · no jailbreak
</details>

## License

[GPL‑3.0](LICENSE). Not affiliated with Apple Inc. iPhone and iOS are trademarks of Apple Inc.
