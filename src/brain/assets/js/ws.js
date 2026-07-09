// Reconnecting WebSocket to /ws. The server pushes {type:"stats", reason, data}
// whenever the brain's fingerprint changes, plus one on connect. On a drop we
// retry with exponential backoff (1s → 30s) so a cron `brain cycle` restarting
// the vault, or the laptop sleeping, doesn't leave a dead page.

export function connectWS({ onMessage, onStatus }) {
  let ws = null;
  let backoff = 1000;
  let stopped = false;

  function open() {
    onStatus("connecting");
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    // Bind every handler to *this* socket, not the shared `ws`: after a
    // reconnect swaps `ws`, a late event from an older socket must not reach
    // in and close the current healthy one.
    const sock = new WebSocket(`${proto}//${location.host}/ws`);
    ws = sock;
    sock.onopen = () => { backoff = 1000; onStatus("open"); };
    sock.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      onMessage(msg);
    };
    sock.onclose = () => {
      if (sock !== ws) return; // superseded socket closing; ignore
      onStatus("closed");
      if (!stopped) {
        setTimeout(open, backoff);
        backoff = Math.min(backoff * 2, 30000);
      }
    };
    sock.onerror = () => sock.close();
  }

  open();
  return {
    refresh() { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "refresh" })); },
    close() { stopped = true; if (ws) ws.close(); },
  };
}
