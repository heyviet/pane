// Ordinary keys use HID reports; clipboard shortcuts run on the Windows host.
(function () {
  const CODE_TO_HID = (() => {
    const m = {};
    for (let i = 0; i < 26; i++) m[`Key${String.fromCharCode(65 + i)}`] = 0x04 + i;
    for (let i = 1; i <= 9; i++) m[`Digit${i}`] = 0x1d + i;
    m.Digit0 = 0x27;
    Object.assign(m, {
      Enter: 0x28, NumpadEnter: 0x28, Escape: 0x29, Backspace: 0x2a, Tab: 0x2b, Space: 0x2c,
      Minus: 0x2d, Equal: 0x2e, BracketLeft: 0x2f, BracketRight: 0x30, Backslash: 0x31,
      Semicolon: 0x33, Quote: 0x34, Backquote: 0x35, Comma: 0x36, Period: 0x37, Slash: 0x38,
      IntlBackslash: 0x64, CapsLock: 0x39, Delete: 0x4c, Home: 0x4a, End: 0x4d,
      PageUp: 0x4b, PageDown: 0x4e, ArrowRight: 0x4f, ArrowLeft: 0x50, ArrowDown: 0x51, ArrowUp: 0x52,
      ShiftLeft: 0xe1, ShiftRight: 0xe5, AltLeft: 0xe2, AltRight: 0xe6,
    });
    return m;
  })();
  const ACTIONS = {KeyA: 'select-all', KeyC: 'copy', KeyV: 'paste', KeyX: 'cut', KeyZ: 'undo'};
  const isTextField = (el) => !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON|A|SUMMARY)$/.test(el.tagName));
  function attach({post, enabled, onError = () => {}, onStatus = () => {}}) {
    const pressed = new Set();
    let chain = Promise.resolve();
    const flush = () => {
      const snapshot = [...pressed];
      chain = chain.then(() => post({usages: snapshot})).catch(onError);
    };
    const releaseAll = () => {
      if (!pressed.size) return;
      pressed.clear();
      flush();
    };
    const action = (name) => {
      releaseAll();
      chain = chain.then(async () => {
        onStatus('Clipboard: working…');
        const response = await fetch('/api/clipboard/action', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action: name}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Clipboard action failed. Try again.');
        onStatus(result.message);
        if (result.pickVideo) {
          // Photos video copies are chosen in Pane's picker (the mirror runs in a frame).
          window.dispatchEvent(new CustomEvent('pane-pick-video'));
          if (window.parent !== window) window.parent.postMessage({type: 'pane-pick-video'}, location.origin);
        }
      }).catch(onError);
      return chain;
    };
    window.addEventListener('keydown', (e) => {
      if (!enabled() || isTextField(e.target)) return;
      if (e.ctrlKey || e.metaKey) {
        const command = !e.altKey && ACTIONS[e.code];
        if (!command) return;
        e.preventDefault();
        e.stopPropagation();
        if (!e.repeat) action(command);
        return;
      }
      const usage = CODE_TO_HID[e.code];
      if (usage === undefined) return;
      e.preventDefault();
      e.stopPropagation();
      if (e.repeat) return;
      pressed.add(usage);
      flush();
    }, true);
    window.addEventListener('paste', (e) => {
      if (!enabled() || isTextField(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
      action('paste');
    }, true);
    window.addEventListener('keyup', (e) => {
      if (pressed.delete(CODE_TO_HID[e.code])) {
        e.preventDefault();
        flush();
      }
    }, true);
    window.addEventListener('blur', releaseAll);
    window.addEventListener('focus', releaseAll);
    document.addEventListener('focusout', releaseAll);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') releaseAll();
    });
    return {releaseAll, action};
  }
  window.HandsetKeys = {attach};
})();
