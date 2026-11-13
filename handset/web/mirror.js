const MIRROR = "/mirror";
const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");

let visualRotation = 0;
// How the picture fits its frame: "cover" fills the phone screen (which Pane
// shapes to the picture), "contain" shows all of it (full screen). viewZoom
// scales it further; Pane only zooms here in full screen.
let fitMode = "cover";
let viewZoom = 1;
// Pixel size of the streamed display ({width, height} from the server), or
// null. The encoder pads frames up to a multiple of 16 without signalling it
// (a 1206-wide iPhone arrives 1216 wide, with a black strip on the right), so
// only this content rectangle is drawn.
let displaySize = null;
// Clockwise degrees that turn the display's frames upright, from the server.
let displayRotation = null;

let pendingFrame = null;
let rafScheduled = false;
let lastFrame = null;
let lastFrameLandscape = null;
let frameCount = 0;
let runGeneration = 0;
let abortStream = null;

function setStatus(text, tone, category = "stream") {
  if (window.parent !== window) {
    window.parent.postMessage({type: "pane-toast", text, tone, category, dismiss: tone === "ok"}, location.origin);
  } else if (tone === "ok") {
    window.PaneToast.clear(category);
  } else {
    window.PaneToast.show(text, tone, category);
  }
}

// Tell the Pane window how the picture is shaped, so the iPhone frame turns
// sideways with it.
let lastView = "";
let pictureSized = false;
function postView() {
  if (!window.parent || window.parent === window) return;
  // Before the first frame the canvas has its default 300x150 size: no shape yet.
  const landscape = pictureSized ? canvas.width > canvas.height : undefined;
  const turn = ((visualRotation % 360) + 360) % 360 === 90 ? "cw" : "ccw";
  // Short/long side ratio: Pane shapes the phone's screen to match it exactly.
  const ratio = pictureSized ? Math.min(canvas.width, canvas.height) / Math.max(canvas.width, canvas.height) : undefined;
  const key = `${landscape}|${turn}|${ratio}`;
  if (key === lastView) return;
  lastView = key;
  window.parent.postMessage({ type: "pane-view", landscape, turn, ratio }, location.origin);
}

function fitCanvas() {
  if (!canvas.width || !canvas.height) return;
  const parent = canvas.parentElement;
  if (!parent) return;
  const rect = parent.getBoundingClientRect();
  const naturalW = canvas.width;
  const naturalH = canvas.height;
  // Cover fills the phone screen edge to edge (it is shaped to the picture,
  // so at most a pixel of rounding is trimmed); contain shows all of it.
  const fit = fitMode === "contain" ? Math.min : Math.max;
  const scale = fit(rect.width / naturalW, rect.height / naturalH) * viewZoom;
  canvas.style.width = `${naturalW * scale}px`;
  canvas.style.height = `${naturalH * scale}px`;
  postView();
}

window.addEventListener("resize", fitCanvas);

function touchCoords(e) {
  const rect = canvas.getBoundingClientRect();
  const sx = ((e.clientX - rect.left) * canvas.width) / rect.width;
  const sy = ((e.clientY - rect.top) * canvas.height) / rect.height;
  const cw = canvas.width;
  const ch = canvas.height;
  const sideways = Math.abs(visualRotation % 180) === 90;
  const dw = sideways ? ch : cw;
  const dh = sideways ? cw : ch;
  const dx = sx - cw / 2;
  const dy = sy - ch / 2;
  const rad = (-visualRotation * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const bx = dx * cos - dy * sin + dw / 2;
  const by = dx * sin + dy * cos + dh / 2;
  return {
    x: Math.max(0, Math.min(65535, Math.round((bx / dw) * 65535))),
    y: Math.max(0, Math.min(65535, Math.round((by / dh) * 65535))),
  };
}

async function postJson(path, payload) {
  try {
    await fetch(`${MIRROR}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (_) {
    /* stream may be down */
  }
}

let activePointer = null;
let lastCoords = { x: 0, y: 0 };

// The proxy opens one connection per request, so concurrent fetches can land
// out of order and break drags. Send touches strictly in sequence, collapsing
// queued moves into the newest one.
const touchQueue = [];
let touchBusy = false;

function sendTouch(type, coords, isMove = false) {
  const last = touchQueue[touchQueue.length - 1];
  const event = { type, x: coords.x, y: coords.y, isMove };
  if (isMove && last && last.isMove) touchQueue[touchQueue.length - 1] = event;
  else touchQueue.push(event);
  pumpTouches();
}

async function pumpTouches() {
  if (touchBusy) return;
  touchBusy = true;
  while (touchQueue.length) {
    const { type, x, y } = touchQueue.shift();
    await postJson("/touch", { type, x, y });
  }
  touchBusy = false;
}

// iOS system gestures (Home bar, Control Center, Notification Center) only
// trigger when the contact begins on the screen edge itself.
const EDGE = 2500;
function snapToEdge(c) {
  return {
    x: c.x < EDGE ? 0 : c.x > 65535 - EDGE ? 65535 : c.x,
    y: c.y < EDGE ? 0 : c.y > 65535 - EDGE ? 65535 : c.y,
  };
}

// Injected touches arrive too slowly for iOS to read a bottom-edge flick as
// the Home gesture, so a drag that starts on the bottom edge is kept local and
// turned into the Home button (or a double press for the app switcher when
// the finger pauses before lifting).
const HOME_SWIPE_MIN = 5000;
const SWITCHER_HOLD_MS = 450;
let edgeSwipe = null;

async function pressHome(times = 1) {
  for (let i = 0; i < times; i++) {
    if (i) await new Promise((r) => setTimeout(r, 120));
    await postJson("/button", { name: "home", state: "press" });
  }
}

function releaseActive() {
  if (activePointer === null) return;
  activePointer = null;
  if (edgeSwipe) {
    const swipe = edgeSwipe;
    edgeSwipe = null;
    if (swipe.armed) {
      const paused = performance.now() - swipe.lastMoveAt > SWITCHER_HOLD_MS;
      pressHome(paused ? 2 : 1);
    } else {
      sendTouch("contact", swipe.start);
      sendTouch("release", swipe.start);
    }
    return;
  }
  sendTouch("release", lastCoords);
}

// Listen on the whole document so a drag can start on the black margin just
// below the picture; coordinates clamp onto the screen edge.
document.addEventListener("pointerdown", (e) => {
  if (e.target.closest?.("#pane-toast")) return;
  if (e.button !== 0) return;
  e.preventDefault();
  canvas.focus({ preventScroll: true });
  try {
    document.body.setPointerCapture(e.pointerId);
  } catch (_) {}
  activePointer = e.pointerId;
  lastCoords = snapToEdge(touchCoords(e));
  if (lastCoords.y === 65535) {
    edgeSwipe = { start: lastCoords, armed: false, lastMoveAt: performance.now(), lastY: lastCoords.y };
    return;
  }
  sendTouch("contact", lastCoords);
});

function onMove(e) {
  if (e.pointerId !== activePointer) return;
  e.preventDefault();
  lastCoords = touchCoords(e);
  if (edgeSwipe) {
    if (edgeSwipe.start.y - lastCoords.y > HOME_SWIPE_MIN) edgeSwipe.armed = true;
    if (Math.abs(lastCoords.y - edgeSwipe.lastY) > 600) {
      edgeSwipe.lastMoveAt = performance.now();
      edgeSwipe.lastY = lastCoords.y;
    }
    return;
  }
  sendTouch("contact", lastCoords, true);
}

function onUp(e) {
  if (e.pointerId !== activePointer) return;
  e.preventDefault();
  lastCoords = touchCoords(e);
  releaseActive();
}

window.addEventListener("pointermove", onMove);
window.addEventListener("pointerup", onUp);
window.addEventListener("pointercancel", onUp);
document.body.addEventListener("lostpointercapture", (e) => {
  if (e.pointerId === activePointer) releaseActive();
});
window.addEventListener("blur", releaseActive);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") releaseActive();
});

document.addEventListener("contextmenu", (e) => {
  if (e.target.closest?.("#pane-toast")) return;
  e.preventDefault();
  // A browser can raise contextmenu during an active long press. Keep that
  // finger down for UIKit; Home is only the deliberate mouse right-click.
  if (activePointer === null && e.pointerType === "mouse" && e.button === 2) pressHome();
});

window.HandsetKeys.attach({
  post: (payload) => postJson("/key", payload),
  onError: (error) => setStatus(error.message, "warn", "clipboard"),
  onStatus: (message) => setStatus(message, "", "clipboard"),
  enabled: () => {
    try {
      return window.parent.handsetKeyboardOn !== false;
    } catch (_) {
      return true;
    }
  },
});

window.addEventListener("message", (e) => {
  if (e.origin !== window.location.origin) return;
  const d = e.data;
  if (!d || d.source !== "handset") return;
  if (d.cmd === "rotate") {
    visualRotation = (visualRotation + (d.deg || 0)) % 360;
    if (lastFrame) drawFrame(lastFrame);
    fitCanvas();
  } else if (d.cmd === "fit") {
    fitMode = d.mode === "contain" ? "contain" : "cover";
    viewZoom = typeof d.zoom === "number" && d.zoom > 0 ? d.zoom : 1;
    document.body.classList.toggle("zoomed", viewZoom > 1);
    fitCanvas();
  } else if (d.cmd === "reload") {
    beginRun();
  }
});

async function fetchCodecWithRetry() {
  const delays = [200, 500, 1000, 2000, 3000, 4000];
  let lastErr = "";
  for (let attempt = 0; attempt <= delays.length; attempt++) {
    if (attempt > 0) await new Promise((r) => setTimeout(r, delays[attempt - 1]));
    try {
      const resp = await fetch(`${MIRROR}/codec`, { cache: "no-store" });
      if (!resp.ok) {
        lastErr = `HTTP ${resp.status}`;
        continue;
      }
      const info = await resp.json();
      if (!info.codec || !info.description) {
        lastErr = "waiting for video parameters";
        continue;
      }
      const description = Uint8Array.from(atob(info.description), (c) => c.charCodeAt(0));
      return { codec: info.codec.trim(), description, display: info.display };
    } catch (err) {
      lastErr = err.message || String(err);
    }
  }
  throw new Error(lastErr || "codec unreachable");
}

function setDisplay(display) {
  displaySize = display && display.width > 0 && display.height > 0 ? display : null;
  displayRotation = display && typeof display.rotation === "number" ? display.rotation : null;
}

// The part of a frame that holds the display, without the encoder's padding.
// The display size is in its native orientation; a frame the iPhone rendered
// sideways has it the other way round.
function contentSize(fw, fh) {
  if (!displaySize) return [fw, fh];
  let { width, height } = displaySize;
  if ((fw > fh) !== (width > height)) [width, height] = [height, width];
  const scale = Math.min(1, fw / width);
  return [Math.min(fw, Math.round(width * scale)), Math.min(fh, Math.round(height * scale))];
}

// Turn the picture upright when the server says the display is rotated but
// the frames still come in the display's native orientation. An iPhone that
// re-renders in landscape already sends upright frames (no turn needed).
function applyDisplayRotation(f) {
  if (displayRotation === null || !displaySize) return;
  const frameLandscape = f.displayWidth > f.displayHeight;
  const nativeLandscape = displaySize.width > displaySize.height;
  visualRotation = frameLandscape === nativeLandscape ? displayRotation : 0;
}

function drawFrame(f) {
  const sideways = Math.abs(visualRotation % 180) === 90;
  const [fw, fh] = contentSize(f.displayWidth, f.displayHeight);
  const targetW = sideways ? fh : fw;
  const targetH = sideways ? fw : fh;
  if (targetW !== canvas.width || targetH !== canvas.height || !pictureSized) {
    canvas.width = targetW;
    canvas.height = targetH;
    pictureSized = true;
    fitCanvas();
  }
  const dstW = canvas.width;
  const dstH = canvas.height;
  const fpW = sideways ? dstH : dstW;
  const fpH = sideways ? dstW : dstH;
  ctx.save();
  ctx.translate(dstW / 2, dstH / 2);
  if (visualRotation) ctx.rotate((visualRotation * Math.PI) / 180);
  ctx.drawImage(f, 0, 0, fw, fh, -fpW / 2, -fpH / 2, fpW, fpH);
  ctx.restore();
}

function drawPending() {
  rafScheduled = false;
  const f = pendingFrame;
  pendingFrame = null;
  if (!f) return;
  const landscape = f.displayWidth > f.displayHeight;
  if (landscape !== lastFrameLandscape) {
    // The iPhone turned (or this is the first frame): drop a manual rotation
    // and follow what the server says about the display.
    if (lastFrameLandscape !== null) visualRotation = 0;
    applyDisplayRotation(f);
  }
  lastFrameLandscape = landscape;
  try {
    drawFrame(f);
    if (frameCount === 1) setStatus("Live", "ok");
  } catch (_) {}
  if (lastFrame && lastFrame !== f) {
    try {
      lastFrame.close();
    } catch (_) {}
  }
  lastFrame = f;
}

async function run(generation) {
  setStatus("Connecting to stream…");
  frameCount = 0;
  const startedAt = performance.now();
  const watchdog = window.setInterval(() => {
    if (generation !== runGeneration) {
      window.clearInterval(watchdog);
      return;
    }
    if (frameCount > 0) {
      window.clearInterval(watchdog);
      return;
    }
    if (performance.now() - startedAt > 12000) {
      window.clearInterval(watchdog);
      setStatus("No picture yet — press Restart screen on the right", "warn");
    }
  }, 1000);

  const { codec, description, display } = await fetchCodecWithRetry();
  if (generation !== runGeneration) return;
  setDisplay(display);

  let decoderConfig = { codec, description, optimizeForLatency: true };
  const support = await VideoDecoder.isConfigSupported({ codec, description });
  if (!support.supported) {
    setStatus("This browser cannot decode the iPhone video stream.", "warn");
    return;
  }

  let needsResync = false;
  let autoRestartUsed = false;

  const buildDecoder = () =>
    new VideoDecoder({
      output: (frame) => {
        if (pendingFrame) {
          try {
            pendingFrame.close();
          } catch (_) {}
        }
        pendingFrame = frame;
        if (!rafScheduled) {
          rafScheduled = true;
          requestAnimationFrame(drawPending);
        }
        frameCount += 1;
      },
      error: () => {
        needsResync = true;
        if (!autoRestartUsed && frameCount < 5) {
          autoRestartUsed = true;
          fetch(`${MIRROR}/restart`, { method: "POST", cache: "no-store" }).catch(() => {});
        } else {
          fetch(`${MIRROR}/pli`, { method: "POST", cache: "no-store" }).catch(() => {});
        }
      },
    });

  let decoder = buildDecoder();
  decoder.configure(decoderConfig);
  setStatus("Waiting for video…");

  let resp = null;
  const streamDelays = [300, 600, 1200, 2000, 3000, 5000, 8000];
  for (let a = 0; a <= streamDelays.length; a++) {
    if (generation !== runGeneration) return;
    if (a > 0) {
      setStatus(`Waiting for video stream (retry ${a})…`);
      await new Promise((r) => setTimeout(r, streamDelays[a - 1]));
      if (a === 2) {
        await fetch(`${MIRROR}/pli`, { method: "POST", cache: "no-store" }).catch(() => {});
      }
    }
    try {
      const r = await fetch(`${MIRROR}/stream.bin`, { cache: "no-store" });
      if (r.ok) {
        resp = r;
        break;
      }
      if (r.status === 503) {
        setStatus("Stream starting on the iPhone…");
        continue;
      }
      setStatus(`Stream error (HTTP ${r.status})`, "warn");
    } catch (err) {
      setStatus(err.message || "Stream unreachable", "warn");
    }
  }
  if (!resp || generation !== runGeneration) {
    setStatus("Cannot reach the video stream. Press Restart screen.", "warn");
    return;
  }

  const reader = resp.body.getReader();
  abortStream = () => reader.cancel().catch(() => {});
  let buf = new Uint8Array(0);
  let timestamp = 0;
  let gotKey = false;

  while (generation === runGeneration) {
    const { value, done } = await reader.read();
    if (done) break;
    const merged = new Uint8Array(buf.length + value.length);
    merged.set(buf);
    merged.set(value, buf.length);
    buf = merged;
    while (buf.length >= 4) {
      const len = (buf[0] << 24) | (buf[1] << 16) | (buf[2] << 8) | buf[3];
      if (buf.length < 4 + len) break;
      const type = buf[4];
      const data = buf.slice(5, 4 + len);
      buf = buf.subarray(4 + len);

      // 3 = new decoder config (JSON, as /codec): the stream changed, and the
      // next key frame has new parameters. 4 = the display turned: {rotation}.
      // Both are messages, not video: never hand them to the decoder.
      if (type === 4) {
        try {
          displayRotation = JSON.parse(new TextDecoder().decode(data)).rotation;
          if (lastFrame) {
            applyDisplayRotation(lastFrame);
            drawFrame(lastFrame);
            fitCanvas();
          }
        } catch (_) {}
        continue;
      }
      if (type === 3) {
        try {
          const info = JSON.parse(new TextDecoder().decode(data));
          decoderConfig = {
            codec: info.codec.trim(),
            description: Uint8Array.from(atob(info.description), (c) => c.charCodeAt(0)),
            optimizeForLatency: true,
          };
          setDisplay(info.display);
          lastFrameLandscape = null;
          try {
            decoder.close();
          } catch (_) {}
          decoder = buildDecoder();
          decoder.configure(decoderConfig);
          gotKey = false;
          needsResync = false;
        } catch (_) {}
        continue;
      }

      if (type === 2) {
        try {
          decoder.close();
        } catch (_) {}
        decoder = buildDecoder();
        decoder.configure(decoderConfig);
        needsResync = false;
        gotKey = true;
      } else if (type === 0) {
        gotKey = true;
        if (needsResync) {
          try {
            decoder.close();
          } catch (_) {}
          decoder = buildDecoder();
          decoder.configure(decoderConfig);
          needsResync = false;
        }
      }
      if (!gotKey || needsResync) continue;
      if (decoder.state !== "configured") {
        try {
          decoder.close();
        } catch (_) {}
        decoder = buildDecoder();
        decoder.configure(decoderConfig);
        needsResync = true;
        continue;
      }
      try {
        decoder.decode(
          new EncodedVideoChunk({
            type: type === 0 || type === 2 ? "key" : "delta",
            timestamp,
            data,
          }),
        );
        timestamp += 16666;
      } catch (_) {
        needsResync = true;
      }
    }
  }
  if (generation === runGeneration) {
    setStatus("Stream ended — press Restart screen", "warn");
  }
}

function beginRun() {
  runGeneration += 1;
  const generation = runGeneration;
  if (abortStream) {
    abortStream();
    abortStream = null;
  }
  if (pendingFrame) {
    try {
      pendingFrame.close();
    } catch (_) {}
    pendingFrame = null;
  }
  if (lastFrame) {
    try {
      lastFrame.close();
    } catch (_) {}
    lastFrame = null;
  }
  frameCount = 0;
  lastView = "";
  postView();
  run(generation).catch((err) => {
    setStatus(err.message || "Mirror failed", "warn");
  });
}

beginRun();
