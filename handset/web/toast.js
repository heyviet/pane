(() => {
  let toast;
  let timer;
  let currentCategory;
  function element() {
    if (toast) return toast;
    toast = document.createElement("div");
    toast.id = "pane-toast";
    toast.hidden = true;
    const text = document.createElement("span");
    text.id = "pane-toast-text";
    text.setAttribute("role", "status");
    text.setAttribute("aria-live", "polite");
    text.setAttribute("aria-atomic", "true");
    const close = document.createElement("button");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss notification");
    close.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="m4 4 8 8m0-8-8 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
    close.addEventListener("click", () => clear());
    toast.append(text, close);
    (document.fullscreenElement || document.body).append(toast);
    return toast;
  }
  function clear(category) {
    if (category && category !== currentCategory) return;
    clearTimeout(timer);
    if (toast) toast.hidden = true;
  }
  function show(text, tone = "", category = "general") {
    if (typeof text !== "string" || !text) return;
    clearTimeout(timer);
    currentCategory = category;
    const notification = element();
    notification.querySelector("span").textContent = text;
    notification.dataset.tone = tone;
    notification.hidden = false;
    // Errors and in-progress operations stay until dismissed or superseded.
    if (tone !== "warn" && !text.endsWith("…")) timer = setTimeout(() => clear(category), 6000);
  }
  document.addEventListener("fullscreenchange", () => {
    if (toast) (document.fullscreenElement || document.body).append(toast);
  });
  window.addEventListener("message", (event) => {
    const frame = document.getElementById("screen-view");
    if (event.origin !== location.origin || !frame || event.source !== frame.contentWindow) return;
    const data = event.data;
    if (!data || data.type !== "pane-toast") return;
    if (data.dismiss) clear(data.category);
    else show(data.text, data.tone, data.category);
  });
  window.PaneToast = {show, clear};
})();
