// sidepanel.js — UI logic for the chat agent side panel.
//
// This script runs in the side panel's own page context (not in a tab, not in
// the background worker).  It can use:
//   - chrome.storage.local  — to persist the session_id
//   - chrome.runtime.sendMessage — to talk to background.js
//   - fetch()               — to call the agent server directly
//
// The agent server URL is hardcoded to the default port.  If you change
// settings.port in config.py, update AGENT_URL here too.

const AGENT_URL = "http://localhost:8084";

// ---------------------------------------------------------------------------
// Session ID — persisted in chrome.storage.local so the conversation survives
// browser restarts.  Generated once as a random UUID on first use.
// ---------------------------------------------------------------------------
let sessionId = null;

async function getOrCreateSessionId() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["sessionId"], (result) => {
      if (result.sessionId) {
        resolve(result.sessionId);
      } else {
        // crypto.randomUUID() is available in extension contexts (Chrome 92+).
        const id = crypto.randomUUID();
        chrome.storage.local.set({ sessionId: id });
        resolve(id);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------
const transcript = document.getElementById("transcript");
const statusEl   = document.getElementById("status");
const inputEl    = document.getElementById("input");
const sendBtn    = document.getElementById("send-btn");
const usePageBtn = document.getElementById("use-page-btn");

function setStatus(text) {
  statusEl.textContent = text;
}

function appendBubble(role, text) {
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  div.textContent = text;
  transcript.appendChild(div);
  // Scroll to the bottom so the latest message is always visible.
  transcript.scrollTop = transcript.scrollHeight;
}

function setBusy(busy) {
  sendBtn.disabled   = busy;
  usePageBtn.disabled = busy;
  inputEl.disabled   = busy;
}

// ---------------------------------------------------------------------------
// Chat — send a message to the agent and display the reply
// ---------------------------------------------------------------------------
async function sendMessage() {
  const message = inputEl.value.trim();
  if (!message) return;

  inputEl.value = "";
  appendBubble("user", message);
  setBusy(true);
  setStatus("Thinking…");

  try {
    const response = await fetch(`${AGENT_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
    });

    if (!response.ok) {
      throw new Error(`Server returned ${response.status}`);
    }

    const data = await response.json();
    appendBubble("assistant", data.reply);
    setStatus("");
  } catch (err) {
    appendBubble("assistant", `⚠ Error: ${err.message}`);
    setStatus("Make sure the chat-agent server is running on port 8084.");
  } finally {
    setBusy(false);
    inputEl.focus();
  }
}

// ---------------------------------------------------------------------------
// Use current page — extract the tab's text, ingest it, confirm to the user
// ---------------------------------------------------------------------------
async function useCurrentPage() {
  setBusy(true);
  setStatus("Reading page…");

  // Ask background.js to ask content.js for the page text.
  // background.js is the only context that can talk to a specific tab.
  let pageData;
  try {
    pageData = await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: "GET_PAGE_TEXT" }, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else if (response && response.error) {
          reject(new Error(response.error));
        } else {
          resolve(response);
        }
      });
    });
  } catch (err) {
    setStatus(`⚠ Could not read page: ${err.message}`);
    setBusy(false);
    return;
  }

  setStatus(`Ingesting "${pageData.title}"…`);

  try {
    const response = await fetch(`${AGENT_URL}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: pageData.url, text: pageData.text }),
    });

    if (!response.ok) {
      throw new Error(`Server returned ${response.status}`);
    }

    const data = await response.json();
    setStatus(`✓ Page ready (${data.chunks} chunks) — ask me anything about it`);
  } catch (err) {
    setStatus(`⚠ Ingest failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
sendBtn.addEventListener("click", sendMessage);

// Also send on Enter key (Shift+Enter inserts a newline — not applicable here
// since the input is a single-line <input>, but good habit to document).
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

usePageBtn.addEventListener("click", useCurrentPage);

// ---------------------------------------------------------------------------
// Initialise — run once when the side panel loads
// ---------------------------------------------------------------------------
(async () => {
  sessionId = await getOrCreateSessionId();
  inputEl.focus();
})();
