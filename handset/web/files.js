(() => {
  const root = document.querySelector("[data-files-mode]");
  if (!root) return;
  const desktop = root.dataset.filesMode === "desktop";
  const $ = (id) => root.querySelector(`#files-${id}`);
  let state = null;
  let uploading = false;
  let activeUpload = null;
  let cancelRequested = false;
  let refreshing = false;
  let addressIndex = 0;
  let addressKey = "";
  let filesKey = "";

  function message(text = "", error = false) {
    $("message").textContent = text;
    $("message").classList.toggle("error", error);
  }

  async function api(path = "", options = {}) {
    const response = await fetch(`/api/files${path}`, {cache: "no-store", ...options});
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Cannot connect. Try again.");
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function size(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  }

  function list(id, items, empty) {
    const fragment = document.createDocumentFragment();
    for (const item of items.slice().reverse()) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "files-name";
      name.textContent = item.name;
      const detail = document.createElement("small");
      detail.textContent = `${size(item.size)} · ${item.direction === "to-pc" ? "Saved on PC" : "Ready to download"}`;
      name.append(detail);
      li.append(name);
      if (desktop || item.direction === "to-phone") {
        const link = document.createElement("a");
        link.href = `/api/files/download/${item.id}`;
        link.download = item.name;
        link.textContent = desktop ? "Download" : "Save to iPhone";
        link.setAttribute("aria-label", `${link.textContent}: ${item.name}`);
        li.append(link);
      }
      fragment.append(li);
    }
    if (!items.length) {
      const li = document.createElement("li");
      li.className = "files-empty";
      li.textContent = empty;
      fragment.append(li);
    }
    $(id).replaceChildren(fragment);
  }

  function pairingAddress() {
    const url = state.urls[addressIndex];
    $("link").value = url || "No local network address found.";
    $("manual-address").textContent = url ? url.split("#")[0] : "Connect this PC to Wi-Fi first.";
    $("code").textContent = state.code ? `${state.code.slice(0, 4)} ${state.code.slice(4)}` : "";
    $("qr").hidden = !url;
    if (url) $("qr").src = `/api/files/qr?address=${addressIndex}&session=${encodeURIComponent(url.split("#")[1])}`;
  }

  function render(next) {
    state = next;
    $("content").hidden = !state.enabled;
    if (!desktop) $("pair-form").hidden = true;
    $("connection").textContent = state.enabled ? (desktop ? (state.connected ? "iPhone connected" : "Waiting for iPhone") : "Connected to PC") : "Sharing off";
    $("connection").classList.toggle("connected", state.enabled && (!desktop || state.connected));
    if (desktop) {
      $("toggle").textContent = state.enabled ? "Stop file sharing" : "Start file sharing";
      $("pairing").hidden = !state.enabled;
      $("folder").textContent = `Files from iPhone are saved in ${state.folder}. Files stay on disk after sharing stops.`;
      const key = state.urls.join("|");
      if (key !== addressKey) {
        addressKey = key;
        addressIndex = 0;
        $("address").replaceChildren(...state.urls.map((url, index) => {
          const option = document.createElement("option");
          option.value = index;
          option.textContent = new URL(url).hostname;
          return option;
        }));
        pairingAddress();
      }
    }
    const nextFilesKey = JSON.stringify(state.files);
    if (nextFilesKey !== filesKey) {
      filesKey = nextFilesKey;
      list("outgoing", state.files.filter((f) => f.direction === "to-phone"), desktop ? "Choose files above to make them available on iPhone." : "Files shared from Pane will appear here.");
      list("incoming", state.files.filter((f) => f.direction === "to-pc"), desktop ? "Files sent from Safari will appear here." : "You haven’t sent any files yet.");
    }
  }

  async function refresh() {
    if (refreshing || document.hidden) return;
    refreshing = true;
    try {
      render(await api());
    } catch (error) {
      $("connection").textContent = "Disconnected";
      $("connection").classList.remove("connected");
      message(`${error.message} ${desktop ? "Check that Pane is running." : "Keep sharing enabled on the PC, or scan a new pairing code."}`, true);
      if (!desktop) {
        $("content").hidden = true;
        if (error.status === 403) {
          state = null;
          $("pair-form").hidden = false;
          message("Enter the 8-digit pairing code shown in Pane on your PC.");
        }
      }
    } finally {
      refreshing = false;
    }
  }

  function upload(file) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      activeUpload = xhr;
      xhr.open("POST", "/api/files/upload");
      xhr.timeout = 30 * 60 * 1000;
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          const percent = Math.round(event.loaded / event.total * 100);
          $("progress").value = percent;
          $("progress-label").textContent = percent === 100 ? `Saving ${file.name}…` : `Sending ${file.name} · ${percent}%`;
        }
      };
      xhr.onload = () => {
        let body;
        try { body = JSON.parse(xhr.responseText); } catch { body = {}; }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new Error(body.error || "Upload failed. Choose the file again to retry."));
      };
      xhr.onerror = () => reject(new Error("Connection lost. Keep Safari open and choose the file again to retry."));
      xhr.ontimeout = () => reject(new Error("Upload timed out. Choose the file again to retry."));
      xhr.onabort = () => reject(new Error("Upload cancelled. Completed files are still available."));
      xhr.send(file);
    });
  }

  async function send(files) {
    if (uploading || !files.length) return;
    if (!state?.enabled) { message("Start file sharing before choosing files.", true); return; }
    uploading = true;
    cancelRequested = false;
    $("choose").disabled = true;
    $("progress-wrap").hidden = false;
    message();
    let completed = 0;
    try {
      for (const file of files) {
        if (cancelRequested) throw new Error("Upload cancelled. Completed files are still available.");
        if (file.size > state.maxSize) throw new Error(`${file.name} exceeds the 1 GB limit. Choose a smaller file.`);
        $("progress").value = 0;
        $("progress-label").textContent = `Sending ${file.name}…`;
        await upload(file);
        completed++;
        await refresh();
      }
      message(desktop ? `${completed} file(s) ready. Open Safari on your iPhone and tap Save to iPhone.` : `${completed} file(s) saved on your PC.`);
    } catch (error) {
      message(error.message, true);
    } finally {
      activeUpload = null;
      uploading = false;
      $("choose").disabled = false;
      $("input").value = "";
      $("progress-wrap").hidden = true;
      await refresh();
    }
  }

  $("choose").addEventListener("click", () => $("input").click());
  $("input").addEventListener("change", (event) => send([...event.target.files]));
  $("cancel").addEventListener("click", () => { cancelRequested = true; activeUpload?.abort(); });
  $("drop").addEventListener("dragover", (event) => { event.preventDefault(); $("drop").classList.add("dragging"); });
  $("drop").addEventListener("dragleave", () => $("drop").classList.remove("dragging"));
  $("drop").addEventListener("drop", (event) => {
    event.preventDefault();
    $("drop").classList.remove("dragging");
    send([...event.dataTransfer.files]);
  });

  if (desktop) {
    $("toggle").addEventListener("click", async () => {
      $("toggle").disabled = true;
      try {
        cancelRequested = true;
        activeUpload?.abort();
        render(await api(state?.enabled ? "/stop" : "/start", {method: "POST"}));
        message(state.enabled ? "Open the address in Safari and enter the pairing code, or scan the QR code." : "Sharing stopped. The previous pairing link no longer works.");
      } catch (error) { message(error.message, true); }
      finally { $("toggle").disabled = false; }
    });
    $("address").addEventListener("change", () => { addressIndex = Number($("address").value); pairingAddress(); });
    $("copy").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText($("link").value); message("Pairing link copied."); }
      catch { $("link").select(); message("Select and copy the link above."); }
    });
  }

  if (!desktop) {
    $("pair-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      $("pair-submit").disabled = true;
      try {
        await api("/pair", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({code: $("pair-code").value})});
        $("pair-code").value = "";
        message("Connected. You can now send and receive files.");
        await refresh();
      } catch (error) { message(error.message, true); }
      finally { $("pair-submit").disabled = false; }
    });
  }

  async function init() {
    if (!desktop && location.hash.length > 1) {
      const token = location.hash.slice(1);
      history.replaceState(null, "", location.pathname);
      try {
        await api("/pair", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token})});
        message("Connected. You can now send and receive files.");
      } catch (error) { message(error.message, true); }
    }
    await refresh();
    setInterval(() => { if (desktop || state?.enabled) refresh(); }, 4000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  }
  init();
})();
